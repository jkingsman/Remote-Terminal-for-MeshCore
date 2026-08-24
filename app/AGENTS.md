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
├── api_docs.py          # OpenAPI description/tag metadata and docs route registration
├── config.py            # Env-driven runtime settings
├── channel_constants.py # Public/default channel constants shared across sync/send logic
├── database.py          # SQLite connection + base schema + migration runner
├── migrations/          # Schema migrations (SQLite user_version, per-version modules)
├── models.py            # Pydantic request/response models and typed write contracts (for example ContactUpsert)
├── version_info.py      # Unified version/build metadata resolution for debug + startup surfaces
├── repository/          # Data access layer (contacts, channels, messages, raw_packets, settings, fanout, push_subscriptions, repeater_telemetry, contact_telemetry)
├── services/            # Shared orchestration/domain services
│   ├── messages.py              # Shared message creation, dedup, ACK application
│   ├── message_send.py          # Direct send, channel send, resend workflows
│   ├── dm_ingest.py             # Shared direct-message ingest / dedup seam for packet + fallback paths
│   ├── dm_ack_apply.py          # Shared DM ACK application over pending/buffered ACK state
│   ├── dm_ack_tracker.py        # Pending DM ACK state
│   ├── contact_reconciliation.py # Prefix-claim, sender-key backfill, name-history wiring
│   ├── flood_scope.py           # Firmware-version-aware flood-scope set/clear command seam
│   ├── radio_lifecycle.py       # Post-connect setup and reconnect/setup helpers
│   ├── radio_commands.py        # Radio config/private-key command workflows
│   ├── radio_stats.py           # In-memory local radio stats sampling and noise-floor history
│   └── radio_runtime.py         # Router/dependency seam over the global RadioManager
├── radio.py             # RadioManager transport/session state + lock management
├── radio_sync.py        # Polling, sync, periodic advertisement loop
├── decoder.py           # Packet parsing/decryption
├── packet_processor.py  # Raw packet pipeline, dedup, path handling
├── event_handlers.py    # MeshCore event subscriptions and ACK tracking
├── events.py            # Typed WS event payload serialization
├── websocket.py         # WS manager + broadcast helpers
├── security.py          # Optional app-wide HTTP Basic auth middleware for HTTP + WS
├── push/                # Web Push notification subsystem
│   ├── vapid.py                 # VAPID key generation, storage, caching
│   ├── send.py                  # pywebpush wrapper (async via thread executor)
│   └── manager.py               # Push dispatch: filter, build payload, concurrent send
├── bots/                # Bots workspace engine: triggers, cron, feeds, i18n (see bots/AGENTS_bots.md)
├── fanout/              # Fanout bus: MQTT, bots, webhooks, Apprise, SQS (see fanout/AGENTS_fanout.md)
├── telemetry_interval.py # Shared telemetry interval math for tracked-repeater scheduler
├── path_utils.py        # Path hex rendering and hop-width helpers
├── region_scope.py      # Normalize/validate regional flood-scope values
├── region_resolver.py   # Recompute transport codes per known region to name a packet's region
├── keystore.py          # Ephemeral private/public key storage for DM decryption
├── frontend_static.py   # Mount/serve built frontend (production)
└── routers/
    ├── health.py
    ├── debug.py
    ├── radio.py
    ├── contacts.py
    ├── channels.py
    ├── messages.py
    ├── packets.py
    ├── read_state.py
    ├── rooms.py
    ├── server_control.py   # Shared helpers for repeater/room CLI flows (not an APIRouter)
    ├── settings.py
    ├── fanout.py
    ├── repeaters.py
    ├── statistics.py
    ├── push.py
    └── ws.py
