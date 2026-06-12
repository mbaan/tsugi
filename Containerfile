# Build and runtime both use the same Python so the venv built in the builder
# stage points at an interpreter that actually exists in the runtime stage.

FROM python:3.13-slim-bookworm AS builder

# uv: fast, reproducible dependency install (copied from the official image)
COPY --from=ghcr.io/astral-sh/uv:0.11.19 /uv /uvx /bin/

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=0

WORKDIR /app

# Dependencies only — `package = false` means the project itself isn't installed.
# Copy the lockfile + manifest first so this layer caches across app-code edits.
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev


FROM python:3.13-slim-bookworm AS runtime

RUN useradd --create-home --uid 1000 tsugi

WORKDIR /app

COPY --from=builder /app/.venv /app/.venv
ENV PATH="/app/.venv/bin:$PATH"

COPY app ./app

# Data (sqlite + covers) lives outside the image, on a volume.
ENV TSUGI_DATA=/data
RUN mkdir -p /data && chown tsugi:tsugi /data
VOLUME /data

USER tsugi
EXPOSE 9000

# The app's __main__ binds 127.0.0.1; inside a container we must bind 0.0.0.0.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "9000"]
