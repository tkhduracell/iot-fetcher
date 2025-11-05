# iot_fetcher
The `iot_fetcher` project is designed to collect and process data from various IoT devices. It provides a framework for fetching data from sensors, storing it in a database, and analyzing it to gain insights. The project aims to simplify the integration and management of IoT devices in a scalable and efficient manner.

## Features

  - Aqualink - Connects to pool pump to fetch temperature and settings
  - Airquality - Connection to Google Maps Airquality API
  - Balboa - Connects to SPA to fetch temperature and settings
  - Elpris - Fetches energy price in Sweden SE 1-4
  - Ngenic - Fetches temperature and settings from Ngenic
  - Tapo - Connects to TP-Link Tapo smart devices to fetch power state, energy usage, and device metrics
  - **InfluxDB Backup** - Automated backup system that exports all buckets to Google Cloud Storage every 12 hours

## Environment Variables

### General Configuration
- `INFLUX_HOST` - InfluxDB server hostname/IP
- `INFLUX_TOKEN` - InfluxDB authentication token
- `INFLUX_ORG` - InfluxDB organization name
- `INFLUX_BUCKET` - InfluxDB bucket name for data storage

### Module-Specific Configuration

#### TAPO (TP-Link Smart Devices)
- `TAPO_EMAIL` - Your TP-Link/Tapo account email address
- `TAPO_PASSWORD` - Your TP-Link/Tapo account password

Run TAPO module individually for testing:
```bash
docker run --rm --env-file .env iot-fetcher:latest -- tapo
```

## InfluxDB Backup & Restore

### Automated Backups
- **Schedule**: Runs automatically every 12 hours
- **Storage**: Google Cloud Storage with date-based folders (`backup/YYYY-MM-DD/`)
- **Process**: Exports all buckets sequentially, compresses with gzip, uploads to GCS
- **Memory optimized**: Processes one bucket at a time to minimize memory usage

### Environment Variables Required
```bash
GOOGLE_BACKUP_URI=gcs://your-bucket/backup/%Y-%m-%d/
GOOGLE_SERVICE_ACCOUNT='{"type":"service_account",...}'
```

### Manual Backup
```bash
# Run backup manually inside the container
docker exec iot-fetcher python python/src/main.py backup_influx
```

### Restore from Backup
```bash
# Download backup from GCS (on host machine)
gsutil cp gs://your-bucket/backup/2025-08-20/bucket_name_timestamp.tar.gz ./backup.tar.gz

# Extract backup locally
tar -xzf backup.tar.gz

# Copy extracted backup into container
docker cp ./backup_bucket_name iot-fetcher:/tmp/backup_bucket_name

# Restore inside the container using influx CLI to new bucket
docker exec iot-fetcher influx restore /tmp/backup_bucket_name -o your-org -b your-bucket-old

# Migrate from new restored bucket into old one
docker exec iot-fetcher influx query -o your-org -q 'from(bucket: "you-bucket-old") |> range(start: -5y, stop: now()) |> to(bucket: "your-bucket")'
```

## Make setup

  - `make build` Build the container an tag it
  - `make run` Start the application inside the docker container
  - `make run MODULE=<module-name>` Start a specifc module only
  - `make push` Build and push the container
  - `make dev` Rebuild container with pydebugger installed and start application with debugger on port `5678`
  - `make run-webui` Run the web UI locally using uvx
  - `make run-webui-dev` Run the web UI in development mode with uv
  - `make lock` Update uv.lock files after modifying pyproject.toml
  - `make install-uv` Install uv package manager if not already installed

## Dependency Management with uv

