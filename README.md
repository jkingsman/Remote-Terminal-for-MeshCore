# RemoteTerm for MeshCore

Web interface for MeshCore mesh radio networks. Attach your radio over serial, and then you can:

* Cache all received packets, decrypting as you gain keys
* Send and receive DMs and GroupTexts
* Passively monitor as many contacts and channels as you want; radio limitations are irrelevant as all packets get hoovered up, then decrypted serverside
* Use your radio remotely over your network or away from home over a VPN
* Look for hashtag room names by brute forcing channel keys of GroupTexts you don't have the keys for yet

## This is a personal toolkit, and not optimized for general consumption! This is entirely vibecoded slop and I make no warranty of fitness for any purpose.

For real, this code is bad and totally LLM generated. If you insist on extending it, there are three `CLAUDE.md` fils you should have your LLM read in `./`, `./frontend`, and `./app`.

## Requirements

- Python 3.10+
- Node.js 18+
- UV (Python package manager): `curl -LsSf https://astral.sh/uv/install.sh | sh`
- MeshCore-compatible radio connected via USB serial

## Quick Start

### Backend

```bash
# Install dependencies
uv sync

# Run (auto-detects serial port)
uv run uvicorn app.main:app --reload

# Or specify port explicitly
MESHCORE_SERIAL_PORT=/dev/cu.usbserial-0001 uv run uvicorn app.main:app --reload
```

Backend runs at http://localhost:8000

### Frontend

```bash
cd frontend

# Install dependencies
npm install

# Development server (proxies API to localhost:8000)
npm run dev

# Production build
npm run build
```

Dev server runs at http://localhost:5173

## Production Deployment

For production, the FastAPI backend serves the compiled frontend directly.

```bash
# 1. Install Python dependencies
uv sync

# 2. Build frontend
cd frontend
npm install
npm run build
cd ..

# 3. Run server
uv run uvicorn app.main:app --host 0.0.0.0 --port 8000

# Or with explicit serial port
MESHCORE_SERIAL_PORT=/dev/ttyUSB0 uv run uvicorn app.main:app --host 0.0.0.0 --port 8000
```

Access the app at http://localhost:8000 (or your server's IP/hostname).

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `MESHCORE_SERIAL_PORT` | (auto-detect) | Serial port path |
| `MESHCORE_SERIAL_BAUDRATE` | 115200 | Baud rate |
| `MESHCORE_LOG_LEVEL` | INFO | DEBUG, INFO, WARNING, ERROR |
| `MESHCORE_DATABASE_PATH` | data/meshcore.db | SQLite database path |
| `MESHCORE_MAX_RADIO_CONTACTS` | 200 | Max recent contacts to keep on radio for DM ACKs |

## Testing

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

# Run tests once
npm run test:run

# Run tests in watch mode
npm test
```

## API Docs

With the backend running, visit http://localhost:8000/docs for interactive API documentation.
