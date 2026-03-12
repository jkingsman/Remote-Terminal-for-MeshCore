# RemoteTerm for MeshCore

## Important Rules

**NEVER make git commits.** A human must make all commits. You may stage files and prepare commit messages, but do not run `git commit`.

If instructed to "run all tests" or "get ready for a commit" or other summative, work ending directives, run:

```bash
./scripts/all_quality.sh
```

This runs all linting, formatting, type checking, tests, and builds for both backend and frontend sequentially. All checks must pass green.

## Overview

A web interface for MeshCore mesh radio networks. The backend connects to a MeshCore-compatible radio over Serial, TCP, or BLE and exposes REST/WebSocket APIs. The React frontend provides real-time messaging and radio configuration.

**For detailed component documentation, see these primary AGENTS.md files:**
- `app/AGENTS.md` - Backend (FastAPI, database, radio connection, packet decryption)
- `frontend/AGENTS.md` - Frontend (React, state management, WebSocket, components)

Ancillary AGENTS.md files which should generally not be reviewed unless specific work is being performed on those features include:
- `app/fanout/AGENTS_fanout.md` - Fanout bus architecture (MQTT, bots, webhooks, Apprise, SQS)
- `frontend/src/components/visualizer/AGENTS_packet_visualizer.md` - Packet visualizer (force-directed graph, advert-path identity, layout engine)

## Architecture Overview

```
┌─────────────────────────────────────────────────────────────────┐
│                         Frontend (React)                        │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌──────────────────┐ │
│  │ StatusBar│  │ Sidebar  │  │MessageList│  │  MessageInput   │ │
│  └──────────┘  └──────────┘  └──────────┘  └──────────────────┘ │
│  ┌────────────────────────────────────────────────────────────┐ │
│  │      CrackerPanel (global collapsible, WebGPU cracking)    │ │
│  └────────────────────────────────────────────────────────────┘ │
│                           │                                     │
│                    useWebSocket ←──── Real-time updates         │
│                           │                                     │
│                      api.ts ←──── REST API calls                │
└───────────────────────────┼─────────────────────────────────────┘
                            │ HTTP + WebSocket (/api/*)
┌───────────────────────────┼──────────────────────────────────────┐
│                      Backend (FastAPI)                           │
│  ┌──────────┐  ┌──────────┐  ┌──────────────┐  ┌────────────┐    │
│  │ Routers  │→ │ Services │→ │ Repositories │→ │  SQLite DB │    │
│  └──────────┘  └──────────┘  └──────────────┘  └────────────┘    │
│        ↓                         │                ┌───────────┐  │
│  ┌──────────────────────────┐    └──────────────→ │ WebSocket │  │
│  │ Radio runtime seam +     │                     │  Manager  │  │
│  │ RadioManager lifecycle   │                     └───────────┘  │
│  │ / event adapters         │                                    │
│  └──────────────────────────┘                                    │
└───────────────────────────┼──────────────────────────────────────┘
                            │ Serial / TCP / BLE
                     ┌──────┴──────┐
                     │ MeshCore    │
                     │   Radio     │
                     └─────────────┘
```

## Feature Priority

**Primary (must work correctly):**
- Sending and receiving direct messages and channel messages
- Accurate message display: correct ordering, deduplication, pagination/history loading, and real-time updates without data loss or duplicates
- Accurate ACK tracking, repeat/echo counting, and path display
- Historical packet decryption (recovering incoming messages using newly-added keys)
- Outgoing DMs are stored as plaintext by the send endpoint — no decryption needed

**Secondary:**
- Channel key cracker (WebGPU brute-force)
- Repeater management (telemetry, CLI commands, ACL)

**Tertiary (best-effort, quality-of-life):**
- Raw packet feed — a debug/observation tool ("radio aquarium"); interesting to watch or copy packets from, but not critical infrastructure
- Map view — visual display of node locations from advertisements
- Network visualizer — force-directed graph of mesh topology
- Fanout integrations (MQTT, bots, webhooks, Apprise, SQS) — see `app/fanout/AGENTS_fanout.md`
- Read state tracking / mark-all-read — convenience feature for unread badges; no need for transactional atomicity or race-condition hardening

## Error Handling Philosophy