This project uses [uv](https://github.com/astral-sh/uv) for fast and reliable Python package management. Dependencies are defined in `pyproject.toml` files with version locks in `uv.lock` files.

### Adding a New Dependency - Complete Workflow

This section documents the **complete command-line workflow** for adding a new dependency (e.g., `package-xyz`).

#### Method 1: Using `uv add` (Recommended ⭐)

This is the **fastest and easiest** method. The `uv add` command handles everything automatically.

##### For the Main Backend (python/)

```bash
# 1. Navigate to the python directory
cd python

# 2. Add the dependency using uv
uv add package-xyz
```

**What happens when you run `uv add package-xyz`:**
1. ✅ Searches PyPI for the latest compatible version of `package-xyz`
2. ✅ Adds `package-xyz` to the `dependencies` list in `pyproject.toml`
3. ✅ Resolves all dependencies (including transitive dependencies)
4. ✅ Updates `uv.lock` with exact versions of all dependencies
5. ✅ Installs the package in your local environment (ready to use immediately)

```bash
# 3. Verify what changed
git diff pyproject.toml uv.lock

# You should see:
# - pyproject.toml: your new dependency added to the list
# - uv.lock: updated with exact versions of package-xyz and its dependencies

# 4. Test your code with the new dependency
uv run python src/main.py

# 5. Commit when ready!
git add pyproject.toml uv.lock
git commit -m "feat: add package-xyz dependency"
```

##### Version Specifiers

```bash
# Add latest version (default)
uv add package-xyz

# Add specific version
uv add "package-xyz==1.2.3"

# Add with minimum version
uv add "package-xyz>=1.2.0"

# Add with version range (recommended for libraries)
uv add "package-xyz>=1.2.0,<2.0.0"

# Add with compatible version (~= allows patch updates)
uv add "package-xyz~=1.2.0"  # Allows 1.2.x, not 1.3.0
```

##### For the Web UI (webui/)

```bash
# 1. Navigate to the webui directory
cd webui

# 2. Add the dependency
uv add package-xyz

# Same automatic behavior:
# - Updates pyproject.toml
# - Updates uv.lock
# - Installs locally

# 3. Test the webui
uv run python web.py

# 4. Verify and commit
git diff pyproject.toml uv.lock
git add pyproject.toml uv.lock
git commit -m "feat: add package-xyz to webui"
```

##### Real-World Example: Adding redis client

Let's say you want to add Redis support to the backend:

```bash
# Navigate to backend
cd python

# Add redis client
uv add redis

# Output you'll see:
# Resolved 5 packages in 234ms
# Downloaded 1 package in 89ms
# Installed 1 package in 12ms
#  + redis==5.0.1

# Verify the change
git diff pyproject.toml

# You'll see something like:
# dependencies = [
#     "requests==2.32.5",
#     "pybalboa==1.1.3",
# +   "redis==5.0.1",
#     ...
# ]

# The lock file will also update (many lines changed)
git diff uv.lock --stat
# uv.lock | 47 +++++++++++++++++++++++++++++++++++++++

# Test it immediately
uv run python -c "import redis; print('Redis imported successfully!')"

# Commit
git add pyproject.toml uv.lock
git commit -m "feat: add Redis client for caching support"
git push
```

##### Common `uv add` Options

```bash
# Add as a development dependency (not needed in production)
uv add --dev pytest

# Add as an optional dependency group
uv add --optional debug debugpy

# Add from a git repository
uv add git+https://github.com/user/repo.git

# Add from a local path (for development)
uv add --editable ./local-package

# Add multiple packages at once
uv add redis celery

# Upgrade an existing dependency to latest
uv add --upgrade package-xyz
```

##### Complete Workflow Summary (Method 1)

Here's the **complete end-to-end workflow** from adding a dependency to committing:

```bash
# Step 1: Navigate to the project directory
cd python  # or cd webui

# Step 2: Add the dependency (one command does everything!)
uv add package-xyz

# uv automatically:
# - Updates pyproject.toml
# - Updates uv.lock
# - Installs the package locally

# Step 3: Review what changed
git status
# You'll see:
#   modified:   pyproject.toml
#   modified:   uv.lock

git diff pyproject.toml
# Shows your new dependency added to the list

git diff uv.lock --stat
# Shows how many dependencies were affected in the lockfile

# Step 4: Test your changes locally
uv run python src/main.py  # or web.py for webui

# Or test in an interactive shell
uv run python
>>> import package_xyz
>>> # Test your new package
>>> exit()

# Step 5: Everything works? Commit!
git add pyproject.toml uv.lock
git commit -m "feat: add package-xyz dependency

Added package-xyz for [reason/use case]"
git push

# That's it! The Docker build will use your updated uv.lock
```

**Timeline:** The entire process takes ~30 seconds from `uv add` to `git push` ⚡

#### Method 2: Manual Edit (Alternative)

If you prefer to edit `pyproject.toml` manually:

```bash
# 1. Edit the pyproject.toml file
nano python/pyproject.toml  # or webui/pyproject.toml

# Add your dependency:
# [project]
# dependencies = [
#     "requests==2.32.5",
#     "package-xyz==1.2.3",  # <-- Add this line
#     ...
# ]

# 2. Update the lock file
cd python  # or cd webui
uv lock

# 3. Verify the changes
git diff pyproject.toml uv.lock

# Now you're ready to commit!
```

#### Using the Makefile

```bash
# After manually editing both pyproject.toml files, update all lock files at once:
make lock

# This runs:
# - cd python && uv lock
# - cd webui && uv lock
```

### Removing a Dependency

```bash
# Navigate to the appropriate directory
cd python  # or cd webui

# Remove the dependency
uv remove package-xyz

# This will:
# - Remove package-xyz from pyproject.toml
# - Update uv.lock without the package
```

### Installing Dependencies Locally

```bash
# For the main backend
cd python
uv sync

# For the webui
cd webui
uv sync

# For development (with editable install)
uv sync --all-extras
```

### Running Code with uv

```bash
# Run a script with all dependencies available
cd python
uv run python src/main.py

# Run the webui
cd webui
uv run python web.py

# Or use the Makefile
make run-webui-dev
```

### Installing uv Locally

If you need to install uv on your development machine:

```bash
# Using the Makefile
make install-uv

# Or install directly
curl -LsSf https://astral.sh/uv/install.sh | sh
```

### Understanding the Lock File

- **`uv.lock`** contains the exact versions of all dependencies (including transitive dependencies)
- **Commit both `pyproject.toml` and `uv.lock`** to version control
- **`uv sync --frozen`** in the Dockerfile ensures reproducible builds
- Lock files guarantee that everyone gets the same dependency versions

### Optional Dependencies (Debug Mode)

To add optional dependencies (like debugpy for debugging):

```bash
# Add as an optional dependency group
cd python
uv add --optional debug debugpy

# Or edit pyproject.toml manually:
# [project.optional-dependencies]
# debug = ["debugpy"]

# Then update the lock file
uv lock

# Install with optional dependencies
uv sync --extra debug
```

### Benefits of uv

- **10-100x faster** than pip for dependency resolution and installation
- **Reproducible builds** with lock files (`uv.lock`)
- **Better dependency resolution** with proper backtracking
- **Modern Python tooling** with improved caching and performance
- **Single command** to add/remove/update dependencies (`uv add`, `uv remove`, `uv lock`)

