# Fanout Bus Architecture

The fanout bus is a unified system for dispatching mesh radio events (decoded messages and raw packets) to external integrations. It replaces the previous scattered singleton MQTT publishers with a modular, configurable framework.

## Core Concepts

### FanoutModule (base.py)
Base class that all integration modules extend:
- `__init__(config_id, config, *, name="")` — constructor; receives the config UUID, the type-specific config dict, and the user-assigned name
- `start()` / `stop()` — async lifecycle (e.g. open/close connections)
- `on_message(data)` — receive decoded messages (DM/channel)
- `on_raw(data)` — receive raw RF packets
- `status` property (**must override**) — return `"connected"`, `"disconnected"`, or `"error"`

### FanoutManager (manager.py)
Singleton that owns all active modules and dispatches events:
- `load_from_db()` — startup: load enabled configs, instantiate modules
- `reload_config(id)` — CRUD: stop old, start new
- `remove_config(id)` — delete: stop and remove
- `broadcast_message(data)` — scope-check + dispatch `on_message`
- `broadcast_raw(data)` — scope-check + dispatch `on_raw`
- `stop_all()` — shutdown
- `get_statuses()` — health endpoint data

All modules are constructed uniformly: `cls(config_id, config_blob, name=cfg.get("name", ""))`.

### Scope Matching
Each config has a `scope` JSON blob controlling what events reach it:
```json
{"messages": "all", "raw_packets": "all"}
{"messages": "none", "raw_packets": "all"}
{"messages": {"channels": ["key1"], "contacts": "all"}, "raw_packets": "none"}
```
Community MQTT always enforces `{"messages": "none", "raw_packets": "all"}`.

## Event Flow

```
Radio Event -> packet_processor / event_handler
  -> broadcast_event("message"|"raw_packet", data, realtime=True)
    -> WebSocket broadcast (always)
    -> FanoutManager.broadcast_message/raw (only if realtime=True)
      -> scope check per module
      -> module.on_message / on_raw
```

Setting `realtime=False` (used during historical decryption) skips fanout dispatch entirely.

## Current Module Types

### mqtt_private (mqtt_private.py)
Wraps `MqttPublisher` from `app/fanout/mqtt.py`. Config blob:
- `broker_host`, `broker_port`, `username`, `password`
- `use_tls`, `tls_insecure`, `topic_prefix`

### mqtt_community (mqtt_community.py)
Wraps `CommunityMqttPublisher` from `app/fanout/community_mqtt.py`. Config blob:
- `broker_host`, `broker_port`, `iata`, `email`
- Only publishes raw packets (on_message is a no-op)
- The published `raw` field is always the original packet hex.
- When a direct packet includes a `path` field, it is emitted as comma-separated hop identifiers exactly as the packet reports them. Token width varies with the packet's path hash mode (`1`, `2`, or `3` bytes per hop); there is no legacy flat per-byte companion field.

### bot (bot.py)
Wraps bot code execution via `app/fanout/bot_exec.py`. Config blob:
- `code` — Python bot function source code
- Executes in a thread pool with timeout and semaphore concurrency control
- Rate-limits outgoing messages for repeater compatibility

### webhook (webhook.py)
HTTP webhook delivery. Config blob:
- `url`, `method` (POST/PUT/PATCH)
- `hmac_secret` (optional) — when set, each request includes an HMAC-SHA256 signature of the JSON body
- `hmac_header` (optional, default `X-Webhook-Signature`) — header name for the signature (value format: `sha256=<hex>`)
- `headers` — arbitrary extra headers (JSON object)

### apprise (apprise_mod.py)
Push notifications via Apprise library. Config blob:
- `urls` — newline-separated Apprise notification service URLs
- `preserve_identity` — suppress Discord webhook name/avatar override
- `include_path` — include routing path in notification body