**Background tasks** (WebSocket broadcasts, periodic sync, contact auto-loading, etc.) use fire-and-forget `asyncio.create_task`. Exceptions in these tasks are logged to the backend logs, which is sufficient for debugging. There is no need to track task references or add done-callbacks purely for error visibility. If there's a convenient way to bubble an error to the frontend (e.g., via `broadcast_error` for user-actionable problems), do so, but this is minor and best-effort.

Radio startup/setup is one place where that frontend bubbling is intentional: if post-connect setup hangs past its timeout, the backend both logs the failure and pushes a toast instructing the operator to reboot the radio and restart the server.

## Key Design Principles

1. **Store-and-serve**: Backend stores all packets even when no client is connected
2. **Parallel storage**: Messages stored both decrypted (when possible) and as raw packets
3. **Extended capacity**: Server stores contacts/channels beyond radio limits (~350 contacts, ~40 channels)
4. **Real-time updates**: WebSocket pushes events; REST for actions; optional MQTT forwarding
5. **Offline-capable**: Radio operates independently; server syncs when connected
6. **Auto-reconnect**: Background monitor detects disconnection and attempts reconnection

## Code Ethos

- Prefer fewer, stronger modules over many tiny wrapper files.
- Split code only when the new module owns a real invariant, workflow, or contract.
- Avoid "enterprise" indirection layers whose main job is forwarding, renaming, or prop bundling.
- For this repo, "locally dense but semantically obvious" is better than context scattered across many files.
- Use typed contracts at important boundaries such as API payloads, WebSocket events, and repository writes.
- Refactors should be behavior-preserving slices with tests around the moved seam, not aesthetic reshuffles.

## Intentional Security Design Decisions

The following are **deliberate design choices**, not bugs. They are documented in the README with appropriate warnings. Do not "fix" these or flag them as vulnerabilities.

1. **No CORS restrictions**: The backend allows all origins (`allow_origins=["*"]`). This lets users access their radio from any device/origin on their network without configuration hassle.
2. **Minimal optional access control only**: The app has no user accounts, sessions, authorization model, or per-feature permissions. Operators may optionally set `MESHCORE_BASIC_AUTH_USERNAME` and `MESHCORE_BASIC_AUTH_PASSWORD` for app-wide HTTP Basic auth, but this is only a coarse gate and still requires HTTPS plus a trusted network posture.
3. **Arbitrary bot code execution**: The bot system (`app/fanout/bot_exec.py`) executes user-provided Python via `exec()` with full `__builtins__`. This is intentional — bots are a power-user feature for automation. The README explicitly warns that anyone on the network can execute arbitrary code through this. Operators can set `MESHCORE_DISABLE_BOTS=true` to completely disable the bot system at startup — this skips all bot execution, returns 403 on bot settings updates, and shows a disabled message in the frontend.

## Intentional Packet Handling Decision

Raw packet handling uses two identities by design:
- **`id` (DB packet row ID)**: storage identity from payload-hash deduplication (path bytes are excluded), so repeated payloads share one stored raw-packet row.
- **`observation_id` (WebSocket only)**: realtime observation identity, unique per RF arrival, so path-diverse repeats are still visible in-session.

Frontend packet-feed consumers should treat `observation_id` as the dedup/render key, while `id` remains the storage reference.

Channel metadata updates may also fan out as `channel` WebSocket events (full `Channel` payload) so clients can reflect local-only channel state such as regional flood-scope overrides without a full refetch.

## Contact Advert Path Memory

To improve repeater disambiguation in the network visualizer, the backend stores recent unique advertisement paths per contact in a dedicated table (`contact_advert_paths`).

- This is independent of raw-packet payload deduplication.
- Paths are keyed per contact + path + hop count, with `heard_count`, `first_seen`, and `last_seen`.
- Only the N most recent unique paths are retained per contact (currently 10).
- See `frontend/src/components/AGENTS_packet_visualizer.md` § "Advert-Path Identity Hints" for how the visualizer consumes this data.

## Path Hash Modes

MeshCore firmware can encode path hops as 1-byte, 2-byte, or 3-byte identifiers.

- `path_hash_mode` values are `0` = 1-byte, `1` = 2-byte, `2` = 3-byte.
- `GET /api/radio/config` exposes both the current `path_hash_mode` and `path_hash_mode_supported`.
- `PATCH /api/radio/config` may update `path_hash_mode` only when the connected firmware supports it.
- Contacts persist `out_path_hash_mode` separately from `last_path` so contact sync and DM send paths can round-trip correctly even when hop bytes are ambiguous.
- Contacts may also persist an explicit routing override (`route_override_*`). When set, radio-bound operations use the override instead of the learned `last_path*`, but learned paths still keep updating from adverts.
- `path_len` in API payloads is always hop count, not byte count. The actual path byte length is `hop_count * hash_size`.

