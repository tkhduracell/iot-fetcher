# Stage 1: Build Next.js app
FROM node:22 AS frontend-build
WORKDIR /app
COPY webui/package*.json ./
RUN npm ci || npm install
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

# Python deps for scheduler
COPY python/requirements.txt python/
RUN pip install --no-cache-dir -r python/requirements.txt

ARG PYDEBUGGER=0
RUN if [ "$PYDEBUGGER" = "1" ]; then \
        pip install debugpy; \
    fi

# Copy Python scheduler
COPY python/ ./python

# Python deps for roborock sidecar
COPY roborock-sidecar/requirements.txt roborock-sidecar/
RUN pip install --no-cache-dir -r roborock-sidecar/requirements.txt

# Copy roborock sidecar
COPY roborock-sidecar/ ./roborock-sidecar

# Copy Next.js standalone build
COPY --from=frontend-build /app/.next/standalone ./webui
COPY --from=frontend-build /app/.next/static ./webui/.next/static

# Copy start script
COPY start.sh .
COPY healthcheck.sh .

RUN chmod +x start.sh healthcheck.sh

HEALTHCHECK --interval=30s --timeout=10s --retries=3 \
  CMD /app/healthcheck.sh

ENTRYPOINT ["/bin/sh", "./start.sh"]
