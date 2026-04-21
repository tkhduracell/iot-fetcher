---
name: list-metrics
description: List VictoriaMetrics metric names via ssh rpi5 + docker exec (no external auth, Docker-internal network). Use when the user asks "what metrics exist", "list VictoriaMetrics metrics", "find metric named X", or wants to grep/filter the metric catalog. Prefer this over victoriametrics-query for fast name-only lookups.
disable-model-invocation: true
allowed-tools: Bash
---

# List VictoriaMetrics Metrics

Lists all metric names in the VictoriaMetrics instance running on rpi5 by querying the Docker-internal `database:8181` endpoint from inside the `iot-fetcher` container. No auth, no external proxy.

## Arguments

- `filter` (optional): case-insensitive substring to filter metric names. If omitted, lists all metrics.

## Steps

1. **Fetch metric names** from VictoriaMetrics via an existing container on the same Docker network:

   ```bash
   ssh rpi5 "sudo docker exec iot-fetcher sh -c 'wget -qO- http://database:8181/api/v1/label/__name__/values'"
   ```

   This returns JSON: `{"status":"ok","data":[...names...]}`.

2. **Parse and filter locally** with `python3` (avoids depending on `jq` inside the container):

   ```bash
   ssh rpi5 "sudo docker exec iot-fetcher sh -c 'wget -qO- http://database:8181/api/v1/label/__name__/values'" \
     | python3 -c "import json,sys,os; d=json.load(sys.stdin); names=sorted(d.get('data',[])); f=os.environ.get('F','').lower(); m=[n for n in names if f in n.lower()]; print(f'total={len(names)} matching={len(m)}'); print('\n'.join(m))"
   ```

   Pass the filter via the `F` env var: `F=balboa ssh rpi5 ...` (set at the start of the pipeline).

3. **Report**: print the totals line and the sorted matches. If filter yielded zero matches, say so explicitly.

## Notes

- This skill is intentionally lighter than `victoriametrics-query`, which goes through the external https-proxy with `INFLUX_TOKEN`. Use that one if you need PromQL queries or label values.
- VictoriaMetrics listens on `8181` (non-default) per `docker-compose.yml` — `-httpListenAddr=:8181`.
