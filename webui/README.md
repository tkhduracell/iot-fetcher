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
- `GET /roborock/zones` - List available cleaning zones/rooms
- `POST /roborock/clean` - Start cleaning (full clean or zone-specific)
  - Body: `{}` for full clean
  - Body: `{"zone_id": "1"}` for zone-specific clean

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

The Roborock integration allows you to:
- List available cleaning zones/rooms
- Start full vacuum cleaning
- Start zone-specific cleaning

**Requirements:**
- Valid Roborock account credentials
- Device must be connected to Roborock cloud service

**API Usage Examples:**

```bash
# Get available zones
curl http://localhost:8080/roborock/zones

# Start full clean
curl -X POST http://localhost:8080/roborock/clean \
     -H "Content-Type: application/json" \
     -d '{}'

# Clean specific zone
curl -X POST http://localhost:8080/roborock/clean \
     -H "Content-Type: application/json" \
     -d '{"zone_id": "5"}'
```

## License

This project is licensed under the MIT License.