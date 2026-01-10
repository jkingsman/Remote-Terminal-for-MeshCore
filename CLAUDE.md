# RemoteTerm for MeshCore

A web interface for MeshCore mesh radio networks. The backend connects to a MeshCore-compatible radio over serial and exposes REST/WebSocket APIs. The React frontend provides real-time messaging and radio configuration.

**For detailed component documentation, see:**
- `app/CLAUDE.md` - Backend (FastAPI, database, radio connection, packet decryption)
- `frontend/CLAUDE.md` - Frontend (React, state management, WebSocket, components)

## Architecture Overview

```
┌─────────────────────────────────────────────────────────────────┐
│                         Frontend (React)                         │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────────────┐ │
│  │ StatusBar│  │ Sidebar  │  │MessageList│  │  MessageInput   │ │
│  └──────────┘  └──────────┘  └──────────┘  └──────────────────┘ │
│  ┌────────────────────────────────────────────────────────────┐ │
│  │      CrackerPanel (global collapsible, WebGPU cracking)    │ │
│  └────────────────────────────────────────────────────────────┘ │
│                           │                                      │
│                    useWebSocket ←──── Real-time updates          │
│                           │                                      │
│                      api.ts ←──── REST API calls                 │
└───────────────────────────┼──────────────────────────────────────┘
                            │ HTTP + WebSocket (/api/*)
┌───────────────────────────┼──────────────────────────────────────┐
│                      Backend (FastAPI)                           │
│  ┌──────────┐  ┌──────────────┐  ┌────────────┐  ┌───────────┐  │
│  │ Routers  │→ │ Repositories │→ │  SQLite DB │  │ WebSocket │  │
│  └──────────┘  └──────────────┘  └────────────┘  │  Manager  │  │
│        ↓                                          └───────────┘  │
│  ┌──────────────────────────────────────────────────────────┐   │
│  │              RadioManager + Event Handlers               │   │
│  └──────────────────────────────────────────────────────────┘   │
└───────────────────────────┼──────────────────────────────────────┘
                            │ Serial
                     ┌──────┴──────┐
                     │ MeshCore    │
                     │   Radio     │
                     └─────────────┘
```

## Key Design Principles

1. **Store-and-serve**: Backend stores all packets even when no client is connected
2. **Parallel storage**: Messages stored both decrypted (when possible) and as raw packets
3. **Extended capacity**: Server stores contacts/channels beyond radio limits (~350 contacts, ~40 channels)
4. **Real-time updates**: WebSocket pushes events; REST for actions
5. **Offline-capable**: Radio operates independently; server syncs when connected
6. **Auto-reconnect**: Background monitor detects disconnection and attempts reconnection

## Data Flow

### Incoming Messages

1. Radio receives message → MeshCore library emits event
2. `event_handlers.py` catches event → stores in database
3. `ws_manager` broadcasts to connected clients
4. Frontend `useWebSocket` receives → updates React state

### Outgoing Messages

1. User types message → clicks send
2. `api.sendChannelMessage()` → POST to backend
3. Backend calls `radio_manager.meshcore.commands.send_chan_msg()`
4. Message stored in database with `outgoing=true`
5. For direct messages: ACK tracked; for channel: repeat detection

### ACK and Repeat Detection

**Direct messages**: Expected ACK code is tracked. When ACK event arrives, message marked as acked.

**Channel messages**: Flood messages echo back. The decoder identifies repeats by matching (channel_idx, text_hash, timestamp ±5s) and marks the original as "acked".

## Directory Structure

```
.
├── app/                    # FastAPI backend
│   ├── CLAUDE.md           # Backend documentation
│   ├── main.py             # App entry, lifespan
│   ├── routers/            # API endpoints
│   ├── repository.py       # Database CRUD
│   ├── event_handlers.py   # Radio events
│   ├── decoder.py          # Packet decryption
│   └── websocket.py        # Real-time broadcasts
├── frontend/               # React frontend
│   ├── CLAUDE.md           # Frontend documentation
│   ├── src/
│   │   ├── App.tsx         # Main component
│   │   ├── api.ts          # REST client
│   │   ├── useWebSocket.ts # WebSocket hook
│   │   └── components/
│   │       ├── CrackerPanel.tsx  # WebGPU key cracking
│   │       └── ...
│   └── vite.config.ts
├── references/meshcore_py/ # MeshCore Python library
├── tests/                  # Backend tests (pytest)
├── data/                   # SQLite database (runtime)
├── integration_test.html   # Browser-based API tests
└── pyproject.toml          # Python dependencies
```

## Development Setup

### Backend

```bash
# Install dependencies
uv sync

# Run server (auto-detects radio)
uv run uvicorn app.main:app --reload

# Or specify port
MESHCORE_SERIAL_PORT=/dev/cu.usbserial-0001 uv run uvicorn app.main:app --reload
```

