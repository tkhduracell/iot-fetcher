# IoT Fetcher Development Instructions

**ALWAYS** follow these instructions first and only fallback to additional search or bash commands when the information here is incomplete or found to be in error.

This repository is an IoT data collection system with Python backends, Node.js services, and a React web interface, containerized for deployment on Balena-enabled devices.

## Working Effectively

### Essential Build Commands
- **Bootstrap dependencies:**
  - Node.js components: `cd webui && npm install` (takes ~7 seconds)
  - Node.js components: `cd nodejs && npm install` (takes ~5 seconds)
  - Python backend: `cd python && pip install -r requirements.txt` (takes ~45 seconds)
  - WebUI Flask: `cd webui && pip install -r requirements.txt` (takes ~10 seconds)

- **Build components individually:**
  - WebUI: `cd webui && npm run build` -- takes ~2 seconds. **NEVER CANCEL**.
  - Node.js: `cd nodejs && npm run build` -- takes ~1 second. **NEVER CANCEL**.

- **Run components for development:**
  - WebUI server: `cd webui && export WEB_UI_PORT=8080 && python web.py` -- starts on port 8080
  - InfluxDB for testing: `docker run --rm -d --name influxdb-test -p 8086:8086 influxdb:2`

- **Docker operations:**
  - **CRITICAL**: Full Docker build via `make run` or `docker build . -t iot-fetcher:test` **CURRENTLY FAILS** due to npm install issues in the Node.js build stage with the `eufy-security-client` dependency. The error is: "npm error Exit handler never called!" during the Docker build process.
  - **Workaround**: Individual components work perfectly outside Docker. Use direct component testing instead.
  - Development database only: `docker compose up database -d` (may have runtime issues in CI environments)

### Environment Setup
- Copy `.env.template` to `.env` and configure required variables:
  - Minimum for testing: `INFLUX_HOST`, `INFLUX_TOKEN`, `INFLUX_ORG`, `INFLUX_BUCKET`, `WEB_UI_PORT`
  - Python modules require many additional environment variables specific to each IoT service

### Validation and Testing

**ALWAYS run these validation steps after making changes:**

1. **Component Build Tests:**
   - `cd webui && npm install && npm run build` -- **NEVER CANCEL**. Takes ~10 seconds total.
   - `cd nodejs && npm install && npm run build` -- **NEVER CANCEL**. Takes ~6 seconds total.
   - `cd python && pip install -r requirements.txt` -- **NEVER CANCEL**. Takes ~60 seconds.
   - `cd webui && pip install -r requirements.txt` -- **NEVER CANCEL**. Takes ~15 seconds.

2. **Functional Testing:**
   - Start InfluxDB: `docker run --rm -d --name influxdb-test -p 8086:8086 influxdb:2`
   - Test WebUI: `cd webui && export WEB_UI_PORT=8080 && timeout 10 python web.py`
   - Verify HTTP response: `curl -s -o /dev/null -w "%{http_code}" http://localhost:8080/` (should return 200)
   - Test Node.js component: `cd nodejs && timeout 10 node dist/index.js` (should start scheduler and attempt service connections)
   - Cleanup: `docker stop influxdb-test && pkill -f "python web.py"`

**Manual validation requirement:** After building, you MUST test actual functionality by running the complete scenario above. Simply building is NOT sufficient validation.

## Common Tasks and Time Expectations

### Directory Structure
```
.
├── README.md                 # Main project documentation  
├── docker-compose.yml        # Multi-service orchestration (InfluxDB, proxy, bridges)
├── Dockerfile               # **BROKEN**: Multi-stage build fails on npm install
├── Makefile                 # Build shortcuts (build, run, dev, push, deploy)
├── python/                  # IoT data fetchers and InfluxDB backup system
│   ├── requirements.txt     # Python dependencies
│   └── src/                 # IoT modules: balboa, elpris, ngenic, aqualink, etc.
├── webui/                   # React + TypeScript web interface with Flask backend
│   ├── package.json         # Frontend dependencies (React, Vite, Tailwind)
│   ├── requirements.txt     # Flask backend dependencies
│   └── web.py              # Flask server serving built React app
├── nodejs/                  # TypeScript cron jobs with esbuild
│   └── package.json         # Node 24+, eufy-security-client, influxdb-client
└── https-proxy/            # Caddy HTTPS reverse proxy
```

### Build Timing and Cancellation Warnings
- **WebUI npm install**: ~7 seconds -- **NEVER CANCEL**
- **WebUI build**: ~2 seconds -- **NEVER CANCEL**  
- **Node.js npm install**: ~5 seconds -- **NEVER CANCEL**
- **Node.js build**: ~1 second -- **NEVER CANCEL**
- **Python dependencies**: ~45-60 seconds -- **NEVER CANCEL**
- **Docker build**: **FAILS CONSISTENTLY** -- npm install fails with "Exit handler never called!" in Docker environment

### Known Issues and Workarounds
- **Docker Build Failure**: The multi-stage Docker build fails during `npm install` for the `eufy-security-client` dependency. This is a persistent issue in the Docker build environment.
  - **Workaround**: Test components individually outside Docker
  - **Do NOT** attempt to fix this by modifying the Dockerfile unless specifically asked
- **Environment Variables**: Python modules require extensive environment configuration. Most will fail without proper IoT service credentials.
- **Node.js Version**: Requires Node 24+ as specified in `nodejs/package.json`

## Repository Context
- **Owner**: tkhduracell  
- **Primary Languages**: Python, TypeScript, JavaScript
- **Deployment Target**: Balena-enabled IoT devices
- **Main Services**: 
  - IoT data collection (Balboa spa, Elpris energy prices, Ngenic, Aqualink pool, etc.)
  - InfluxDB backup system with Google Cloud Storage
  - Real-time web dashboard
  - HTTPS reverse proxy with Let's Encrypt

## Commit Style
Use Conventional Commits (max 160 chars):
- `feat: add new IoT sensor integration`
- `fix: resolve InfluxDB connection timeout`
- `chore: update dependency to v2.1.0`

**Always build and test changes before committing. The CI pipeline expects working builds.**
