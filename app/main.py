import asyncio
import json
import os
import secrets
import sqlite3
import time
from contextlib import asynccontextmanager, suppress
from datetime import datetime, timezone
from pathlib import Path

import httpx
import psutil
from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

DB = os.getenv('DATABASE_PATH', 'data/infrawatch.db')
INTERVAL = max(5, int(os.getenv('CHECK_INTERVAL', '15')))
TOKEN = os.getenv('ADMIN_TOKEN', '')
DEMO = os.getenv('DEMO_ENABLED', 'true').lower() == 'true'
BASE = os.getenv('SELF_URL', 'http://127.0.0.1:' + os.getenv('PORT', '8000'))
TARGETS = json.loads(os.getenv('MONITOR_TARGETS', '[]'))
if not TARGETS:
    TARGETS = [dict(id='gateway', name='API Gateway', url=BASE+'/health', kind='live'),
               dict(id='inventory', name='Inventory API', url=BASE+'/demo/health', kind='demo')]
if len({t['id'] for t in TARGETS}) != len(TARGETS):
    raise ValueError('Monitor target IDs must be unique')
states = {}
demo_failed = False
lock = asyncio.Lock()
process = psutil.Process()


def now():
    return datetime.now(timezone.utc).isoformat()


def db():
    Path(DB).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB, timeout=10)
    conn.row_factory = sqlite3.Row
    return conn


def initialize():
    with db() as c:
        c.executescript('''CREATE TABLE IF NOT EXISTS checks(id INTEGER PRIMARY KEY, service TEXT, at TEXT, status TEXT, latency REAL, error TEXT);
        CREATE TABLE IF NOT EXISTS events(id INTEGER PRIMARY KEY, service TEXT, at TEXT, type TEXT, message TEXT);
        CREATE TABLE IF NOT EXISTS metrics(id INTEGER PRIMARY KEY, at TEXT, cpu REAL, memory REAL);''')
        c.execute('INSERT INTO events(service,at,type,message) VALUES(?,?,?,?)', ('Willow', now(), 'startup', 'Monitor process started; no deployment integration configured.'))


def classify(code, latency):
    if not 200 <= code < 300:
        return 'unavailable'
    return 'degraded' if latency > 1000 else 'healthy'


async def probe(client, target):
    start = time.perf_counter()
    error = None
    try:
        response = await client.get(target['url'])
        latency = round((time.perf_counter()-start)*1000, 1)
        status = classify(response.status_code, latency)
        if status == 'unavailable':
            error = f'HTTP {response.status_code}. Check application logs, dependency health and routing.'
        elif status == 'degraded':
            error = 'Response exceeded 1000 ms. Check load, database latency and network paths.'
    except httpx.HTTPError as exc:
        latency = round((time.perf_counter()-start)*1000, 1)
        status, error = 'unavailable', f'{type(exc).__name__}. Check DNS, port, TLS and whether the service is running.'
    return dict(**target, status=status, latency=latency, error=error, checked_at=now())


async def check_all():
    async with lock:
        async with httpx.AsyncClient(timeout=4, follow_redirects=False, trust_env=False) as client:
            results = await asyncio.gather(*(probe(client, t) for t in TARGETS))
        with db() as c:
            for result in results:
                old = states.get(result['id'], {}).get('status', 'pending')
                states[result['id']] = result
                c.execute('INSERT INTO checks(service,at,status,latency,error) VALUES(?,?,?,?,?)',
                          (result['id'], result['checked_at'], result['status'], result['latency'], result['error']))
                if old != result['status'] and (old != 'pending' or result['status'] != 'healthy'):
                    kind = 'recovery' if result['status'] == 'healthy' else 'incident'
                    c.execute('INSERT INTO events(service,at,type,message) VALUES(?,?,?,?)',
                              (result['name'], now(), kind, f"{old} → {result['status']}. " + (result['error'] or 'Health check passed.')))
            c.execute('INSERT INTO metrics(at,cpu,memory) VALUES(?,?,?)',
                      (now(), process.cpu_percent()/max(1, psutil.cpu_count()), process.memory_info().rss/1024/1024))
            for table in ('checks', 'events', 'metrics'):
                c.execute(f'DELETE FROM {table} WHERE id NOT IN (SELECT id FROM {table} ORDER BY id DESC LIMIT 2000)')
        return results


async def monitor():
    await asyncio.sleep(1)
    while True:
        try:
            await check_all()
        except Exception:
            import logging
            logging.exception('Monitoring cycle failed')
        await asyncio.sleep(INTERVAL)


@asynccontextmanager
async def lifespan(app):
    initialize()
    process.cpu_percent()
    task = asyncio.create_task(monitor())
    yield
    task.cancel()
    with suppress(asyncio.CancelledError):
        await task


app = FastAPI(title='Willow', lifespan=lifespan)
app.mount('/static', StaticFiles(directory=Path(__file__).parent/'static'), name='static')


def authorize(authorization):
    if not TOKEN:
        raise HTTPException(403, 'Controls disabled. Set ADMIN_TOKEN to enable operator actions.')
    if not secrets.compare_digest(authorization or '', 'Bearer ' + TOKEN):
        raise HTTPException(401, 'Invalid operator token')


@app.get('/')
def index():
    return FileResponse(Path(__file__).parent/'static/index.html')


@app.get('/health')
def health():
    with db() as c:
        c.execute('SELECT 1').fetchone()
    return {'status': 'ok'}


@app.get('/demo/health')
def demo_health():
    if not DEMO:
        raise HTTPException(404, 'Demo disabled')
    if demo_failed:
        raise HTTPException(503, 'Deliberately injected demo failure')
    return {'status': 'ok', 'source': 'demo fixture'}


@app.get('/api/dashboard')
def dashboard():
    with db() as c:
        events = [dict(r) for r in c.execute('SELECT * FROM events ORDER BY id DESC LIMIT 50')]
        metrics = [dict(r) for r in c.execute('SELECT * FROM metrics ORDER BY id DESC LIMIT 60')][::-1]
        history = [dict(r) for r in c.execute('SELECT * FROM checks ORDER BY id DESC LIMIT 240')][::-1]
    services = [states.get(t['id'], dict(**t, status='pending', latency=None, checked_at=None, error=None)) for t in TARGETS]
    return dict(services=services, events=events, metrics=metrics, history=history, interval=INTERVAL,
                demo_enabled=DEMO, demo_failed=demo_failed, controls_enabled=bool(TOKEN),
                metric_scope='Willow process · CPU normalized to logical host cores · RSS memory', server_time=now())


@app.post('/api/check')
async def run_check(authorization: str | None = Header(default=None)):
    authorize(authorization)
    return {'services': await check_all()}


@app.post('/api/demo/toggle')
async def toggle(authorization: str | None = Header(default=None)):
    authorize(authorization)
    if not DEMO:
        raise HTTPException(404, 'Demo disabled')
    global demo_failed
    demo_failed = not demo_failed
    await check_all()
    return {'failed': demo_failed}
