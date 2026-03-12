# Backend AGENTS.md

This document is the backend working guide for agents and developers.
Keep it aligned with `app/` source files and router behavior.

## Stack

- FastAPI
- aiosqlite
- Pydantic
- MeshCore Python library (`meshcore` from PyPI)
- PyCryptodome

## Code Ethos

- Prefer strong domain modules over layers of pass-through helpers.
- Split code when the new module owns real policy, not just a nicer name.
- Avoid wrapper services around globals unless they materially improve testability or reduce coupling.
- Keep workflows locally understandable; do not scatter one reasoning unit across several files without a clear contract.
- Typed write/read contracts are preferred over loose dict-shaped repository inputs.

## Backend Map

```text
app/
├── main.py              # App startup/lifespan, router registration, static frontend mounting
├── config.py            # Env-driven runtime settings
├── database.py          # SQLite connection + base schema + migration runner
├── migrations.py        # Schema migrations (SQLite user_version)
├── models.py            # Pydantic request/response models and typed write contracts (for example ContactUpsert)
├── repository/          # Data access layer (contacts, channels, messages, raw_packets, settings, fanout)
├── services/            # Shared orchestration/domain services
│   ├── messages.py              # Shared message creation, dedup, ACK application
│   ├── message_send.py          # Direct send, channel send, resend workflows
│   ├── dm_ack_tracker.py        # Pending DM ACK state
│   ├── contact_reconciliation.py # Prefix-claim, sender-key backfill, name-history wiring
│   ├── radio_lifecycle.py       # Post-connect setup and reconnect/setup helpers
│   ├── radio_commands.py        # Radio config/private-key command workflows
│   └── radio_runtime.py         # Router/dependency seam over the global RadioManager
├── radio.py             # RadioManager transport/session state + lock management
├── radio_sync.py        # Polling, sync, periodic advertisement loop
├── decoder.py           # Packet parsing/decryption
├── packet_processor.py  # Raw packet pipeline, dedup, path handling
├── event_handlers.py    # MeshCore event subscriptions and ACK tracking
├── events.py            # Typed WS event payload serialization
├── websocket.py         # WS manager + broadcast helpers
├── security.py          # Optional app-wide HTTP Basic auth middleware for HTTP + WS
├── fanout/              # Fanout bus: MQTT, bots, webhooks, Apprise, SQS (see fanout/AGENTS_fanout.md)
├── dependencies.py      # Shared FastAPI dependency providers
├── path_utils.py        # Path hex rendering and hop-width helpers
├── region_scope.py      # Normalize/validate regional flood-scope values
├── keystore.py          # Ephemeral private/public key storage for DM decryption
├── frontend_static.py   # Mount/serve built frontend (production)
└── routers/
    ├── health.py
    ├── radio.py
    ├── contacts.py
    ├── channels.py
    ├── messages.py
    ├── packets.py
    ├── read_state.py
    ├── settings.py
    ├── fanout.py
    ├── repeaters.py
    ├── statistics.py
    └── ws.py
```

## Core Runtime Flows

### Incoming data

1. Radio emits events.
2. `on_rx_log_data` stores raw packet and tries decrypt/pipeline handling.
3. Shared message-domain services create/update `messages` and shape WS payloads.
4. `CONTACT_MSG_RECV` is a fallback DM path when packet pipeline cannot decrypt.

### Outgoing messages

1. Send endpoints in `routers/messages.py` validate requests and delegate to `services/message_send.py`.
2. Service-layer send workflows call MeshCore commands, persist outgoing messages, and wire ACK tracking.
3. Endpoint broadcasts WS `message` event so all live clients update.
4. ACK/repeat updates arrive later as `message_acked` events.
5. Channel resend (`POST /messages/channel/{id}/resend`) strips the sender name prefix by exact match against the current radio name. This assumes the radio name hasn't changed between the original send and the resend. Name changes require an explicit radio config update and are rare, but the `new_timestamp=true` resend path has no time window, so a mismatch is possible if the name was changed between the original send and a later resend.

