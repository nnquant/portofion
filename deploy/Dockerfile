# syntax=docker/dockerfile:1.7
FROM node:22-alpine AS web
WORKDIR /web
COPY web/package.json web/package-lock.json ./
RUN npm ci
COPY web/ ./
RUN npm run build

FROM python:3.12-slim AS app
COPY --from=ghcr.io/astral-sh/uv:0.6.10 /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PROJECT_ENVIRONMENT=/opt/venv PATH="/opt/venv/bin:$PATH"
WORKDIR /src
COPY pyproject.toml uv.lock .python-version ./
COPY engine/ engine/
COPY server/ server/
COPY client/ client/
RUN uv sync --frozen --no-dev --package portofion-server \
 && uv build --package portofion-client --wheel --out-dir /opt/sdk
COPY --from=web /web/dist /opt/static
ENV PORTOFION_STATIC_DIR=/opt/static PORTOFION_SDK_DIR=/opt/sdk
EXPOSE 8000
CMD ["sh", "-c", "portofion migrate && exec uvicorn portofion_server.main:app --host 0.0.0.0 --port 8000 --workers ${WORKERS:-2} --proxy-headers"]
