# Publish Willow on Render

## Free deployment

1. Sign in at https://dashboard.render.com and connect GitHub.
2. Choose New > Blueprint and select Kisukii/willow on main.
3. Review render.yaml: one Docker web service, Free plan, Singapore region, /health check. Do not select paid resources.
4. Create the service. Render builds the Dockerfile and generates a private ADMIN_TOKEN environment value.
5. Wait until the service is Live. Open the exact onrender.com URL shown by Render; do not guess the URL from the service name.
6. Verify /health returns {"status":"ok"}, the dashboard loads, and both default services become healthy.
7. For operator actions, retrieve ADMIN_TOKEN privately from Render environment settings and enter it in Configuration. Do not put it in the internship application.
8. Submit the verified public URL and https://github.com/Kisukii/willow.

Automatic updates use checksPass: tests and the container job must pass first. The initial Blueprint deployment can begin before checks complete; review the first CI run before submitting.

## Cost assumptions

Estimate, not billing data: Render Free Docker web service, Singapore, one instance, no custom domain, no paid database or persistent disk, light internship-review traffic within the free allowance: **$0/month**. Confirm the plan at creation; do not authorize an upgrade for this demo. Pricing checked October 26, 2026 at https://render.com/pricing and https://render.com/docs/free.

Free web services spin down after 15 minutes without incoming traffic. The first visitor may wait for startup. Internal self-probes do not make this a reliable always-on external monitor. Local filesystem data is ephemeral; SQLite history can disappear on restart/redeployment. Persistent disks require paid services and are not included here. For a production monitor, choose persistent storage and always-on compute only after reviewing cost and authorizing it.

## Troubleshooting

- Port in use locally: choose PORT=8001 and start uvicorn with --port 8001.
- Startup failure: inspect Render logs; confirm Docker runtime and writable /data directory.
- Self-probes fail: PORT must match the server. Clear a stale SELF_URL override.
- 401 controls: use the current server ADMIN_TOKEN. 403 means controls are disabled.
- Configured endpoint unavailable: check DNS, TLS, service port, firewall and application logs. Redirects and non-2xx responses fail by design.
- Lost history after Render restart: expected on Free ephemeral storage; use Docker Compose locally for persistent demo history.
- Deployment blocked by CI: open the failed Actions job, correct the error, and push again.
- Old styling: hard refresh the browser.

## Verification record

Local: 11 tests passed, JavaScript syntax checked, HTTP self-probes and injected 503/recovery verified. Docker image, GitHub CI and public hosting require their actual remote execution before they can be marked verified.
