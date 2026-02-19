---
name: deployment-logs
description: Check Docker container logs on the rpi5 deployment
disable-model-invocation: true
allowed-tools: Bash
---

# Deployment Logs

Check Docker container logs on the rpi5 deployment server.

## Arguments

- `service` (optional): The service name to check logs for (e.g. `home-assistant`, `https-proxy`, `iot-fetcher`). If not provided, shows logs for all services.
- `lines` (optional): Number of lines to show, defaults to 50.

## Steps

1. **SSH into rpi5 and run docker compose logs**:

```bash
ssh rpi5 "cd ~/iot-fetcher && sudo docker compose -f docker-compose.yml -f docker-compose.local.yml logs {service} --tail {lines}"
```

If no service is specified, omit the service name to show all logs.

2. **Available services**:
   - `home-assistant` - Home Assistant
   - `https-proxy` - Caddy HTTPS reverse proxy
   - `iot-fetcher` - IoT data fetcher
   - `victoria-metrics` - VictoriaMetrics database
   - `victoria-metrics-auth` - VMAuth authentication proxy
   - `tibber-influxdb-bridge` - Tibber data bridge
   - `garmin-influx-bridge` - Garmin data bridge

3. **Report findings**: Summarize any errors or warnings found in the logs.
