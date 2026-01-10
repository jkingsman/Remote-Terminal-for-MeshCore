# RemoteTerm for MeshCore

Web interface for MeshCore mesh radio networks. Attach your radio over serial, and then you can:

* Cache all received packets, decrypting as you gain keys
* Send and receive DMs and GroupTexts
* Passively monitor as many contacts and channels as you want; radio limitations are irrelevant as all packets get hoovered up, then decrypted serverside
* Use your radio remotely over your network or away from home over a VPN
* Look for hashtag room names by brute forcing channel keys of GroupTexts you don't have the keys for yet

This app is fully trustful, with no endpoint protection, and is intended to be run on a protected, private network. **Do not run this application on the open internet unless you want strangers sending traffic as you!**

![Screenshot of the application's web interface](screenshot.png)

## This is a personal toolkit, and not optimized for general consumption! This is entirely vibecoded slop and I make no warranty of fitness for any purpose.

For real, this code is bad and totally LLM generated. If you insist on extending it, there are three `CLAUDE.md` fils you should have your LLM read in `./CLAUDE.md`, `./frontend/CLAUDE.md`, and `./app/CLAUDE.md`.

## Requirements

- Python 3.10+
- Node.js 18+ (only needed for frontend development)
- UV (Python package manager): `curl -LsSf https://astral.sh/uv/install.sh | sh`
- MeshCore-compatible radio connected via USB serial

## Quick Start

### Docker

```bash
# basic invocation without TLS
docker run -d \
  --device=/dev/ttyUSB0 \
  -v remoteterm-data:/app/data \
  -p 8000:8000 \
  jkingsman/remoteterm-meshcore:latest

# optional; if you want roomname discover to work: WebGPU requires a cert, even snakeoil, to function
openssl req -x509 -newkey rsa:4096 -keyout key.pem -out cert.pem -days 365 -nodes -subj '/CN=localhost'

docker run -d \
  --device=/dev/ttyUSB0 \
  -v remoteterm-data:/app/data \
  -v $(pwd)/cert.pem:/app/cert.pem:ro \
  -v $(pwd)/key.pem:/app/key.pem:ro \
  -p 8000:8000 \
  jkingsman/remoteterm-meshcore:latest \
  uv run uvicorn app.main:app --host 0.0.0.0 --port 8000 --ssl-keyfile=/app/key.pem --ssl-certfile=/app/cert.pem
```

### Backend

```bash
# Clone repo
https://github.com/jkingsman/Remote-Terminal-for-MeshCore.git
cd Remote-Terminal-for-MeshCore

# Install dependencies
uv sync

# Run (auto-detects serial port)
uv run uvicorn app.main:app --reload

# Or specify port explicitly
MESHCORE_SERIAL_PORT=/dev/cu.usbserial-0001 uv run uvicorn app.main:app --reload
```

Backend runs at http://localhost:8000, and will preferentially serve from `./frontend/dist` for the GUI. If you want to do GUI development, see below and use http://localhost:5173 for the GUI.

See the `HTTPS` section below if you're serving this anywhere but localhost and need the GPU cracker to function.

**If you just want to run this as-is (all commits push a distribution-ready frontend build), you can just run the backend and access the GUI from there; no need to boot the frontend**

### Frontend Dev

```bash
cd frontend

# Install dependencies
npm install

# Development server (proxies API to localhost:8000)
npm run dev

# Production build; writes out to dist/
npm run build
```

Dev server runs the frontend at http://localhost:5173

## Production Deployment

For production, the FastAPI backend serves the compiled frontend directly.

```bash
# 1. Install Python dependencies
uv sync

# 2. Build frontend if you've made changes
cd frontend
npm install
npm run build
cd ..

# 3. Run server
uv run uvicorn app.main:app --host 0.0.0.0 --port 8000

# Or with explicit serial port
MESHCORE_SERIAL_PORT=/dev/ttyUSB0 uv run uvicorn app.main:app --host 0.0.0.0 --port 8000
```

Access the app at http://localhost:8000 (or your server's IP/hostname), which will serve static files from `./frontend/dist`.

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `MESHCORE_SERIAL_PORT` | (auto-detect) | Serial port path |
| `MESHCORE_SERIAL_BAUDRATE` | 115200 | Baud rate |
| `MESHCORE_LOG_LEVEL` | INFO | DEBUG, INFO, WARNING, ERROR |
| `MESHCORE_DATABASE_PATH` | data/meshcore.db | SQLite database path |
| `MESHCORE_MAX_RADIO_CONTACTS` | 200 | Max recent contacts to keep on radio for DM ACKs |

## Other Details...

<details>
<summary>HTTPS (Required for WebGPU Cracking)</summary>

WebGPU requires a secure context. To use the channel key cracker when not serving on `localhost` (which is always permitted GPU access), serve over HTTPS:

```bash
# Generate self-signed cert
openssl req -x509 -newkey rsa:4096 -keyout key.pem -out cert.pem -days 365 -nodes -subj '/CN=localhost'

# Run with SSL
uv run uvicorn app.main:app --host 0.0.0.0 --port 8000 --ssl-keyfile=key.pem --ssl-certfile=cert.pem
```

Accept the browser security warning on first visit. For locally-trusted certs without warnings, use [mkcert](https://github.com/FiloSottile/mkcert).
</details>

<details>
<summary>Systemd Service (Linux)</summary>

To run as a system service:

```bash
# 1. Create service user (with home directory for uv cache)
sudo useradd -r -m -s /bin/false remoteterm

# 2. Install to /opt/remoteterm
sudo mkdir -p /opt/remoteterm
sudo cp -r . /opt/remoteterm/
sudo chown -R remoteterm:remoteterm /opt/remoteterm

# 3. Create virtualenv and install deps (may need to install uv for the user with curl -LsSf https://astral.sh/uv/install.sh | sudo -u remoteterm sh)
cd /opt/remoteterm
sudo -u remoteterm uv venv
sudo -u remoteterm uv sync

# 4. Build frontend
cd /opt/remoteterm/frontend
sudo -u remoteterm npm install
sudo -u remoteterm npm run build

# 5. Install and start service
sudo cp /opt/remoteterm/remoteterm.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable remoteterm
sudo systemctl start remoteterm

# Check status
sudo systemctl status remoteterm
sudo journalctl -u remoteterm -f
```

Edit `/etc/systemd/system/remoteterm.service` to set `MESHCORE_SERIAL_PORT` if auto-detection doesn't work.
</details>

<details>
<summary>Testing</summary>

### Backend (pytest)

```bash
# Install test dependencies
uv sync --extra test

# Run all tests
PYTHONPATH=. uv run pytest tests/ -v

# Run specific test file
PYTHONPATH=. uv run pytest tests/test_decoder.py -v
```

### Frontend (Vitest)

```bash
cd frontend
npm install

# Run tests once
npm run test:run

# Run tests in watch mode
npm test
```
</details>

<details>
<summary>Docker Build</summary>

Build and run with Docker, passing through your serial device:

```bash
# Build the image
docker build -t remoteterm-meshcore .

# Run with serial passthrough (replace /dev/ttyUSB0 with your device)
docker run -d \
  --name remoteterm \
  --device=/dev/ttyUSB0 \
  -e MESHCORE_SERIAL_PORT=/dev/ttyUSB0 \
  -v remoteterm-data:/app/data \
  -p 8000:8000 \
  remoteterm-meshcore

# View logs
docker logs -f remoteterm
```

**Finding your serial device:**

```bash
# Linux
ls /dev/ttyUSB* /dev/ttyACM*

# macOS
ls /dev/cu.usbserial-* /dev/cu.usbmodem*
```

**Persistent data:** The `-v remoteterm-data:/app/data` flag creates a named volume for the SQLite database, so your messages and contacts persist across container restarts.

**HTTPS with Docker:** For WebGPU cracking support over non-localhost connections:

```bash
openssl req -x509 -newkey rsa:4096 -keyout key.pem -out cert.pem -days 365 -nodes -subj '/CN=localhost'

docker run -d \
  --name remoteterm \
  --device=/dev/ttyUSB0 \
  -e MESHCORE_SERIAL_PORT=/dev/ttyUSB0 \
  -v remoteterm-data:/app/data \
  -v $(pwd)/cert.pem:/app/cert.pem:ro \
  -v $(pwd)/key.pem:/app/key.pem:ro \
  -p 8000:8000 \
  remoteterm-meshcore \
  uv run uvicorn app.main:app --host 0.0.0.0 --port 8000 --ssl-keyfile=/app/key.pem --ssl-certfile=/app/cert.pem
```
</details>

## API Docs

With the backend running, visit http://localhost:8000/docs for interactive API documentation.
