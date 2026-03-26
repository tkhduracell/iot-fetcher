FROM python:3.13

WORKDIR /workspace

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    curl \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Install Node.js 22 (LTS)
RUN curl -fsSL https://deb.nodesource.com/setup_22.x | bash - \
    && apt-get install -y nodejs \
    && node --version && npm --version


COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

COPY python/pyproject.toml python/uv.lock ./python/
RUN uv pip install --system --no-cache -r python/pyproject.toml

COPY webui/package.json ./webui/package.json
COPY webui/package-lock.json ./webui/package-lock.json
RUN npm -C webui ci