### Connection lifecycle

- `RadioManager.start_connection_monitor()` checks health every 5s.
- `RadioManager.post_connect_setup()` delegates to `services/radio_lifecycle.py`.
- Routers, startup/lifespan code, fanout helpers, and `radio_sync.py` should reach radio state through `services/radio_runtime.py`, not by importing `app.radio.radio_manager` directly.
- Shared reconnect/setup helpers in `services/radio_lifecycle.py` are used by startup, the monitor, and manual reconnect/reboot flows before broadcasting healthy state.
- Setup still includes handler registration, key export, time sync, contact/channel sync, and advertisement tasks. The message-poll task always starts: by default it runs as a low-frequency hourly audit, and `MESHCORE_ENABLE_MESSAGE_POLL_FALLBACK=true` switches it to aggressive 10-second polling.
- Post-connect setup is timeout-bounded. If initial radio offload/setup hangs too long, the backend logs the failure and broadcasts an `error` toast telling the operator to reboot the radio and restart the server.

## Important Behaviors

### Multibyte routing

- Packet `path_len` values are hop counts, not byte counts.
- Hop width comes from the packet or radio `path_hash_mode`: `0` = 1-byte, `1` = 2-byte, `2` = 3-byte.
- Channel slot count comes from firmware-reported `DEVICE_INFO.max_channels`; do not hardcode `40` when scanning/offloading channel slots.
- Contacts persist `out_path_hash_mode` in the database so contact sync and outbound DM routing reuse the exact stored mode instead of inferring from path bytes.
- Contacts may also persist `route_override_path`, `route_override_len`, and `route_override_hash_mode`. `Contact.to_radio_dict()` gives these override fields precedence over learned `last_path*`, while advert processing still updates the learned route for telemetry/fallback.
- `contact_advert_paths` identity is `(public_key, path_hex, path_len)` because the same hex bytes can represent different routes at different hop widths.

### Read/unread state

- Server is source of truth (`contacts.last_read_at`, `channels.last_read_at`).
- `GET /api/read-state/unreads` returns counts, mention flags, and `last_message_times`.

### Echo/repeat dedup

- Message uniqueness: `(type, conversation_key, text, sender_timestamp)`.
- Duplicate insert is treated as an echo/repeat: the new path (if any) is appended, and the ACK count is incremented **only for outgoing messages**. Incoming repeats add path data but do not change the ACK count.

### Raw packet dedup policy

- Raw packet storage deduplicates by payload hash (`RawPacketRepository.create`), excluding routing/path bytes.
- Stored packet `id` is therefore a payload identity, not a per-arrival identity.
- Realtime raw-packet WS broadcasts include `observation_id` (unique per RF arrival) in addition to `id`.
- Frontend packet-feed features should key/dedupe by `observation_id`; use `id` only as the storage reference.
- Message-layer repeat handling (`_handle_duplicate_message` + `MessageRepository.add_path`) is separate from raw-packet storage dedup.

### Contact sync throttle

- `sync_recent_contacts_to_radio()` sets `_last_contact_sync = now` before the sync completes.
- This is intentional: if sync fails, the next attempt is still throttled to prevent a retry-storm against a flaky radio. Contacts will resync on the next scheduled cycle or on reconnect.

### Periodic advertisement

- Controlled by `app_settings.advert_interval` (seconds).
- `0` means disabled.
- Last send time tracked in `app_settings.last_advert_time`.

### Fanout bus

- All external integrations (MQTT, bots, webhooks, Apprise, SQS) are managed through the fanout bus (`app/fanout/`).
- Configs stored in `fanout_configs` table, managed via `GET/POST/PATCH/DELETE /api/fanout`.
- `broadcast_event()` in `websocket.py` dispatches to the fanout manager for `message` and `raw_packet` events.
- Each integration is a `FanoutModule` with scope-based filtering.
- Community MQTT publishes raw packets only, but its derived `path` field for direct packets is emitted as comma-separated hop identifiers, not flat path bytes.
- See `app/fanout/AGENTS_fanout.md` for full architecture details.

