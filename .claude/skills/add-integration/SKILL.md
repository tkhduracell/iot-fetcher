---
name: add-integration
description: Scaffold a new IoT device integration module with InfluxDB metrics
disable-model-invocation: true
allowed-tools: Edit, Write, Read, Glob, Grep, Bash
---

# Add IoT Integration

Scaffold a new IoT device integration for the iot-fetcher platform.

## Arguments

- `name` (required): The integration name in snake_case (e.g. `my_device`)
- `interval` (optional): Scheduler interval, defaults to `every(5).minutes`

## Steps

1. **Create the Python module** at `fetcher-core/python/src/{name}.py` following this pattern:

```python
import os
import logging
from influx import write_influx, Point

logger = logging.getLogger(__name__)

# Configuration from environment
# TODO: Add required env vars
host = os.environ.get('{NAME}_HOST', '')


def {name}():
    if not host:
        logger.error("[{name}] {NAME}_HOST environment variable not set, ignoring...")
        return
    try:
        _fetch_{name}()
    except Exception:
        logger.warning("[{name}] Unexpected error", exc_info=True)


def _fetch_{name}():
    logger.info("[{name}] Fetching data...")

    # TODO: Implement device communication

    points = [
        Point("{name}_measurement")
            .tag("source", "{name}")
            .field("value", 0.0)
    ]

    write_influx(points)
```

2. **Register in `fetcher-core/python/src/main.py`**:
   - Add import: `from {name} import {name}`
   - Add to the `sys.argv[1] in [...]` list in the CLI handler
   - Add to the `for m in [...]` list in the CLI handler
   - Add scheduler: `schedule.every(5).minutes.do({name})` (adjust interval as needed)

3. **Add environment variables** to `fetcher-core/python/.env.template`:
   - Add the required env vars with empty defaults and a comment

4. **Summary**: Print what was created and what the developer needs to do next:
   - Implement the device communication logic in `_fetch_{name}()`
   - Add actual env vars to `.env`
   - Add any new Python dependencies to `fetcher-core/python/requirements.txt`
