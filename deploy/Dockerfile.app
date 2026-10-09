# syntax=docker/dockerfile:1
# Image für ems-app (Phase 1b: Anmeldung, Preise, Prognose, Abrechnung, HA – nur lesend).
# Bauen aus dem Repository-Wurzelverzeichnis:
#   docker build -f deploy/Dockerfile.app -t bindaems-app:dev .
FROM ghcr.io/astral-sh/uv:0.11.32 AS uv

FROM python:3.12-slim

COPY --from=uv /uv /uvx /bin/

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/app/.venv \
    PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1

WORKDIR /app

# Abhängigkeiten zuerst (eigene Schicht, bleibt bei Codeänderungen im Cache)
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --locked --no-dev --extra app --no-install-project

COPY src ./src
RUN uv sync --locked --no-dev --extra app --no-editable

RUN useradd --uid 10001 --create-home --shell /usr/sbin/nologin ems \
    && mkdir -p /data /backup \
    && chown ems:ems /data /backup

USER ems
VOLUME ["/data", "/backup"]
EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD ["python", "-m", "bindaems.app.healthcheck"]

ENTRYPOINT ["python", "-m", "bindaems.app"]
CMD ["serve", "--config", "/config/config.yaml"]
