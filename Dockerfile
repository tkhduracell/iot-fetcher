# Stage 1: Build React app
FROM node:22 AS frontend-build
WORKDIR /app
COPY webui/package.json webui/package-lock.json ./
RUN npm ci
COPY webui/ .
RUN npm run build

# Stage 2: Node binaries
FROM node:22 AS node

# Stage 3: Python backend
FROM python:3.13
WORKDIR /app

COPY --from=node /usr/lib /usr/lib
COPY --from=node /usr/local/share /usr/local/share/usr/local/bin
COPY --from=node /usr/local/lib /usr/local/lib
COPY --from=node /usr/local/include /usr/local/include
COPY --from=node /usr/local/bin /usr/local/bin
RUN node -v && npm -v

COPY python/requirements.txt python/
RUN pip install --no-cache-dir -r python/requirements.txt

ARG PYDEBUGGER=0
RUN if [ "$PYDEBUGGER" = "1" ]; then \
        pip install debugpy; \
    fi

# Copy Python stack
COPY python/ ./python

# Copy Node.js stack
COPY nodejs/ ./nodejs
RUN npm --prefix nodejs ci

# Copy built frontend from previous stage
COPY --from=frontend-build /app/dist ./webui/dist
COPY webui/web.py webui/requirements.txt ./webui/
RUN pip install --no-cache-dir -r webui/requirements.txt

# Copy start script
COPY start.sh .

ENTRYPOINT ["/bin/sh", "./start.sh"]