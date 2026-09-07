FROM python:3.13-slim
WORKDIR /app
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project
COPY src ./src
COPY seed ./seed
RUN uv sync --frozen --no-dev
# Non-root, after the sync so uv installs as root. uid 1000 is what the host
# bind mount must be owned by; see the rollout notes in README.md.
RUN useradd -u 1000 -m brain && mkdir -p /memory && chown brain:brain /memory /app
USER brain
ENV SEED_ROOT=/app/seed MEMORY_ROOT=/memory TZ=Europe/Stockholm
VOLUME ["/memory"]
# The venv interpreter directly, so the container's PID 1 is the brain itself
# and a SIGTERM from `docker stop` reaches it rather than a uv wrapper.
CMD ["/app/.venv/bin/python", "-m", "ai_brain"]