### Frontend

```bash
cd frontend
npm install
npm run dev    # http://localhost:5173, proxies /api to :8000
```

### Both Together (Development)

Terminal 1: `uv run uvicorn app.main:app --reload`
Terminal 2: `cd frontend && npm run dev`

### Production

In production, the FastAPI backend serves the compiled frontend:

```bash
cd frontend && npm run build && cd ..
uv run uvicorn app.main:app --host 0.0.0.0 --port 8000
```

Access at `http://localhost:8000`. All API routes are prefixed with `/api`.

## Testing

### Backend (pytest)

```bash
PYTHONPATH=. uv run pytest tests/ -v
```

Key test files:
- `tests/test_decoder.py` - Channel + direct message decryption, key exchange
- `tests/test_keystore.py` - Ephemeral key store
- `tests/test_event_handlers.py` - ACK tracking, repeat detection
- `tests/test_api.py` - API endpoints

### Frontend (Vitest)

```bash
cd frontend
npm run test:run
```

### Integration Tests

Open `integration_test.html` in a browser with the backend running.

## API Summary

All endpoints are prefixed with `/api` (e.g., `/api/health`).

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/health` | Connection status |
| GET | `/api/radio/config` | Radio configuration |
| PATCH | `/api/radio/config` | Update name, location, radio params |
| POST | `/api/radio/advertise` | Send advertisement |
| POST | `/api/radio/reconnect` | Manual radio reconnection |
| POST | `/api/radio/enable-server-decryption` | Export private key, enable decryption |
| GET | `/api/radio/decryption-status` | Check if decryption enabled |
| GET | `/api/contacts` | List contacts |
| POST | `/api/contacts/sync` | Pull from radio |
| POST | `/api/contacts/{key}/telemetry` | Request telemetry from repeater |
| POST | `/api/contacts/{key}/command` | Send CLI command to repeater |
| GET | `/api/channels` | List channels |
| POST | `/api/channels` | Create channel |
| GET | `/api/messages` | List with filters |
| POST | `/api/messages/direct` | Send direct message |
| POST | `/api/messages/channel` | Send channel message |
| POST | `/api/packets/decrypt/historical` | Decrypt stored packets |
| GET | `/api/settings` | Get app settings |
| PATCH | `/api/settings` | Update app settings |
| WS | `/api/ws` | Real-time updates |

## Key Concepts

### Contact Public Keys

- Full key: 64-character hex string
- Prefix: 12-character hex (used for matching)
- Lookups use `LIKE 'prefix%'` for matching

### Contact Types

- `0` - Unknown
- `1` - Client (regular node)
- `2` - Repeater
- `3` - Room

### Channel Keys

- Stored as 32-character hex string (TEXT PRIMARY KEY)
- Hashtag channels: `SHA256("#name")[:16]` converted to hex
- Custom channels: User-provided or generated

### Message Types

- `PRIV` - Direct messages
- `CHAN` - Channel messages
- Both use `conversation_key` (user pubkey for PRIV, channel key for CHAN)

### State Tracking Keys (Frontend)

Generated by `getStateKey()` for unread tracking and message times:
- Channels: `channel-{channel_key}`
- Contacts: `contact-{12-char-pubkey-prefix}`

**Note:** These are NOT the same as `Message.conversation_key` (the database field).

### Server-Side Decryption

The server can decrypt historical packets if given the necessary keys:

**Channel messages**: Decrypted automatically using stored channel keys.

**Direct messages**: Requires the node's private key, which must be:
1. Exported from radio via `POST /radio/enable-server-decryption`
2. Stored **only in memory** (never persisted to disk)
3. Re-exported after every server restart

This allows decrypting messages from contacts whose public keys were learned after the message was received.

## MeshCore Library

The `meshcore_py` library provides radio communication. Key patterns:

```python
# Connection
mc = await MeshCore.create_serial(port="/dev/ttyUSB0")

# Commands
await mc.commands.send_msg(dst, msg)
await mc.commands.send_chan_msg(channel_idx, msg)
await mc.commands.get_contacts()
await mc.commands.set_channel(idx, name, key)

# Events
mc.subscribe(EventType.CONTACT_MSG_RECV, handler)
mc.subscribe(EventType.CHANNEL_MSG_RECV, handler)
mc.subscribe(EventType.ACK, handler)
```

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `MESHCORE_SERIAL_PORT` | auto-detect | Serial port for radio |
| `MESHCORE_DATABASE_PATH` | `data/meshcore.db` | SQLite database location |
| `MESHCORE_MAX_RADIO_CONTACTS` | `200` | Max recent contacts to keep on radio for DM ACKs |