## API Surface (all under `/api`)

### Health
- `GET /health`

### Radio
- `GET /radio/config` — includes `path_hash_mode`, `path_hash_mode_supported`, and advert-location on/off
- `PATCH /radio/config` — may update `path_hash_mode` (`0..2`) when firmware supports it
- `PUT /radio/private-key`
- `POST /radio/advertise`
- `POST /radio/disconnect`
- `POST /radio/reboot`
- `POST /radio/reconnect`

### Contacts
- `GET /contacts`
- `GET /contacts/analytics` — unified keyed-or-name analytics payload
- `GET /contacts/repeaters/advert-paths` — recent advert paths for all contacts
- `POST /contacts`
- `DELETE /contacts/{public_key}`
- `POST /contacts/{public_key}/mark-read`
- `POST /contacts/{public_key}/command`
- `POST /contacts/{public_key}/routing-override`
- `POST /contacts/{public_key}/trace`
- `POST /contacts/{public_key}/repeater/login`
- `POST /contacts/{public_key}/repeater/status`
- `POST /contacts/{public_key}/repeater/lpp-telemetry`
- `POST /contacts/{public_key}/repeater/neighbors`
- `POST /contacts/{public_key}/repeater/acl`
- `POST /contacts/{public_key}/repeater/node-info`
- `POST /contacts/{public_key}/repeater/radio-settings`
- `POST /contacts/{public_key}/repeater/advert-intervals`
- `POST /contacts/{public_key}/repeater/owner-info`

### Channels
- `GET /channels`
- `GET /channels/{key}/detail`
- `POST /channels`
- `DELETE /channels/{key}`
- `POST /channels/{key}/flood-scope-override`
- `POST /channels/{key}/mark-read`

### Messages
- `GET /messages` — list with filters; supports `q` (full-text search), `after`/`after_id` (forward cursor)
- `GET /messages/around/{message_id}` — context messages around a target (for jump-to-message navigation)
- `POST /messages/direct`
- `POST /messages/channel`
- `POST /messages/channel/{message_id}/resend`

### Packets
- `GET /packets/undecrypted/count`
- `POST /packets/decrypt/historical`
- `POST /packets/maintenance`

### Read state
- `GET /read-state/unreads`
- `POST /read-state/mark-all-read`

### Settings
- `GET /settings`
- `PATCH /settings`
- `POST /settings/favorites/toggle`
- `POST /settings/blocked-keys/toggle`
- `POST /settings/blocked-names/toggle`
- `POST /settings/migrate`

### Fanout
- `GET /fanout` — list all fanout configs
- `POST /fanout` — create new fanout config
- `PATCH /fanout/{id}` — update fanout config (triggers module reload)
- `DELETE /fanout/{id}` — delete fanout config (stops module)

### Statistics
- `GET /statistics` — aggregated mesh network stats (entity counts, message/packet splits, activity windows, busiest channels)

### WebSocket
- `WS /ws`

## WebSocket Events

- `health` — radio connection status (broadcast on change, personal on connect)
- `contact` — single contact upsert (from advertisements and radio sync)
- `message` — new message (channel or DM, from packet processor or send endpoints)
- `message_acked` — ACK/echo update for existing message (ack count + paths)
- `raw_packet` — every incoming RF packet (for real-time packet feed UI)
- `contact_deleted` — contact removed from database (payload: `{ public_key }`)
- `channel` — single channel upsert/update (payload: full `Channel`)
- `channel_deleted` — channel removed from database (payload: `{ key }`)
- `error` — toast notification (reconnect failure, missing private key, stuck radio startup, etc.)
- `success` — toast notification (historical decrypt complete, etc.)

Backend WS sends go through typed serialization in `events.py`. Initial WS connect sends `health` only. Contacts/channels are loaded by REST.
Client sends `"ping"` text; server replies `{"type":"pong"}`.

