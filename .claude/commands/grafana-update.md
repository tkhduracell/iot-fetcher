---
description: Regenerate Grafana dashboard JSON from the TypeScript SDK source
allowed-tools: Bash, Read, Edit
---

## Your Task

Regenerate the Grafana dashboard using the TypeScript SDK in `./grafana/`.

### Guardrails
- Always update `grafana/src/index.ts` — never edit the generated JSON directly
- Requires Node >= 25 (check `.nvmrc`)

### Steps

1. **Apply requested changes** to `grafana/src/index.ts` if the user asked for modifications.

2. **Build the dashboard JSON**:
```bash
cd grafana && npm run build
```

3. **Verify the output**: Check that `grafana/dist/` contains the updated dashboard JSON.

4. **If user wants to upload to Grafana** (optional):
```bash
cd grafana && npm run generate
```
This requires `grafana/.env` with valid Grafana API credentials.
