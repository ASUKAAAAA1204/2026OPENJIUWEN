# syntax=docker/dockerfile:1

# ---------- UI 静态资源 ----------
FROM node:22-alpine AS ui-build
WORKDIR /ui
COPY ui/package.json ui/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY ui/ ./
RUN npm run build

# ---------- Nginx 同源入口 ----------
FROM nginxinc/nginx-unprivileged:1.27-alpine AS web
COPY deploy/nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=ui-build /ui/dist /usr/share/nginx/html
EXPOSE 8080

# ---------- 中央服务 ----------
FROM python:3.12-slim AS app
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    TEMIS_CONFIG=/app/configs/platform.docker.toml
WORKDIR /app

RUN apt-get update \
 && apt-get install -y --no-install-recommends ca-certificates git \
 && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml README.md LICENSE ./
COPY src ./src
RUN python -m pip install --no-cache-dir --upgrade pip \
 && python -m pip install --no-cache-dir ".[api,ledger_crypto]"

COPY scripts ./scripts
COPY configs ./configs
COPY docs/api/generated ./docs/api/generated

RUN groupadd --gid 10001 temis \
 && useradd --uid 10001 --gid temis --create-home --shell /usr/sbin/nologin temis \
 && mkdir -p /app/data /app/outputs /app/workspaces/workspace \
 && chown -R temis:temis /app \
 && chmod +x /app/scripts/docker_entrypoint.sh

USER temis
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=3)"
ENTRYPOINT ["/app/scripts/docker_entrypoint.sh"]
CMD ["serve"]