## Data Model Notes

Main tables:
- `contacts` (includes `first_seen` for contact age tracking and `out_path_hash_mode` for route round-tripping)
- `channels`
  Includes optional `flood_scope_override` for channel-specific regional sends.
- `messages` (includes `sender_name`, `sender_key` for per-contact channel message attribution)
- `raw_packets`
- `contact_advert_paths` (recent unique advertisement paths per contact, keyed by contact + path bytes + hop count)
- `contact_name_history` (tracks name changes over time)
- `app_settings`

Repository writes should prefer typed models such as `ContactUpsert` over ad hoc dict payloads when adding or updating schema-coupled data.

`max_radio_contacts` is the configured radio contact capacity baseline. Favorites reload first, the app refills non-favorite working-set contacts to about 80% of that capacity, and periodic offload triggers once occupancy reaches about 95%.

`app_settings` fields in active model:
- `max_radio_contacts`
- `favorites`
- `auto_decrypt_dm_on_advert`
- `sidebar_sort_order`
- `last_message_times`
- `preferences_migrated`
- `advert_interval`
- `last_advert_time`
- `flood_scope`
- `blocked_keys`, `blocked_names`

Note: MQTT, community MQTT, and bot configs were migrated to the `fanout_configs` table (migrations 36-38).

## Security Posture (intentional)

- No authn/authz.
- No CORS restriction (`*`).
- Bot code executes user-provided Python via `exec()`.

These are product decisions for trusted-network deployments; do not flag as accidental vulnerabilities.

## Testing

Run backend tests:

```bash
PYTHONPATH=. uv run pytest tests/ -v
```

Test suites:

```text
tests/
├── conftest.py                 # Shared fixtures
├── test_ack_tracking_wiring.py # DM ACK tracking extraction and wiring
├── test_api.py                 # REST endpoint integration tests
├── test_bot.py                 # Bot execution and sandboxing
├── test_channels_router.py     # Channels router endpoints
├── test_channel_sender_backfill.py # Sender-key backfill uniqueness rules for channel messages
├── test_config.py              # Configuration validation
├── test_contact_reconciliation_service.py # Prefix/contact reconciliation service helpers
├── test_contacts_router.py     # Contacts router endpoints
├── test_decoder.py             # Packet parsing/decryption
├── test_disable_bots.py        # MESHCORE_DISABLE_BOTS=true feature
├── test_echo_dedup.py          # Echo/repeat deduplication (incl. concurrent)
├── test_fanout.py              # Fanout bus CRUD, scope matching, manager dispatch
├── test_fanout_integration.py  # Fanout integration tests
├── test_fanout_hitlist.py      # Fanout-related hitlist regression tests
├── test_event_handlers.py      # ACK tracking, event registration, cleanup
├── test_frontend_static.py     # Frontend static file serving
├── test_health_mqtt_status.py  # Health endpoint MQTT status field
├── test_http_quality.py        # Cache-control / gzip / basic-auth HTTP quality checks
├── test_key_normalization.py   # Public key normalization
├── test_keystore.py            # Ephemeral keystore
├── test_message_pagination.py  # Cursor-based message pagination
├── test_message_prefix_claim.py # Message prefix claim logic
├── test_migrations.py          # Schema migration system
├── test_community_mqtt.py      # Community MQTT publisher (JWT, packet format, hash, broadcast)
├── test_mqtt.py                # MQTT publisher topic routing and lifecycle
├── test_packet_pipeline.py     # End-to-end packet processing
├── test_packets_router.py      # Packets router endpoints (decrypt, maintenance)
├── test_radio.py               # RadioManager, serial detection
├── test_radio_commands_service.py # Radio config/private-key service workflows
├── test_radio_lifecycle_service.py # Reconnect/setup orchestration helpers
├── test_radio_runtime_service.py # radio_runtime seam behavior and helpers
├── test_real_crypto.py         # Real cryptographic operations
├── test_radio_operation.py     # radio_operation() context manager
├── test_radio_router.py        # Radio router endpoints
├── test_radio_sync.py          # Polling, sync, advertisement
├── test_repeater_routes.py     # Repeater command/telemetry/trace + granular pane endpoints
├── test_repository.py          # Data access layer
├── test_rx_log_data.py         # on_rx_log_data event handler integration
├── test_messages_search.py      # Message search, around, forward pagination
├── test_block_lists.py          # Blocked keys/names filtering
├── test_security.py            # Optional Basic Auth middleware / config behavior
├── test_send_messages.py       # Outgoing messages, bot triggers, concurrent sends
├── test_settings_router.py     # Settings endpoints, advert validation
├── test_statistics.py          # Statistics aggregation
├── test_main_startup.py        # App startup and lifespan
├── test_path_utils.py          # Path hex rendering helpers
├── test_websocket.py           # WS manager broadcast/cleanup
└── test_websocket_route.py     # WS endpoint lifecycle
```