## Data Flow

### Incoming Messages

1. Radio receives raw bytes → `packet_processor.py` parses, decrypts, deduplicates, and stores in database (primary path via `RX_LOG_DATA` event)
2. `event_handlers.py` handles higher-level events (`CONTACT_MSG_RECV`, `ACK`) as a fallback/supplement
3. `broadcast_event()` in `websocket.py` fans out to both WebSocket clients and MQTT
4. Frontend `useWebSocket` receives → updates React state

### Outgoing Messages

1. User types message → clicks send
2. `api.sendChannelMessage()` → POST to backend
3. Backend route delegates to service-layer send orchestration, which acquires the radio lock and calls MeshCore commands
4. Message stored in database with `outgoing=true`
5. For direct messages: ACK tracked; for channel: repeat detection

### ACK and Repeat Detection

**Direct messages**: Expected ACK code is tracked. When ACK event arrives, message marked as acked.

**Channel messages**: Flood messages echo back through repeaters. Repeats are identified by the database UNIQUE constraint on `(type, conversation_key, text, sender_timestamp)` — when an INSERT hits a duplicate, `_handle_duplicate_message()` in `packet_processor.py` adds the new path and, for outgoing messages only, increments the ack count. Incoming repeats add path data but do not change the ack count. There is no timestamp-windowed matching; deduplication is exact-match only.

This message-layer echo/path handling is independent of raw-packet storage deduplication.

## Directory Structure

```
.
├── app/                    # FastAPI backend
│   ├── AGENTS.md           # Backend documentation
│   ├── main.py             # App entry, lifespan
│   ├── routers/            # API endpoints
│   ├── services/           # Shared backend orchestration/domain services, including radio_runtime access seam
│   ├── packet_processor.py # Raw packet pipeline, dedup, path handling
│   ├── repository/         # Database CRUD (contacts, channels, messages, raw_packets, settings, fanout)
│   ├── event_handlers.py   # Radio events
│   ├── decoder.py          # Packet decryption
│   ├── websocket.py        # Real-time broadcasts
│   └── fanout/             # Fanout bus: MQTT, bots, webhooks, Apprise, SQS (see fanout/AGENTS_fanout.md)
├── frontend/               # React frontend
│   ├── AGENTS.md           # Frontend documentation
│   ├── src/
│   │   ├── App.tsx         # Frontend composition entry (hooks → AppShell)
│   │   ├── api.ts          # REST client
│   │   ├── useWebSocket.ts # WebSocket hook
│   │   └── components/
│   │       ├── CrackerPanel.tsx  # WebGPU key cracking
│   │       ├── MapView.tsx       # Leaflet map showing node locations
│   │       └── ...
│   └── vite.config.ts
├── scripts/
│   ├── all_quality.sh      # Run all lint, format, typecheck, tests, build (sequential)
│   ├── collect_licenses.sh # Gather third-party license attributions
│   ├── e2e.sh              # End-to-end test runner
│   └── publish.sh          # Version bump, changelog, docker build & push
├── remoteterm.service      # Systemd unit file for production deployment
├── tests/                  # Backend tests (pytest)
├── data/                   # SQLite database (runtime)
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
npm ci
npm run dev    # http://localhost:5173, proxies /api to :8000
```

### Both Together (Development)

Terminal 1: `uv run uvicorn app.main:app --reload`
Terminal 2: `cd frontend && npm run dev`

### Production

In production, the FastAPI backend serves the compiled frontend. Build the frontend first:

```bash
cd frontend && npm ci && npm run build && cd ..
uv run uvicorn app.main:app --host 0.0.0.0 --port 8000
```

Access at `http://localhost:8000`. All API routes are prefixed with `/api`.

If `frontend/dist` (or `frontend/dist/index.html`) is missing, backend startup now logs an explicit error and continues serving API routes. In that case, frontend static routes are not mounted until a frontend build is present.

## Testing

### Backend (pytest)

```bash
PYTHONPATH=. uv run pytest tests/ -v
```

