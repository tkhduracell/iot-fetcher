---
description: Quick health check of all services running on rpi5
allowed-tools: Bash
---

## Your Task

Run a quick health check of all Docker services on rpi5 and report their status.

### Steps

1. **Check container status**:
```bash
ssh rpi5 "cd ~/iot-fetcher && sudo docker compose -f docker-compose.yml -f docker-compose.local.yml ps --format 'table {{.Name}}\t{{.Status}}\t{{.Ports}}'"
```

2. **Check for recently restarted or unhealthy containers**:
```bash
ssh rpi5 "sudo docker ps --format '{{.Names}}\t{{.Status}}' | grep -E 'Restarting|unhealthy|Exited'"
```

3. **Check disk usage** (VictoriaMetrics data can grow):
```bash
ssh rpi5 "df -h /home"
```

4. **Report summary**: List all services with their status. Flag any that are not running, restarting, or unhealthy. Note disk usage if above 80%.
