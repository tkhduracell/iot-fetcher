# Stage 1: Build React app
FROM node:22 AS frontend-build
WORKDIR /app
COPY webui/package.json webui/package-lock.json ./
RUN npm ci
COPY webui/ .
RUN npm run build

# Stage 2: Python backend
FROM python:3.13
WORKDIR /app

ARG TARGETARCH=linux-arm64
ARG NODE_VERSION=22.14.0

# If the architecture is amd64, set linux-x64 as the architecture
RUN if [ "${TARGETARCH}" = "amd64" ]; then \
        export NODE_ARCH=linux-x64; \
    else \
        export NODE_ARCH=${TARGETARCH}; \
    fi;\
    (cd /tmp && \
    wget https://nodejs.org/dist/v${NODE_VERSION}/node-v${NODE_VERSION}-${NODE_ARCH}.tar.xz \
    && tar -xf node-v${NODE_VERSION}-${NODE_ARCH}.tar.xz --strip-components=1 -C /usr/local \
    && rm node-v${NODE_VERSION}-${NODE_ARCH}.tar.xz)

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

CMD ["/bin/sh", "./start.sh"]