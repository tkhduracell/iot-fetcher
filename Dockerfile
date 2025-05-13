# Stage 1: Build React app
FROM node:22 AS frontend-build
WORKDIR /app
COPY webui/package.json webui/package-lock.json ./
RUN npm install
COPY webui/ .
RUN npm run build

# Stage 2: Python backend
FROM python:3.13
WORKDIR /app

COPY python/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

ARG PYDEBUGGER=0
RUN if [ "$PYDEBUGGER" = "1" ]; then \
        pip install debugpy; \
    fi

COPY python/src .

# Copy built frontend from previous stage
COPY --from=frontend-build /app/dist ./dist

CMD [ "python", "./main.py" ]