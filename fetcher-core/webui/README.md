# IoT Fetcher WebUI

This is a Flask-based web application that provides a web interface and API endpoints for the IoT Fetcher system. It includes React frontend components and various API endpoints for device management.

## Project Structure

```
webui/
├── public
│   └── index.html        # Main HTML structure of the application
├── src                   # React frontend source
│   ├── App.tsx          # Main React component
│   ├── main.tsx         # Entry point for the React application
│   └── components       # React components
├── web.py               # Flask backend application
├── requirements.txt     # Python dependencies
├── package.json         # npm configuration file
├── tsconfig.json        # TypeScript configuration file
└── vite.config.ts       # Vite configuration file
```

## API Endpoints

### Health
- `GET /health` - Check application health

### InfluxDB Proxy
- `POST /influx/api/v2/query` - Proxy to InfluxDB query API
- `GET /influx/api/v2/health` - Proxy to InfluxDB health check

### Metrics
- `GET /metrics/garmin` - Get Garmin device metrics

### Sonos
- `GET /sonos/*` - Proxy to Sonos API

### Roborock API
- `GET /roborock/targets` - List available floors and rooms from Home Assistant
- `GET /roborock/status` - Get vacuum status from Home Assistant
- `POST /roborock/trigger` - Trigger vacuum clean via Home Assistant
- `POST /roborock/dock` - Return vacuum to dock via Home Assistant

### File Upload
- `POST /upload` - Upload files
- `GET /upload/<uuid>` - Retrieve uploaded files

## Environment Variables

The following environment variables need to be configured:

```bash
# Roborock Integration
ROBOROCK_USERNAME=your_roborock_email
ROBOROCK_PASSWORD=your_roborock_password

# Other integrations
INFLUX_HOST=influxdb_host
INFLUX_TOKEN=influxdb_token
SONOS_HOST=sonos_host
WEB_UI_PORT=8080
```

## Getting Started

1. **Install Python dependencies:**
   ```bash
   pip install -r requirements.txt
   ```

2. **Install npm dependencies:**
   ```bash
   npm install
   ```

3. **Set up environment variables:**
   ```bash
   cp ../.env.template ../.env
   # Edit .env with your configuration
   ```

4. **Build frontend:**
   ```bash
   npm run build
   ```

5. **Run the Flask server:**
   ```bash
   python web.py
   ```

6. **Open your browser:**
   Navigate to `http://localhost:8080` to access the web interface.

## Development

For frontend development:
```bash
npm run dev
```

For backend development with auto-reload:
```bash
export FLASK_ENV=development
python web.py
```

## Roborock Integration

The Roborock clean dialog is driven by Home Assistant. It allows you to:
- View available floors and rooms configured in Home Assistant
- Start full vacuum cleaning
- Start floor-specific or room-specific cleaning
- Check vacuum status and dock it

**Requirements:**
- Home Assistant instance running with Roborock integration configured
- Long-lived access token for authentication

**Configuration:**

Add to `fetcher-core/webui/.env` (loaded by `docker-compose.local.yml`):

```
HOMEASSISTANT_URL=http://host.docker.internal:8123
HOMEASSISTANT_TOKEN=<long-lived access token>
```

Home Assistant runs with `network_mode: host`, so it does **not** join the compose
bridge network — the service name `home-assistant` will not resolve from this
container. Use `host.docker.internal`, which `docker-compose.yml` already maps to
`host-gateway` for this service; unlike a hardcoded LAN IP it survives the host's
address changing.

For local development outside Docker, point it at the LAN address instead
(e.g. `http://192.168.68.87:8123`).

Both are read server-side only; the token never reaches the browser. When either is
unset the dialog reports no targets and the Clean button hides itself.

Floors and rooms are discovered from the `roborock_floor` and `roborock_room` HA
labels — run `scripts/roborock-ha-provision.mjs` to create them. To add a room later,
label its automation in Home Assistant; no code change is needed.

## License

This project is licensed under the MIT License.