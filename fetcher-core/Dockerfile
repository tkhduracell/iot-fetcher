# Stage 1: Build Next.js app
FROM node:22 AS frontend-build
WORKDIR /app
COPY webui/package.json ./
COPY webui/package-lock.json ./
RUN npm ci
COPY webui/ .
RUN npm run build

# Stage 2: Python + Node.js runtime
FROM python:3.13
WORKDIR /app

# Install Node.js 22 for Next.js standalone server
RUN curl -fsSL https://deb.nodesource.com/setup_22.x | bash - && \
    apt-get install -y nodejs && \
    apt-get clean && rm -rf /var/lib/apt/lists/*

# Install InfluxDB CLI
RUN curl -s https://repos.influxdata.com/influxdata-archive.key | gpg --dearmor > /etc/apt/trusted.gpg.d/influxdata.gpg && \
    echo 'deb https://repos.influxdata.com/debian stable main' > /etc/apt/sources.list.d/influxdata.list && \
    apt-get update && apt-get install -y influxdb2-client && \
    apt-get clean && rm -rf /var/lib/apt/lists/* /tmp/* /var/tmp/*

# Install uv
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

# Python deps for scheduler
COPY python/pyproject.toml python/uv.lock python/
RUN uv pip install --system --no-cache -r python/pyproject.toml

ARG PYDEBUGGER=0
RUN if [ "$PYDEBUGGER" = "1" ]; then \
        uv pip install --system debugpy; \
    fi

# Copy Python scheduler
COPY python/ ./python

# Copy Next.js standalone build
COPY --from=frontend-build /app/.next/standalone ./webui
COPY --from=frontend-build /app/.next/static ./webui/.next/static

# Copy start script
COPY start.sh .
COPY healthcheck.sh .

RUN chmod +x start.sh healthcheck.sh

HEALTHCHECK --interval=30s --timeout=10s --start-period=60s --retries=3 \
  CMD /app/healthcheck.sh

ENTRYPOINT ["/bin/sh", "./start.sh"]
