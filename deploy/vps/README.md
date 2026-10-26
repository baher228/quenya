# Quenya API on the VPS

The API container listens on port 8000 and is published only to `127.0.0.1:8100`; Nginx handles public HTTP(S). The frontend remains on Vercel.

## Deploy

1. Install Docker Compose, Nginx and Certbot on the host. Create `/etc/quenya/quenya.env` from `quenya.env.example`, fill in the Supabase and Cursor settings, and restrict it:

   ```sh
   sudo install -d -m 700 /etc/quenya
   sudo install -m 600 deploy/vps/quenya.env.example /etc/quenya/quenya.env
   sudoedit /etc/quenya/quenya.env
   ```

   Keep the env file as plain `KEY=value` lines without quotes; Docker's `env_file` parser preserves quote characters. The example already sets the CLIP text ONNX URL and SHA-256. The image sets `TMPDIR=/cache/tmp` and `HF_HOME=/cache/hf`, so downloaded model/cache files persist in the named volume.

2. Install the Nginx site, start the API, then request the TLS certificate:

   ```sh
   sudo install -m 644 deploy/vps/nginx-quenya-api.conf /etc/nginx/sites-available/quenya-api
   sudo ln -s /etc/nginx/sites-available/quenya-api /etc/nginx/sites-enabled/quenya-api
   sudo nginx -t && sudo systemctl reload nginx
   docker compose -f deploy/vps/docker-compose.yml up -d --build
   sudo certbot --nginx -d srv948389.hstgr.cloud
   ```

3. Check `https://srv948389.hstgr.cloud/health` and confirm `llm_provider` is `cursor` and `llm_ready` is `true`. `QUENYA_CORS_ORIGINS` is a comma-separated exact-origin allowlist; add each Vercel preview origin explicitly if needed.

The current `frontend/vercel.json` rewrite still targets `quenya-api.vercel.app`; point that rewrite at the VPS hostname as a separate frontend cutover step.

## Update, logs and rollback

Sync the approved branch/revision to the host, then rebuild and replace the service:

```sh
docker compose -f deploy/vps/docker-compose.yml up -d --build
docker compose -f deploy/vps/docker-compose.yml logs -f quenya-api
```

To roll back, check out the previously deployed revision and run the same `up -d --build` command. Do not remove the `quenya-cache` volume during an update or rollback.