### sqs (sqs.py)
Amazon SQS delivery. Config blob:
- `queue_url` — target queue URL
- `region_name` (optional; inferred from standard AWS SQS queue URLs when omitted), `endpoint_url` (optional)
- `access_key_id`, `secret_access_key`, `session_token` (all optional; blank uses the normal AWS credential chain)
- Publishes a JSON envelope of the form `{"event_type":"message"|"raw_packet","data":...}`
- Supports both decoded messages and raw packets via normal scope selection

## Adding a New Integration Type

### Step-by-step checklist

#### 1. Backend module (`app/fanout/my_type.py`)

Create a class extending `FanoutModule`:

```python
from app.fanout.base import FanoutModule

class MyTypeModule(FanoutModule):
    def __init__(self, config_id: str, config: dict, *, name: str = "") -> None:
        super().__init__(config_id, config, name=name)
        # Initialize module-specific state

    async def start(self) -> None:
        """Open connections, create clients, etc."""

    async def stop(self) -> None:
        """Close connections, clean up resources."""

    async def on_message(self, data: dict) -> None:
        """Handle decoded messages. Omit if not needed."""

    async def on_raw(self, data: dict) -> None:
        """Handle raw packets. Omit if not needed."""

    @property
    def status(self) -> str:
        """Required. Return 'connected', 'disconnected', or 'error'."""
        ...
```

Constructor requirements:
- Must accept `config_id: str, config: dict, *, name: str = ""`
- Must forward `name` to super: `super().__init__(config_id, config, name=name)`

#### 2. Register in manager (`app/fanout/manager.py`)

Add import and mapping in `_register_module_types()`:

```python
from app.fanout.my_type import MyTypeModule
_MODULE_TYPES["my_type"] = MyTypeModule
```

#### 3. Router changes (`app/routers/fanout.py`)

Three changes needed:

**a)** Add to `_VALID_TYPES` set:
```python
_VALID_TYPES = {"mqtt_private", "mqtt_community", "bot", "webhook", "apprise", "sqs", "my_type"}
```

**b)** Add a validation function:
```python
def _validate_my_type_config(config: dict) -> None:
    """Validate my_type config blob."""
    if not config.get("some_required_field"):
        raise HTTPException(status_code=400, detail="some_required_field is required")
```

**c)** Wire validation into both `create_fanout_config` and `update_fanout_config` — add an `elif` to the validation block in each:
```python
elif body.type == "my_type":
    _validate_my_type_config(body.config)
```
Note: validation only runs when the config will be enabled (disabled configs are treated as drafts).

**d)** Add scope enforcement in `_enforce_scope()` if the type has fixed scope constraints (e.g. raw_packets always none). Otherwise it falls through to the `mqtt_private` default which allows both messages and raw_packets to be configurable.

#### 4. Frontend editor component (`SettingsFanoutSection.tsx`)

Four changes needed in this single file:

**a)** Add to `TYPE_LABELS` and `TYPE_OPTIONS` at the top:
```tsx
const TYPE_LABELS: Record<string, string> = {
  // ... existing entries ...
  my_type: 'My Type',
};

const TYPE_OPTIONS = [
  // ... existing entries ...
  { value: 'my_type', label: 'My Type' },
];
```

**b)** Create an editor component (follows the same pattern as existing editors):
```tsx
function MyTypeConfigEditor({
  config,
  scope,
  onChange,
  onScopeChange,
}: {
  config: Record<string, unknown>;
  scope: Record<string, unknown>;
  onChange: (config: Record<string, unknown>) => void;
  onScopeChange: (scope: Record<string, unknown>) => void;
}) {
  return (
    <div className="space-y-3">
      {/* Type-specific config fields */}
      <Separator />
      <ScopeSelector scope={scope} onChange={onScopeChange} />
    </div>
  );
}
```

If your type does NOT have user-configurable scope (like bot or community MQTT), omit the `scope`/`onScopeChange` props and the `ScopeSelector`.