Key test files:
- `tests/test_decoder.py` - Channel + direct message decryption, key exchange
- `tests/test_keystore.py` - Ephemeral key store
- `tests/test_event_handlers.py` - ACK tracking, repeat detection
- `tests/test_packet_pipeline.py` - End-to-end packet processing
- `tests/test_api.py` - API endpoints, read state tracking
- `tests/test_migrations.py` - Database migration system
- `tests/test_frontend_static.py` - Frontend static route registration (missing `dist`/`index.html` handling)
- `tests/test_messages_search.py` - Message search, around endpoint, forward pagination
- `tests/test_rx_log_data.py` - on_rx_log_data event handler integration
- `tests/test_ack_tracking_wiring.py` - DM ACK tracking extraction and wiring
- `tests/test_radio_lifecycle_service.py` - Radio reconnect/setup orchestration helpers
- `tests/test_radio_commands_service.py` - Radio config/private-key service workflows
- `tests/test_health_mqtt_status.py` - Health endpoint MQTT status field
- `tests/test_community_mqtt.py` - Community MQTT publisher (JWT, packet format, hash, broadcast)
- `tests/test_radio_sync.py` - Radio sync, periodic tasks, and contact offload back to the radio
- `tests/test_real_crypto.py` - Real cryptographic operations
- `tests/test_disable_bots.py` - MESHCORE_DISABLE_BOTS=true feature

### Frontend (Vitest)

```bash
cd frontend
npm run test:run
```

### Before Completing Changes

**Always run `./scripts/all_quality.sh` before finishing any changes that have modified code or tests.** This runs all linting, formatting, type checking, tests, and builds sequentially, catching type mismatches, breaking changes, and compilation errors. This is not necessary for docs-only changes.

## API Summary

