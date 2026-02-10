#!/bin/sh

# 1. Check if python script is running
pgrep -f "python3 ./webui/web.py" >/dev/null || (echo "Web UI is down" && exit 1)

# 2. Check if another process is running
pgrep -f "python3 ./python/src/main.py" >/dev/null || (echo "Python service is down" && exit 1)

# 3. Check if HTTP endpoint is alive
curl -fs http://localhost:${WEB_UI_PORT}/health || (echo "Web UI health check failed" && exit 1)