```

## Core Runtime Flows

### Incoming data

1. Radio emits events.
2. `on_rx_log_data` stores raw packet and tries decrypt/pipeline handling.
3. Shared message-domain services create/update `messages` and shape WS payloads.
4. Direct-message storage is centralized in `services/dm_ingest.py`; packet-processor DMs and `CONTACT_MSG_RECV` fallback events both route through that seam.

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
- Setup still includes handler registration, key export, time sync, contact/channel sync, and advertisement tasks. The message-poll task always starts: by default it runs as a low-frequency hourly audit, and `MESHCORE_ENABLE_MESSAGE_POLL_FALLBACK=true` switches it to aggressive 10-second polling. That audit checks both missed-radio-message drift and channel-slot cache drift; cache mismatches are logged, toasted, and the send-slot cache is reset.
- Post-connect setup is timeout-bounded. If initial radio offload/setup hangs too long, the backend logs the failure and broadcasts an `error` toast telling the operator to reboot the radio and restart the server.

## Important Behaviors

### Multibyte routing

- Packet `path_len` values are hop counts, not byte counts.
- Hop width comes from the packet or radio `path_hash_mode`: `0` = 1-byte, `1` = 2-byte, `2` = 3-byte.
- Channel slot count comes from firmware-reported `DEVICE_INFO.max_channels`; do not hardcode `40` when scanning/offloading channel slots.
- Channel sends use a session-local LRU slot cache after startup channel offload clears the radio. Repeated sends to the same channel reuse the loaded slot; new channels fill free slots up to the discovered channel capacity, then evict the least recently used cached channel.
- TCP radios do not reuse cached slot contents. For TCP, channel sends still force `set_channel(...)` before every send because this backend does not have exclusive device access.
- `MESHCORE_FORCE_CHANNEL_SLOT_RECONFIGURE=true` disables slot reuse on all transports and forces the old always-`set_channel(...)` behavior before every channel send.
- Contacts persist canonical direct-route fields (`direct_path`, `direct_path_len`, `direct_path_hash_mode`) so contact sync and outbound DM routing reuse the exact stored hop width instead of inferring from path bytes.
- Direct-route sources are limited to radio contact sync (`out_path`) and PATH/path-discovery updates. This mirrors firmware `onContactPathRecv(...)`, which replaces `ContactInfo.out_path` when a new returned path is heard.
- `route_override_path`, `route_override_len`, and `route_override_hash_mode` take precedence over the learned direct route for radio-bound sends.
- Advertisement paths are stored only in `contact_advert_paths` for analytics/visualization. They are not part of `Contact.to_radio_dict()` or DM route selection.
- `contact_advert_paths` identity is `(public_key, path_hex, path_len)` because the same hex bytes can represent different routes at different hop widths.

### Read/unread state

- Server is source of truth (`contacts.last_read_at`, `channels.last_read_at`).
- `GET /api/read-state/unreads` returns counts, mention flags, `last_message_times`, `last_read_ats`, and `first_unread_ids`.
- `first_unread_ids` maps stateKey -> id of the oldest unread message, so the client can anchor the unread divider (and jump to it) without paging back through history. It is computed with `ROW_NUMBER() OVER (PARTITION BY type, conversation_key ORDER BY received_at, id)` — deliberately not `MIN(received_at)` with a bare id, because sender timestamps are whole seconds and same-second ties are routine, and not `MIN(id)`, because historical decryption inserts old messages with new ids.

### DM ingest + ACKs

- `services/dm_ingest.py` is the one place that should decide fallback-context resolution, DM dedup/reconciliation, and packet-linked vs. content-based storage behavior.
- `CONTACT_MSG_RECV` is a fallback path, not a parallel source of truth. If you change DM storage behavior, trace both `event_handlers.py` and `packet_processor.py`.
- DM ACK tracking is an in-memory pending/buffered map in `services/dm_ack_tracker.py`, with periodic expiry from `radio_sync.py`.
- Outgoing DMs send once inline, store/broadcast immediately after the first successful `MSG_SENT`, then may retry up to 2 more times in the background only when the initial `MSG_SENT` result includes an expected ACK code and the message remains unacked.
- DM retry timing follows the firmware-provided `suggested_timeout` from `PACKET_MSG_SENT`; do not replace it with a fixed app timeout unless you intentionally want more aggressive duplicate-prone retries.
- Direct-message send behavior is intended to emulate `meshcore_py.commands.send_msg_with_retry(...)` when the radio provides an expected ACK code: stage the effective contact route on the radio, send, wait for ACK, and on the final retry force flood via `reset_path(...)`.
- Non-final DM attempts use the contact's effective route (`override > direct > flood`). The final retry is intentionally sent as flood even when a routing override exists.
- DM ACK state is terminal on first ACK. Retry attempts may register multiple expected ACK codes for the same message, but sibling pending codes are cleared once one ACK wins so a DM should not accrue multiple delivery confirmations from retries.
- ACKs are delivery state, not routing state. Bundled ACKs inside PATH packets still satisfy pending DM sends, but ACK history does not feed contact route learning.
- DM ACKs are matched from two independent radio emissions, so confirmation does not depend on the radio surfacing a host control frame: (1) the `EventType.ACK`/`SEND_CONFIRMED` host frame via `event_handlers.on_ack`, and (2) the raw RF packet itself via `packet_processor.process_raw_packet`. The packet processor extracts ACK codes both from PATH-return packets (flood replies, ACK embedded in `extra`) and from standalone `PayloadType.ACK` packets (direct replies, 4-byte cleartext payload), feeding both into `apply_dm_ack_code`. This matters for companion firmwares (e.g. pyMC over TCP) that do not reliably emit a separate host ACK frame for direct-routed replies.

### Server login route escalation

`prepare_authenticated_contact_connection` (`routers/server_control.py`, shared by repeater and room login) sends one login over the contact's effective route. If that draws **no reply at all**, it calls `reset_path(...)` and retries exactly once as flood.

This is intentionally *more* than the reference implementations do — do not "correct" it back to single-shot:
- Firmware `BaseChatMesh::sendLogin` picks flood only when `out_path_len == OUT_PATH_UNKNOWN` and never retries; `CMD_SEND_LOGIN` calls it once.
- `meshcore_py` has `send_msg_with_retry` (with `flood_after` + `reset_path`) but no login equivalent — `send_login`/`send_login_sync` are single-shot.
- Firmware only clears a stale path when the *host* asks (`CMD_RESET_PATH`); client-side path learning is otherwise passive via `onContactPathRecv`.

Escalating is still correct because the **server** side treats an inbound flood as its cue to relearn the return path (`simple_repeater`/`simple_room_server`: `if (is_flood) client->out_path_len = OUT_PATH_UNKNOWN`). A flood login is therefore what repairs a broken route in both directions, and login gates the whole repeater dashboard.

Escalation is bounded to one extra attempt and only fires when:
- the first attempt **timed out**. `LOGIN_FAILED` means the server heard us and refused, so the route is fine and retrying only hammers it with bad credentials; a send error is a local radio problem a different route will not fix.
- the contact was **not already on flood** (`effective_route_source != "flood"`), since the retry would otherwise be byte-identical.

The retry deliberately does not re-run `_ensure_on_radio` — re-adding the contact would restore the route just cleared. `reset_path` clears the route on the radio only; the stored contact route is untouched, so the next `add_contact` re-stages it. That mirrors the DM retry and keeps one bad login from discarding a route that may be fine.

### Echo/repeat dedup

- Channel message uniqueness (`idx_messages_dedup_null_safe`): `(type, conversation_key, text, COALESCE(sender_timestamp, 0))` where `type = 'CHAN'`.
- Incoming PRIV message uniqueness (`idx_messages_incoming_priv_dedup`): `(type, conversation_key, text, COALESCE(sender_timestamp, 0), COALESCE(sender_key, ''))` where `type = 'PRIV' AND outgoing = 0` — `sender_key` was added in migration 056 to distinguish room-server posts from different senders in the same second.
- Duplicate insert is treated as an echo/repeat: the new path (if any) is appended, and the ACK count is incremented only for outgoing channel messages. Incoming direct messages with the same dedup identity also collapse onto one stored row, with later observations merging path data instead of creating a second DM.

### MCMP text compression

- MCMP (`app/compression/`) is a Python port of meshcore-open's arithmetic text compressor (n-gram model in `models/model-en-ru.json`), wire-compatible with it and dimapanov/mesh-compressor. Compressed bodies ride as ordinary message text behind an `mcmp2:` (v2) or `mcmp3:` (v3 metadata container) prefix.
- **Decode on ingest:** `decode_incoming_body()` is called at every message ingest route — `create_message_from_decrypted` and `create_fallback_channel_message` (channels, raw-RF + get_msg) and `_store_direct_message` (DMs) — so the DB/search/bots see plaintext and content dedup stays consistent across routes. It never raises; a non-MCMP or malformed body is stored unchanged.
- **Encode on send:** opt-in per conversation via `contacts.mcmp_enabled` / `channels.mcmp_enabled` (migration 066); the transport is selectable via `mcmp_version` (2 or 3; migration 067, default 2). `message_send.py` compresses only the transmitted body — v2 uses "only if smaller", v3 always wraps its container (carrying the message timestamp). The stored/broadcast text stays plaintext. `encode_outbound(text, version=, timestamp=)` is deterministic (v3 uses the sender timestamp) so DM retries and channel resends send identical bytes.
- **Not implemented:** v3 Ed25519 signing (needs firmware) — v3 is sent unsigned and signed v3 from peers is decoded with the signature skipped, never verified. Decode is lenient (no re-encode verify) to tolerate cross-libm float drift.

### Region scope decoding (transport codes)

- `ROUTE_TYPE_TRANSPORT_FLOOD`/`ROUTE_TYPE_TRANSPORT_DIRECT` packets carry a 4-byte transport-code block; `parse_packet_envelope` exposes it as `transport_codes = (code_1, code_2)` (little-endian uint16s; `code_2` is reserved/0).
- `code_1` is a keyed MAC over the payload, not a stable per-region id: `code = HMAC-SHA256(SHA256("#" + region_name)[:16], payload_type || payload)[:2]` (firmware `TransportKeyStore.cpp`; reserved values `0x0000`/`0xFFFF` are nudged to `0x0001`/`0xFFFE`). There is **no** reverse lookup table — to name a packet's region you recompute the code per candidate region and check for a match (`app/region_resolver.py`).
- Candidate region names come from `app_settings.known_regions` (user-editable, seeded by migration 063 from `flood_scope` + channel `flood_scope_override`).
- Channel messages persist `messages.transport_code` (uint16, NULL = unscoped plain flood) and `messages.region` (resolved name, NULL = scoped but no list match) at ingest, so the chat region badge survives raw-packet purge. The packet inspector (`GET /packets/{id}` and the `raw_packet` WS broadcast) resolves region on the fly against the current list since it still holds the raw payload.

### Region-scope adoption stats (`region_scope_24h`)

`GET /statistics` reports regional flood-scope uptake as two views with different denominators that intentionally will not agree:

- **Traffic** (`bucket_region_scope` in `path_utils.py`) counts flood-routed (`route_type` 0/1) GroupText packets across all channels, including undecryptable ones. Zero-hop/direct sends are excluded because firmware reaches them through the non-transport `sendZeroHop`/`sendDirect` overloads and they can never carry transport codes.
- **Senders** (`StatisticsRepository._region_scope_senders_24h`) counts distinct senders with at least one scoped message. Attribution requires decryption, so it only covers channels we hold keys for — narrower, but self-validating (a decrypted packet is provably not a corrupt capture) and immune to one chatty node skewing the result. Identity is `sender_key` falling back to `sender_name`; scoping reads `messages.transport_code`, falling back to the linked raw packet for rows stored before region tagging existed.

`false_positive_floor` exists because corrupt RF captures land in `raw_packets` with effectively random headers and a share of them claim `TRANSPORT_FLOOD`. That garbage spreads near-uniformly across payload-type buckets, so it is measured directly from payload types the protocol does not define (`0x0C`/`0x0D`/`0x0E`) and averaged per bucket. **A `scoped_messages` count at or below the floor is not evidence of adoption**; surface the two together and never show the percentage alone. Do not "fix" the floor by removing it — without it the metric reads several times higher than reality.

Both traffic buckets come from one 24h raw-packet scan (`_packet_shape_24h`) shared with `path_hash_width_24h`, so adding region stats costs no extra query or parse pass.

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
- `broadcast_event()` in `websocket.py` dispatches to the fanout manager for `message`, `raw_packet`, and `contact` events.
- `on_message` and `on_raw` are scope-gated. `on_contact`, `on_telemetry`, and `on_health` are dispatched to all modules unconditionally (modules filter internally).
- Repeater telemetry broadcasts are emitted after `RepeaterTelemetryRepository.record()` in both `radio_sync.py` (auto-collect) and `routers/repeaters.py` (manual fetch). Contact LPP telemetry is similarly recorded to `ContactTelemetryRepository` and dispatched to fanout.
- The telemetry collection loop in `radio_sync.py` is unified: it iterates over both `tracked_telemetry_repeaters` and `tracked_telemetry_contacts`, dispatching to `_collect_repeater_telemetry` (type 2) or `_collect_contact_telemetry` (others). The daily check ceiling uses the combined count.
- The 60-second radio stats sampling loop in `radio_stats.py` dispatches an enriched health snapshot (radio identity + full stats) to all fanout modules after each sample.
- Community MQTT publishes raw packets only, but its derived `path` field for direct packets is emitted as comma-separated hop identifiers, not flat path bytes.
- See `app/fanout/AGENTS_fanout.md` for full architecture details and event payload shapes.

### Web Push notifications

Web Push is a standalone subsystem in `app/push/`, separate from the fanout module system. It sends browser push notifications for incoming messages even when the tab is closed.

- **Not a fanout module** — Web Push manages per-browser subscriptions (N browsers, each with its own endpoint and delivery state), unlike fanout which is one-config-to-one-destination.
- **VAPID keys**: auto-generated P-256 key pair on first startup, stored in `app_settings.vapid_private_key` / `vapid_public_key`. Cached in-module by `app/push/vapid.py`.
- **VAPID subject**: the JWT `sub` claim comes from `get_vapid_claims()` in `app/push/vapid.py`, configurable via `MESHCORE_VAPID_SUBJECT` (default `mailto:noreply@meshcore.local`). Apple's APNs rejects `.local` subjects with `403 BadJwtToken`, so iOS/Safari deployments must set a real `mailto:`/`https:` contact.
- **Dispatch**: `broadcast_event()` in `websocket.py` fires `push_manager.dispatch_message(data)` alongside fanout for `message` events. The manager checks the global `app_settings.push_conversations` list, then sends to all currently registered subscriptions via `pywebpush` (run in a thread executor).
- **Stale cleanup**: HTTP 404/410 from the push service triggers immediate subscription deletion.
- **Subscriptions stored** in `push_subscriptions` table with `UNIQUE(endpoint)` for upsert semantics.
- Requires HTTPS (self-signed OK) and outbound internet to reach browser push services.

## API Surface (all under `/api`)

### Health
- `GET /health`

### Debug
- `GET /debug` — support snapshot with recent logs, live radio probe, slot/contact audits, and version/git info

### Radio
- `GET /radio/config` — includes `path_hash_mode`, `path_hash_mode_supported`, advert-location on/off, and `multi_acks_enabled`
- `PATCH /radio/config` — may update `path_hash_mode` (`0..2`) when firmware supports it, and `multi_acks_enabled`
- `GET /radio/private-key` — export in-memory private key as hex (requires `MESHCORE_ENABLE_LOCAL_PRIVATE_KEY_EXPORT=true`)
- `PUT /radio/private-key`
- `POST /radio/advertise` — manual advert send; request body may set `mode` to `flood` or `zero_hop` (defaults to `flood`)
- `POST /radio/discover` — short mesh discovery sweep for nearby repeaters/sensors
- `POST /radio/discover-regions` — sweep nearby repeaters via the guest anon regions request; aggregates flood-allowed region names into a deduped union for merging into `known_regions` (direct-routed, so only in-range repeaters answer; optional `public_keys`, else recent repeaters)
- `POST /radio/trace` — send a multi-hop trace loop through known repeaters and back to the local radio
- `POST /radio/disconnect`
- `POST /radio/reboot`
- `POST /radio/reconnect`

### Contacts
- `GET /contacts`
- `GET /contacts/analytics` — unified keyed-or-name analytics payload
- `GET /contacts/repeaters/advert-paths` — recent advert paths for all contacts
- `POST /contacts`
- `POST /contacts/bulk-delete`
- `DELETE /contacts/{public_key}`
- `POST /contacts/{public_key}/mark-read`
- `POST /contacts/{public_key}/command`
- `POST /contacts/{public_key}/routing-override`
- `POST /contacts/{public_key}/trace`
- `POST /contacts/{public_key}/path-discovery` — discover forward/return paths, persist the learned direct route, and sync it back to the radio best-effort
- `POST /contacts/{public_key}/repeater/login` — one attempt on the effective route, then one flood retry on timeout
- `POST /contacts/{public_key}/repeater/status`
- `POST /contacts/{public_key}/repeater/lpp-telemetry`
- `POST /contacts/{public_key}/repeater/neighbors`
- `POST /contacts/{public_key}/repeater/acl`
- `POST /contacts/{public_key}/repeater/node-info`
- `POST /contacts/{public_key}/repeater/radio-settings`
- `POST /contacts/{public_key}/repeater/regions` — CLI region hierarchy, falling back to the guest anon flood-allowed names (`source`: `cli` or `anon`)
- `POST /contacts/{public_key}/repeater/advert-intervals`
- `POST /contacts/{public_key}/repeater/owner-info`
- `GET /contacts/{public_key}/repeater/telemetry-history` — stored telemetry history for a repeater (read-only, no radio access)
- `POST /contacts/{public_key}/telemetry` — on-demand CayenneLPP telemetry from any contact (persists in `contact_telemetry_history`)
- `GET /contacts/{public_key}/telemetry-history` — stored LPP telemetry history for a contact (read-only)
- `POST /contacts/{public_key}/room/login` — one attempt on the effective route, then one flood retry on timeout. Body `{password?, use_stored_credential?}`: `password` is three-state (`null`/absent = guest unless `use_stored_credential`, `""` = guest, else the password); `use_stored_credential=true` logs in with the room's server-side stored credential and never returns it.
- `POST /contacts/{public_key}/room/status`
- `POST /contacts/{public_key}/room/lpp-telemetry`
- `POST /contacts/{public_key}/room/acl`
- `GET /contacts/{public_key}/room/poll` — room poll/credential status; booleans only, never the stored credential
- `PUT /contacts/{public_key}/room/poll` — set stored credential (`credential_action` keep/set/clear; `credential=""` stores a guest login) and/or the background poll schedule; enabling polling requires a stored credential
- `DELETE /contacts/{public_key}/room/poll` — remove the stored credential and disable polling

The background room poller (`app/radio_sync.py` `_room_poll_loop`, started post-connect) periodically logs in to each subscribed room with its stored credential so the server enqueues its message delta; the existing drain/dedup pipeline captures it. Reliability rests on the incoming-message dedup (re-pulling an overlapping delta is a no-op), a durable per-room subscription (`room_poll_subscriptions`), the shared radio lock (`radio_operation(blocking=False, suspend_auto_fetch=True)` — busy = skip), and exponential backoff; an explicit `LOGIN_FAILED` disables the subscription. MeshCore has no message cursor, so this is "keep the session current + drain," never "fetch since N".

### Channels
- `GET /channels`
- `GET /channels/{key}/detail`
- `POST /channels`
- `POST /channels/bulk-hashtag`
- `DELETE /channels/{key}`
- `POST /channels/{key}/flood-scope-override`
- `POST /channels/{key}/path-hash-mode-override`
- `POST /channels/{key}/mark-read`

### Messages
- `GET /messages` — list with filters; supports `q` (full-text search), `after`/`after_id` (forward cursor)
- `GET /messages/around/{message_id}` — context messages around a target (for jump-to-message navigation)
- `POST /messages/direct`
- `POST /messages/channel`
- `POST /messages/channel/{message_id}/resend`
- `POST /messages/mcmp-estimate` — compressed wire size of a draft (`{text, version}` → `{wire_bytes, compressed}`) for the live compose counter; pure computation, `text` capped at 4096 chars

### Packets
- `GET /packets/undecrypted/count`
- `POST /packets/region-backfill` — re-resolve region scope for stored channel messages that still have a retained raw packet (region is otherwise only tagged at ingest); returns `{scanned, scoped, named}`
- `GET /packets/{packet_id}` — fetch one stored raw packet by row ID for on-demand inspection
- `POST /packets/decrypt/historical`
- `POST /packets/maintenance`

### Read state
- `GET /read-state/unreads` — counts, mention flags, `last_message_times`, `last_read_ats`, and `first_unread_ids`
- `POST /read-state/mark-all-read`

### Settings
- `GET /settings`
- `PATCH /settings`
- `POST /settings/favorites/toggle`
- `POST /settings/blocked-keys/toggle`
- `POST /settings/blocked-names/toggle`
- `POST /settings/tracked-telemetry/toggle`
- `GET /settings/tracked-telemetry/schedule` — current telemetry scheduling derivation, interval options, and next-run-at timestamp
- `POST /settings/tracked-telemetry-contacts/toggle` — toggle tracked LPP telemetry for any contact (max 8)
- `GET /settings/tracked-telemetry-contacts/schedule` — contact telemetry scheduling (shared ceiling with repeaters)
- `POST /settings/muted-channels/toggle`
- `POST /settings/mcmp/set` — configure MCMP for a conversation (`{type: "contact"|"channel", id, enabled, version?}`; `version` 2 or 3, omit to leave unchanged); broadcasts a `contact`/`channel` event

### Fanout
- `GET /fanout` — list all fanout configs
- `POST /fanout` — create new fanout config
- `PATCH /fanout/{id}` — update fanout config (triggers module reload)
- `DELETE /fanout/{id}` — delete fanout config (stops module)
- `POST /fanout/bots/disable-until-restart` — stop bot modules and keep bots disabled until restart

### Statistics
- `GET /statistics` — aggregated mesh network stats (entity counts, message/packet splits, activity windows, busiest channels, `region_scope_24h` regional adoption)

### Push
- `GET /push/vapid-public-key` — VAPID public key for browser `PushManager.subscribe()`
- `POST /push/subscribe` — register/upsert push subscription (keyed by endpoint URL)
- `GET /push/subscriptions` — list all push subscriptions
- `PATCH /push/subscriptions/{id}` — update label or filter preferences
- `DELETE /push/subscriptions/{id}` — delete subscription
- `POST /push/subscriptions/{id}/test` — send test notification
- `GET /push/conversations` — global list of push-enabled conversation state keys
- `POST /push/conversations/toggle` — add or remove a conversation from the global push list

### WebSocket
- `WS /ws`

## WebSocket Events

- `health` — radio connection status (broadcast on change, personal on connect)
- `contact` — single contact upsert (from advertisements and radio sync)
- `contact_resolved` — prefix contact reconciled to a full contact row (payload: `{ previous_public_key, contact }`)
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
- `contacts` (includes `first_seen` for contact age tracking and `direct_path_hash_mode` / `route_override_*` for DM routing)
- `channels`
  Includes optional `flood_scope_override` for channel-specific regional sends and optional `path_hash_mode_override` for per-channel path hop width.
- `messages` (includes `sender_name`, `sender_key` for per-contact channel message attribution)
- `raw_packets`
- `contact_advert_paths` (recent unique advertisement paths per contact, keyed by contact + path bytes + hop count)
- `contact_name_history` (tracks name changes over time)
- `repeater_telemetry_history` (time-series telemetry snapshots for tracked repeaters)
- `contact_telemetry_history` (time-series LPP telemetry snapshots for tracked contacts; same schema as repeater table)
- `fanout_configs` (MQTT, bot, webhook, Apprise, SQS integration configs)
- `push_subscriptions` (Web Push browser subscriptions with delivery metadata; UNIQUE on endpoint)
- `app_settings` (includes `vapid_private_key` and `vapid_public_key` for Web Push VAPID signing)

Contact route state is canonicalized on the backend:
- stored route inputs: `direct_path`, `direct_path_len`, `direct_path_hash_mode`, `direct_path_updated_at`, plus optional `route_override_*`
- computed route surface: `effective_route`, `effective_route_source`, `direct_route`, `route_override`
- removed legacy names: `last_path`, `last_path_len`, `out_path_hash_mode`

Frontend and send paths should consume the canonical route surface rather than reconstructing precedence from raw fields.

Repository writes should prefer typed models such as `ContactUpsert` over ad hoc dict payloads when adding or updating schema-coupled data.

`max_radio_contacts` is the configured radio contact capacity baseline. Favorites reload first, the app refills non-favorite working-set contacts to about 80% of that capacity, and periodic offload triggers once occupancy reaches about 95%.

`app_settings` fields in active model:
- `max_radio_contacts`
- `auto_decrypt_dm_on_advert`
- `last_message_times`
- `advert_interval`
- `last_advert_time`
- `flood_scope`
- `known_regions`
- `blocked_keys`, `blocked_names`, `discovery_blocked_types`
- `tracked_telemetry_repeaters`, `tracked_telemetry_contacts`
- `auto_resend_channel`
- `telemetry_interval_hours`

Note: MQTT, community MQTT, and bot configs were migrated to the `fanout_configs` table (migrations 36-38).

## Security Posture (intentional)

- No per-user authn/authz model; optionally, operators may enable app-wide HTTP Basic auth for both HTTP and WS entrypoints.
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
├── test_block_lists.py         # Blocked keys/names filtering across list/search surfaces
├── test_bot.py                 # Bot execution and sandboxing
├── test_channel_sender_backfill.py # Sender-key backfill uniqueness rules for channel messages
├── test_channels_router.py     # Channels router endpoints
├── test_community_mqtt.py      # Community MQTT publisher (JWT, packet format, hash, broadcast)
├── test_config.py              # Configuration validation
├── test_contact_reconciliation_service.py # Prefix/contact reconciliation service helpers
├── test_contacts_router.py     # Contacts router endpoints
├── test_decoder.py             # Packet parsing/decryption
├── test_disable_bots.py        # MESHCORE_DISABLE_BOTS=true feature
├── test_echo_dedup.py          # Echo/repeat deduplication (incl. concurrent)
├── test_fanout.py              # Fanout bus CRUD, scope matching, manager dispatch
├── test_fanout_hitlist.py      # Fanout-related hitlist regression tests
├── test_fanout_integration.py  # Fanout integration tests
├── test_event_handlers.py      # ACK tracking, event registration, cleanup
├── test_frontend_static.py     # Frontend static file serving
├── test_health_mqtt_status.py  # Health endpoint MQTT status field
├── test_http_quality.py        # Cache-control / gzip / basic-auth HTTP quality checks
├── test_key_normalization.py   # Public key normalization
├── test_keystore.py            # Ephemeral keystore
├── test_main_startup.py        # App startup and lifespan
├── test_map_upload.py          # Map upload fanout module
├── test_message_pagination.py  # Cursor-based message pagination
├── test_message_prefix_claim.py # Message prefix claim logic
├── test_mqtt.py                # MQTT publisher topic routing and lifecycle
├── test_messages_search.py     # Message search, around, forward pagination
├── test_mqtt_ha.py             # Home Assistant MQTT Discovery fanout module
├── test_packet_pipeline.py     # End-to-end packet processing
├── test_packets_router.py      # Packets router endpoints (decrypt, maintenance)
├── test_path_utils.py          # Path hex rendering helpers
├── test_radio.py               # RadioManager, serial detection
├── test_radio_commands_service.py # Radio config/private-key service workflows
├── test_radio_lifecycle_service.py # Reconnect/setup orchestration helpers
├── test_radio_operation.py     # radio_operation() context manager
├── test_radio_router.py        # Radio router endpoints
├── test_radio_runtime_service.py # radio_runtime seam behavior and helpers
├── test_radio_sync.py          # Polling, sync, advertisement
├── test_real_crypto.py         # Real cryptographic operations
├── test_repeater_routes.py     # Repeater command/telemetry/trace + granular pane endpoints
├── test_repository.py          # Data access layer
├── test_room_routes.py         # Room-server login/status/telemetry/ACL endpoints
├── test_rx_log_data.py         # on_rx_log_data event handler integration
├── test_security.py            # Optional Basic Auth middleware / config behavior
├── test_send_messages.py       # Outgoing messages, bot triggers, concurrent sends
├── test_settings_router.py     # Settings endpoints, advert validation
├── test_push_send.py           # Web Push send/dispatch
├── test_radio_stats.py         # Radio stats sampling and noise-floor history
├── test_repeater_telemetry.py  # Repeater telemetry history recording
├── test_service_installer.py   # Service installer script behavior
├── test_sqs_fanout.py          # SQS fanout module
├── test_statistics.py          # Statistics aggregation
├── test_telemetry_interval.py  # Telemetry interval scheduling math
├── test_version_info.py        # Version/build metadata resolution
├── test_websocket.py           # WS manager broadcast/cleanup
└── test_websocket_route.py     # WS endpoint lifecycle
```

## Errata & Known Non-Issues

### Sender timestamps are 1-second resolution (protocol constraint)

The MeshCore radio protocol encodes `sender_timestamp` as a 4-byte little-endian integer (Unix seconds). This is a firmware-level wire format — the radio, the Python library (`commands/messaging.py`), and the decoder (`decoder.py`) all read/write exactly 4 bytes. Millisecond Unix timestamps would overflow 4 bytes, so higher resolution is not possible without a firmware change.

**Consequence:** Message dedup still operates at 1-second granularity because the radio protocol only provides second-resolution `sender_timestamp`. Do not attempt to fix this by switching to millisecond timestamps — it will break echo dedup (the echo's 4-byte timestamp won't match the stored value) and overflow `to_bytes(4, "little")`. Incoming DMs now share the same second-resolution content identity tradeoff as channel echoes: same-contact same-text same-second observations collapse onto one stored row.

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
