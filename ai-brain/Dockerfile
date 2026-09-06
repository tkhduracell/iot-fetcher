FROM python:3.13-slim
WORKDIR /app
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project
COPY src ./src
COPY seed ./seed
RUN uv sync --frozen --no-dev
ENV SEED_ROOT=/app/seed MEMORY_ROOT=/memory TZ=Europe/Stockholm
VOLUME ["/memory"]
CMD ["uv", "run", "--no-sync", "python", "-m", "ai_brain"]
