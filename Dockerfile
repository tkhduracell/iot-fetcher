# Stage 1: Build React app
FROM node:22 AS frontend-build
WORKDIR /app
COPY webui/package*.json ./
RUN npm ci || npm install
COPY webui/ .
RUN npm run build

# Stage 2: Build Node.js (esbuild bundle)
FROM node:24-bookworm-slim AS nodejs-build
WORKDIR /app/nodejs
COPY nodejs/package*.json ./
RUN npm install
COPY nodejs/ .
RUN npm run build

# Stage 3: Python backend runtime (plus Node runtime for bundled JS)
FROM python:3.13
WORKDIR /app

# Copy only the Node binary needed to run the bundled script
COPY --from=nodejs-build /usr/local/bin/node /usr/local/bin/node
RUN node -v

# Install InfluxDB CLI
RUN curl -s https://repos.influxdata.com/influxdata-archive.key | gpg --dearmor > /etc/apt/trusted.gpg.d/influxdata.gpg && \
    echo 'deb https://repos.influxdata.com/debian stable main' > /etc/apt/sources.list.d/influxdata.list && \
    apt-get update && apt-get install -y influxdb2-client && \
    apt-get clean && rm -rf /var/lib/apt/lists/* /tmp/* /var/tmp/*

# Python deps
COPY python/requirements.txt python/
RUN pip install --no-cache-dir -r python/requirements.txt

ARG PYDEBUGGER=0
RUN if [ "$PYDEBUGGER" = "1" ]; then \
        pip install debugpy; \
    fi

# Copy Python stack
COPY python/ ./python

# Copy built Node.js artifact only
COPY --from=nodejs-build /app/nodejs/dist ./nodejs/dist
RUN mkdir -p /app/nodejs/dist/proto

# Copy built frontend from previous stage
COPY --from=frontend-build /app/dist ./webui/dist
COPY webui/web.py webui/requirements.txt ./webui/
RUN pip install --no-cache-dir -r webui/requirements.txt

# Copy start script
COPY start.sh .
COPY healthcheck.sh .

HEALTHCHECK --interval=30s --timeout=10s --retries=3 \
  CMD /app/healthcheck.sh

ENTRYPOINT ["/bin/sh", "./start.sh"]