The `ScopeSelector` component is defined within the same file. It accepts an optional `showRawPackets` prop:
- **Without `showRawPackets`** (webhook, apprise): shows message scope only (all/only/except — no "none" option since that would make the integration a no-op). A warning appears when the effective selection matches nothing.
- **With `showRawPackets`** (private MQTT): adds a "Forward raw packets" toggle and includes the "No messages" option (valid when raw packets are enabled). The warning appears only when both raw packets and messages are effectively disabled.

**c)** Add default config and scope in `handleAddCreate`:
```tsx
const defaults: Record<string, Record<string, unknown>> = {
  // ... existing entries ...
  my_type: { some_field: '', other_field: true },
};
const defaultScopes: Record<string, Record<string, unknown>> = {
  // ... existing entries ...
  my_type: { messages: 'all', raw_packets: 'none' },
};
```

**d)** Wire the editor into the detail view's conditional render block:
```tsx
{editingConfig.type === 'my_type' && (
  <MyTypeConfigEditor
    config={editConfig}
    scope={editScope}
    onChange={setEditConfig}
    onScopeChange={setEditScope}
  />
)}
```

#### 5. Tests

**Backend integration tests** (`tests/test_fanout_integration.py`):
- Test that a configured + enabled module receives messages via `FanoutManager.broadcast_message`
- Test scope filtering (all, none, selective)
- Test that a disabled module does not receive messages

**Backend unit tests** (`tests/test_fanout_hitlist.py` or a dedicated file):
- Test config validation (required fields, bad values)
- Test module-specific logic in isolation

**Frontend tests** (`frontend/src/test/fanoutSection.test.tsx`):
- The existing suite covers the list/edit/create flow generically. If your editor has special behavior, add specific test cases.

#### Summary of files to touch

| File | Change |
|------|--------|
| `app/fanout/my_type.py` | New module class |
| `app/fanout/manager.py` | Import + register in `_register_module_types()` |
| `app/routers/fanout.py` | `_VALID_TYPES` + validator function + scope enforcement |
| `frontend/.../SettingsFanoutSection.tsx` | `TYPE_LABELS` + `TYPE_OPTIONS` + editor component + defaults + detail view wiring |
| `tests/test_fanout_integration.py` | Integration tests |

## REST API

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/fanout` | List all fanout configs |
| POST | `/api/fanout` | Create new config |
| PATCH | `/api/fanout/{id}` | Update config (triggers module reload) |
| DELETE | `/api/fanout/{id}` | Delete config (stops module) |

## Database

`fanout_configs` table:
- `id` TEXT PRIMARY KEY
- `type`, `name`, `enabled`, `config` (JSON), `scope` (JSON)
- `sort_order`, `created_at`

Migrations:
- **36**: Creates `fanout_configs` table, migrates existing MQTT settings from `app_settings`
- **37**: Migrates bot configs from `app_settings.bots` JSON column into fanout rows
- **38**: Drops legacy `mqtt_*`, `community_mqtt_*`, and `bots` columns from `app_settings`

## Key Files

- `app/fanout/base.py` — FanoutModule base class
- `app/fanout/manager.py` — FanoutManager singleton
- `app/fanout/mqtt_base.py` — BaseMqttPublisher ABC (shared MQTT connection loop)
- `app/fanout/mqtt.py` — MqttPublisher (private MQTT publishing)
- `app/fanout/community_mqtt.py` — CommunityMqttPublisher (community MQTT with JWT auth)
- `app/fanout/mqtt_private.py` — Private MQTT fanout module
- `app/fanout/mqtt_community.py` — Community MQTT fanout module
- `app/fanout/bot.py` — Bot fanout module
- `app/fanout/bot_exec.py` — Bot code execution, response processing, rate limiting
- `app/fanout/webhook.py` — Webhook fanout module
- `app/fanout/apprise_mod.py` — Apprise fanout module
- `app/fanout/sqs.py` — Amazon SQS fanout module
- `app/repository/fanout.py` — Database CRUD
- `app/routers/fanout.py` — REST API
- `app/websocket.py` — `broadcast_event()` dispatches to fanout
- `frontend/src/components/settings/SettingsFanoutSection.tsx` — UI
