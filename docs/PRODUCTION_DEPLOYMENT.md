# ECONITH production deployment

This runbook deploys the controlled defence build: World and Quant are fully
visible and interactive, the LLM route is resilient, real trading is disabled,
and the whole site is protected by one demo login.

## 1. Host prerequisites

- Ubuntu 24.04, recommended 4 vCPU / 8 GB RAM / 160 GB SSD.
- Docker Engine with the Compose plugin.
- DNS A record pointing the chosen hostname to the server.
- Firewall allowing TCP 22, 80 and 443 plus UDP 443. Do not expose 3000, 8000,
  5432, 6379 or 11434.

## 2. Prepare secrets and runtime directories

```bash
cp .env.production.example .env.production
mkdir -p runtime/models runtime/datasets runtime/logs
openssl rand -hex 32
docker run --rm caddy:2-alpine caddy hash-password --plaintext 'choose-a-demo-password'
```

Place the generated values in `.env.production`. Put `DEMO_PASSWORD_HASH` in
single quotes so its leading `$2a$`, `$2b$` or `$argon2...` string is passed
literally and is not interpreted as an environment-variable reference.

Copy any required inference artifacts into `runtime/models` before startup.
Never put live Binance trade credentials in the defence server.

## 3. Validate and start

```bash
docker compose --env-file .env.production -f docker-compose.prod.yml config --quiet
docker compose --env-file .env.production -f docker-compose.prod.yml build
docker compose --env-file .env.production -f docker-compose.prod.yml up -d
docker compose --env-file .env.production -f docker-compose.prod.yml ps
docker compose --env-file .env.production -f docker-compose.prod.yml logs --tail=200 backend_core
```

Caddy obtains HTTPS automatically when `SITE_ADDRESS` is a real hostname whose
DNS already points at this server.

## 4. Optional local open-source LLM fallback

Use this only on a server with enough free memory. The remote API remains the
first route so a slow local model cannot block the presentation.

```bash
sed -i 's/LOCAL_LLM_ENABLED=false/LOCAL_LLM_ENABLED=true/' .env.production
docker compose --env-file .env.production -f docker-compose.prod.yml --profile local-llm up -d ollama
docker compose --env-file .env.production -f docker-compose.prod.yml exec ollama ollama pull llama3.2:3b
docker compose --env-file .env.production -f docker-compose.prod.yml up -d backend_core
```

For the original `llama3:8b`, use at least 16 GB RAM or a separate GPU host.

## 5. Smoke checks

```bash
curl -I https://YOUR_DOMAIN/
curl -u 'econith:YOUR_PASSWORD' https://YOUR_DOMAIN/api/v1/health
docker compose --env-file .env.production -f docker-compose.prod.yml ps
```

Then verify `/`, `/world` and `/quant` on desktop and mobile. World Agent events
must continue updating, Quant WebSockets must show `LIVE`, and changing speed or
simulation mode must create an audit entry under `runtime/logs`.

## 6. Safe update and rollback

```bash
git pull --ff-only
docker compose --env-file .env.production -f docker-compose.prod.yml build
docker compose --env-file .env.production -f docker-compose.prod.yml up -d
```

Before updating, record the working commit with `git rev-parse HEAD`. Roll back
by checking out that commit and rebuilding the two application images. Database,
Redis, Caddy certificates, datasets and logs remain in persistent volumes or
runtime directories.
