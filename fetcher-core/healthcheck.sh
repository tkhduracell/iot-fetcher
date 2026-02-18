#!/bin/sh

# 1. Check if Next.js server is running
pgrep -f "next-server" >/dev/null || { echo "Web UI is down"; exit 1; }

# 2. Check if Python scheduler is running
pgrep -f "python3 ./python/src/main.py" >/dev/null || { echo "Python service is down"; exit 1; }

# 3. Check if HTTP endpoint is alive
curl -fs http://localhost:${WEB_UI_PORT:-8080}/api/health || { echo "Web UI health check failed"; exit 1; }