All endpoints are prefixed with `/api` (e.g., `/api/health`).

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/health` | Connection status, fanout statuses, bots_disabled flag |
| GET | `/api/radio/config` | Radio configuration, including `path_hash_mode`, `path_hash_mode_supported`, and whether adverts include current node location |
| PATCH | `/api/radio/config` | Update name, location, advert-location on/off, radio params, and `path_hash_mode` when supported |
| PUT | `/api/radio/private-key` | Import private key to radio |
| POST | `/api/radio/advertise` | Send advertisement |
| POST | `/api/radio/reboot` | Reboot radio or reconnect if disconnected |
| POST | `/api/radio/disconnect` | Disconnect from radio and pause automatic reconnect attempts |
| POST | `/api/radio/reconnect` | Manual radio reconnection |
| GET | `/api/contacts` | List contacts |
| GET | `/api/contacts/analytics` | Unified keyed-or-name contact analytics payload |
| GET | `/api/contacts/repeaters/advert-paths` | List recent unique advert paths for all contacts |
| POST | `/api/contacts` | Create contact (optionally trigger historical DM decrypt) |
| DELETE | `/api/contacts/{public_key}` | Delete contact |
| POST | `/api/contacts/{public_key}/mark-read` | Mark contact conversation as read |
| POST | `/api/contacts/{public_key}/command` | Send CLI command to repeater |
| POST | `/api/contacts/{public_key}/routing-override` | Set or clear a forced routing override |
| POST | `/api/contacts/{public_key}/trace` | Trace route to contact |
| POST | `/api/contacts/{public_key}/repeater/login` | Log in to a repeater |
| POST | `/api/contacts/{public_key}/repeater/status` | Fetch repeater status telemetry |
| POST | `/api/contacts/{public_key}/repeater/lpp-telemetry` | Fetch CayenneLPP sensor data |
| POST | `/api/contacts/{public_key}/repeater/neighbors` | Fetch repeater neighbors |
| POST | `/api/contacts/{public_key}/repeater/acl` | Fetch repeater ACL |
| POST | `/api/contacts/{public_key}/repeater/node-info` | Fetch repeater name, location, and clock via CLI |
| POST | `/api/contacts/{public_key}/repeater/radio-settings` | Fetch repeater radio config via CLI |
| POST | `/api/contacts/{public_key}/repeater/advert-intervals` | Fetch advert intervals |
| POST | `/api/contacts/{public_key}/repeater/owner-info` | Fetch owner info |

| GET | `/api/channels` | List channels |
| GET | `/api/channels/{key}/detail` | Comprehensive channel profile (message stats, top senders) |
| POST | `/api/channels` | Create channel |
| DELETE | `/api/channels/{key}` | Delete channel |
| POST | `/api/channels/{key}/flood-scope-override` | Set or clear a per-channel regional flood-scope override |
| POST | `/api/channels/{key}/mark-read` | Mark channel as read |
| GET | `/api/messages` | List with filters (`q`, `after`/`after_id` for forward pagination) |
| GET | `/api/messages/around/{id}` | Get messages around a specific message (for jump-to-message) |
| POST | `/api/messages/direct` | Send direct message |
| POST | `/api/messages/channel` | Send channel message |
| POST | `/api/messages/channel/{message_id}/resend` | Resend channel message (default: byte-perfect within 30s; `?new_timestamp=true`: fresh timestamp, no time limit, creates new message row) |
| GET | `/api/packets/undecrypted/count` | Count of undecrypted packets |
| POST | `/api/packets/decrypt/historical` | Decrypt stored packets |
| POST | `/api/packets/maintenance` | Delete old packets and vacuum |
| GET | `/api/read-state/unreads` | Server-computed unread counts, mentions, last message times |
| POST | `/api/read-state/mark-all-read` | Mark all conversations as read |
| GET | `/api/settings` | Get app settings |
| PATCH | `/api/settings` | Update app settings |
| POST | `/api/settings/favorites/toggle` | Toggle favorite status |
| POST | `/api/settings/blocked-keys/toggle` | Toggle blocked key |
| POST | `/api/settings/blocked-names/toggle` | Toggle blocked name |
| POST | `/api/settings/migrate` | One-time migration from frontend localStorage |
| GET | `/api/fanout` | List all fanout configs |
| POST | `/api/fanout` | Create new fanout config |
| PATCH | `/api/fanout/{id}` | Update fanout config (triggers module reload) |
| DELETE | `/api/fanout/{id}` | Delete fanout config (stops module) |
| GET | `/api/statistics` | Aggregated mesh network statistics |
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
- `4` - Sensor

### Channel Keys

- Stored as 32-character hex string (TEXT PRIMARY KEY)
- Hashtag channels: `SHA256("#name")[:16]` converted to hex
- Custom channels: User-provided or generated
- Channels may also persist `flood_scope_override`; when set, channel sends temporarily switch the radio flood scope to that value for the duration of the send, then restore the global app setting.

### Message Types

- `PRIV` - Direct messages
- `CHAN` - Channel messages
- Both use `conversation_key` (user pubkey for PRIV, channel key for CHAN)

### Read State Tracking

Read state (`last_read_at`) is tracked **server-side** for consistency across devices:
- Stored as Unix timestamp in `contacts.last_read_at` and `channels.last_read_at`
- Updated via `POST /api/contacts/{public_key}/mark-read` and `POST /api/channels/{key}/mark-read`
- Bulk update via `POST /api/read-state/mark-all-read`
- Aggregated counts via `GET /api/read-state/unreads` (server-side computation)

**State Tracking Keys (Frontend)**: Generated by `getStateKey()` for message times (sidebar sorting):
- Channels: `channel-{channel_key}`
- Contacts: `contact-{full-public-key}`

**Note:** These are NOT the same as `Message.conversation_key` (the database field).

### Fanout Bus (MQTT, Bots, Webhooks, Apprise, SQS)

All external integrations are managed through the fanout bus (`app/fanout/`). Each integration is a `FanoutModule` with scope-based event filtering, stored in the `fanout_configs` table and managed via `GET/POST/PATCH/DELETE /api/fanout`.

`broadcast_event()` in `websocket.py` dispatches `message` and `raw_packet` events to the fanout manager. See `app/fanout/AGENTS_fanout.md` for full architecture details.

Community MQTT forwards raw packets only. Its derived `path` field, when present on direct packets, is a comma-separated list of hop identifiers as reported by the packet format. Token width therefore varies with the packet's path hash mode; it is intentionally not a flat per-byte rendering.

### Server-Side Decryption

The server can decrypt packets using stored keys, both in real-time and for historical packets.

**Channel messages**: Decrypted automatically when a matching channel key is available.

**Direct messages**: Decrypted server-side using the private key exported from the radio on startup. This enables DM decryption even when the contact isn't loaded on the radio. The private key is stored in memory only (see `keystore.py`).

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
| `MESHCORE_TCP_HOST` | *(none)* | TCP host for radio (mutually exclusive with serial/BLE) |
| `MESHCORE_TCP_PORT` | `4000` | TCP port (used with `MESHCORE_TCP_HOST`) |
| `MESHCORE_BLE_ADDRESS` | *(none)* | BLE device address (mutually exclusive with serial/TCP) |
| `MESHCORE_BLE_PIN` | *(required with BLE)* | BLE PIN code |
| `MESHCORE_SERIAL_BAUDRATE` | `115200` | Serial baud rate |
| `MESHCORE_LOG_LEVEL` | `INFO` | Logging level (`DEBUG`/`INFO`/`WARNING`/`ERROR`) |
| `MESHCORE_DATABASE_PATH` | `data/meshcore.db` | SQLite database location |
| `MESHCORE_DISABLE_BOTS` | `false` | Disable bot system entirely (blocks execution and config) |
| `MESHCORE_BASIC_AUTH_USERNAME` | *(none)* | Optional app-wide HTTP Basic auth username; must be set together with `MESHCORE_BASIC_AUTH_PASSWORD` |
| `MESHCORE_BASIC_AUTH_PASSWORD` | *(none)* | Optional app-wide HTTP Basic auth password; must be set together with `MESHCORE_BASIC_AUTH_USERNAME` |
| `MESHCORE_ENABLE_MESSAGE_POLL_FALLBACK` | `false` | Switch the always-on radio audit task from hourly checks to aggressive 10-second polling; the audit checks both missed message drift and channel-slot cache drift |
| `MESHCORE_FORCE_CHANNEL_SLOT_RECONFIGURE` | `false` | Disable channel-slot reuse and force `set_channel(...)` before every channel send, even on serial/BLE |

**Note:** Runtime app settings are stored in the database (`app_settings` table), not environment variables. These include `max_radio_contacts`, `auto_decrypt_dm_on_advert`, `sidebar_sort_order`, `advert_interval`, `last_advert_time`, `favorites`, `last_message_times`, `flood_scope`, `blocked_keys`, and `blocked_names`. `max_radio_contacts` is the configured radio contact capacity baseline used by background maintenance: favorites reload first, non-favorite fill targets about 80% of that value, and full offload/reload triggers around 95% occupancy. They are configured via `GET/PATCH /api/settings`. MQTT, bot, webhook, Apprise, and SQS configs are stored in the `fanout_configs` table, managed via `/api/fanout`. If the radio's channel slots appear unstable or another client is mutating them underneath this app, operators can force the old always-reconfigure send path with `MESHCORE_FORCE_CHANNEL_SLOT_RECONFIGURE=true`.

Byte-perfect channel retries are user-triggered via `POST /api/messages/channel/{message_id}/resend` and are allowed for 30 seconds after the original send.

**Transport mutual exclusivity:** Only one of `MESHCORE_SERIAL_PORT`, `MESHCORE_TCP_HOST`, or `MESHCORE_BLE_ADDRESS` may be set. If none are set, serial auto-detection is used.

## Errata & Known Non-Issues

### `meshcore_py` advert parsing can crash on malformed/truncated RF log packets

The vendored MeshCore Python reader's `LOG_DATA` advert path assumes the decoded advert payload always contains at least 101 bytes of advert body and reads the flags byte with `pk_buf.read(1)[0]` without a length guard. If a malformed or truncated RF log frame slips through, `MessageReader.handle_rx()` can fail with `IndexError: index out of range` from `meshcore/reader.py` while parsing payload type `0x04` (advert).

This does not indicate database corruption or a message-store bug. It is a parser-hardening gap in `meshcore_py`: the reader does not fully mirror firmware-side packet/path validation before attempting advert decode. The practical effect is usually a one-off asyncio task failure for that packet while later packets continue processing normally.

### Channel-message dedup intentionally treats same-name/same-text/same-second channel sends as indistinguishable because they are

Channel message storage deduplicates on `(type, conversation_key, text, sender_timestamp)`. Reviewers often flag this as "missing sender identity," but for channel messages the stored `text` already includes the displayed sender label (for example `Alice: hello`). That means two different users only collide when they produce the same rendered sender name, the same body text, and the same sender timestamp.

In that case, RemoteTerm usually does not have enough information to distinguish "two independent same-name sends" from "one message observed again as an echo/repeat." Without a reliable sender identity at ingest, treating those packets as the same message is an accepted limitation of the observable data model, not an obvious correctness bug.