## Errata & Known Non-Issues

### Sender timestamps are 1-second resolution (protocol constraint)

The MeshCore radio protocol encodes `sender_timestamp` as a 4-byte little-endian integer (Unix seconds). This is a firmware-level wire format — the radio, the Python library (`commands/messaging.py`), and the decoder (`decoder.py`) all read/write exactly 4 bytes. Millisecond Unix timestamps would overflow 4 bytes, so higher resolution is not possible without a firmware change.

**Consequence:** The dedup index `(type, conversation_key, text, COALESCE(sender_timestamp, 0))` operates at 1-second granularity. Sending identical text to the same conversation twice within one second will hit the UNIQUE constraint on the second insert, returning HTTP 500 *after* the radio has already transmitted. The message is sent over the air but not stored in the database. Do not attempt to fix this by switching to millisecond timestamps — it will break echo dedup (the echo's 4-byte timestamp won't match the stored value) and overflow `to_bytes(4, "little")`.

### Outgoing DM echoes remain undecrypted

When our own outgoing DM is heard back via `RX_LOG_DATA` (self-echo, loopback), `_process_direct_message` passes `our_public_key=None` for the outgoing direction, disabling the outbound hash check in the decoder. The decoder's inbound check (`src_hash == their_first_byte`) fails because the source is us, not the contact — so decryption returns `None`. This is by design: outgoing DMs are stored directly by the send endpoint, so no message is lost.

### Infinite setup retry on connection monitor

When `post_connect_setup()` fails (e.g. `export_and_store_private_key` raises `RuntimeError` because the radio didn't respond), `_setup_complete` is never set to `True`. The connection monitor sees `connected and not setup_complete` and retries every 5 seconds — indefinitely. This is intentional: the radio may be rebooting, waking from sleep, or otherwise temporarily unresponsive. We keep retrying so that setup completes automatically once the radio becomes available, without requiring manual intervention.

### DELETE channel returns 200 for non-existent keys

`DELETE /api/channels/{key}` returns `{"status": "ok"}` even if the key didn't exist. This is intentional — the postcondition is "channel doesn't exist," which is satisfied regardless of whether it existed before. No 404 needed.

### Contact lat/lon 0.0 vs NULL

MeshCore uses `0.0` as the sentinel for "no GPS coordinates" (see `models.py` `to_radio_dict`). The upsert SQL uses `COALESCE(excluded.lat, contacts.lat)`, which preserves existing values when the new value is `NULL` — but `0.0` is not `NULL`, so it overwrites previously valid coordinates. This is intentional: we always want the most recent location data. If a device stops broadcasting GPS, the old coordinates are presumably stale/wrong, so overwriting with "not available" (`0.0`) is the correct behavior.

## Editing Checklist

When changing backend behavior:
1. Update/add router and repository tests.
2. Confirm WS event contracts when payload shape changes.
3. Run `PYTHONPATH=. uv run pytest tests/ -v`.
4. If API contract changed, update frontend types and AGENTS docs.
