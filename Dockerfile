FROM ghcr.io/astral-sh/uv:0.12.23 AS uv

FROM python:3.12-slim

COPY --from=uv /uv /usr/local/bin/uv

WORKDIR /app

ENV UV_LINK_MODE=copy PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1

COPY pyproject.toml uv.lock ./

RUN --mount=type=secret,id=build_ca \
    if [ -f /run/secrets/build_ca ]; then export SSL_CERT_FILE=/run/secrets/build_ca; fi; \
    uv sync --locked --no-dev --no-install-project --system-certs

COPY --chown=10001:10001 backend/ ./backend/
COPY --chown=10001:10001 config/ ./config/

USER 10001:10001

ENTRYPOINT ["/app/.venv/bin/python"]

CMD ["-m", "backend.jobs.workers.worker", "scrape"]
