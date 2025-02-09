# iot_fetcher
The `iot_fetcher` project is designed to collect and process data from various IoT devices. It provides a framework for fetching data from sensors, storing it in a database, and analyzing it to gain insights. The project aims to simplify the integration and management of IoT devices in a scalable and efficient manner.

## Features

  - Aqualink - Connects to pool pump to fetch temperature and settings
  - Airquality - Connection to Google Maps Airquality API
  - Balboa - Connects to SPA to fetch temperature and settings
  - Elpris - Fetches energy price in Sweden SE 1-4
  - Ngenic - Fetches temperature and settings from Ngenic

## Make setup

  - `make build` Build the container an tag it
  - `make run` Start the application inside the docker container
  - `make push` Build and push the container
  - `make dev` Rebuild container with pydebugger installed and start application with debugger on port `5678`

