# Advanced Setup And Troubleshooting

Once the backend is running, FastAPI serves interactive API docs at `/docs` on the same host and port as the web UI. For a default local launch, that is http://localhost:8000/docs.

## Remediation & Advanced Environment Variables

These are intended for diagnosing or working around radios that behave oddly, or enabling advanced functionality.

| Variable | Default | Description |
|----------|---------|-------------|
| `MESHCORE_ENABLE_MESSAGE_POLL_FALLBACK` | false | Run aggressive 10-second `get_msg()` fallback polling to check for messages ([docs](#message-poll-fallback)) |
| `MESHCORE_FORCE_CHANNEL_SLOT_RECONFIGURE` | false | Disable channel-slot reuse and force `set_channel(...)` before every channel send ([docs](#force-channel-slot-reconfigure)) |
| `MESHCORE_LOAD_WITH_AUTOEVICT` | false | Enable autoevict mode for contact loading ([docs](#autoevict-mode)) |
| `__CLOWNTOWN_DO_CLOCK_WRAPAROUND` | false | Highly experimental: if the radio clock is ahead of system time, try forcing the clock to `0xFFFFFFFF`, wait for uint32 wraparound, and then retry normal time sync before falling back to reboot ([docs](#clock-wraparound)) |
| `MESHCORE_ENABLE_LOCAL_PRIVATE_KEY_EXPORT` | false | Enable `GET /api/radio/private-key` to return the in-memory private key as hex for backup or migration. Only enable on a trusted network. Import via `PUT /api/radio/private-key` is always available. ([docs](#private-key-export)) |

By default the app relies on radio events plus MeshCore auto-fetch for incoming messages, and also runs a low-frequency hourly audit poll. That audit checks both:

- whether messages were left on the radio without reaching the app through event subscription
- whether the app's channel-slot expectations still match the radio's actual channel listing

If the audit finds a mismatch, you'll see an error in the application UI and your logs.

### Message Poll Fallback

If you see that warning, or if messages on the radio never show up in the app, try `MESHCORE_ENABLE_MESSAGE_POLL_FALLBACK=true` to switch that task into a more aggressive 10-second safety net.

### Force Channel Slot Reconfigure

If room sends appear to be using the wrong channel slot or another client is changing slots underneath this app, try `MESHCORE_FORCE_CHANNEL_SLOT_RECONFIGURE=true` to force the radio to validate the channel slot is valid before sending (will delay sending by ~500ms).

### Clock Wraparound

`__CLOWNTOWN_DO_CLOCK_WRAPAROUND=true` is a last-resort clock remediation for nodes whose RTC is stuck in the future and where rescue-mode time setting or GPS-based time is not available. It intentionally relies on the clock rolling past the 32-bit epoch boundary, which is board-specific behavior and may not be safe or effective on all MeshCore targets. Treat it as highly experimental.

### Private Key Export

`MESHCORE_ENABLE_LOCAL_PRIVATE_KEY_EXPORT=true` enables `GET /api/radio/private-key`, which returns the in-memory private key as hex for backup or migration. The key is held in memory only (exported from the radio on connect) and is never persisted to disk. Only enable this on a trusted network when you need to retrieve the key.

Import via `PUT /api/radio/private-key` is always available regardless of this setting — it is write-only and does not expose key material.

The Radio Settings config export/import feature uses these endpoints. When export is disabled, config exports will omit the private key and show a notice.

## Contact Loading Issues

RemoteTerm loads favorite and recently active contacts onto the radio so that the radio can automatically acknowledge incoming DMs on your behalf. To do this, it first enumerates the radio's existing contact table, then reconciles it with the desired working set.

On BLE connections with many contacts (or radios with large contact tables from organic advertisements), the initial contact enumeration may time out. If this happens, the app will still attempt to load your favorites and recent contacts onto the radio on a best-effort basis, but without a full snapshot of what's already on the radio, some adds may be redundant or fail.

If the radio's contact table is already full (from contacts added by advertisements or another client), the app may not be able to load all desired contacts. In this case you'll see a warning that auto-DM acking may not work for all contacts. To resolve this:

- **Clear the radio's contact table** using another MeshCore client (e.g., the official companion app), then restart RemoteTerm
- **Lower the contact fill target** in Radio Settings to reduce how many contacts the app tries to load
- **Enable autoevict mode** (see below) to let the radio automatically make room
- If you don't need auto-DM acking, you can safely ignore these warnings — **sending and receiving messages is never affected**

### Autoevict Mode

Setting `MESHCORE_LOAD_WITH_AUTOEVICT=true` enables an alternative contact loading strategy that avoids TABLE_FULL errors entirely. On connect, the app enables the radio's `AUTO_ADD_OVERWRITE_OLDEST` preference, which makes the radio automatically evict the oldest non-favorite contact when the contact table is full. This means:

- Contact adds never fail — the radio always makes room by evicting stale contacts
- The app can load contacts even when it can't enumerate the radio's existing contact table (e.g., on slow BLE connections)
- No contact removal step is needed during reconciliation

**Trade-off:** Contacts loaded by the app are not marked as radio-side favorites, so they are eviction candidates if the radio receives a new advertisement while full. In practice, freshly-loaded contacts have a recent `lastmod` timestamp and will be among the last to be evicted. If you disconnect the radio from RemoteTerm and use it standalone, your contacts will not be protected from eviction by newer advertisements.

## Sub-Path Reverse Proxy

RemoteTerm works behind a reverse proxy that serves it under a sub-path (e.g. `/meshcore/` or Home Assistant ingress). All frontend asset and API paths are relative, so they resolve correctly under any prefix.

**Requirements:**

- The proxy must ensure the sub-path URL has a **trailing slash**. If a user visits `/meshcore` (no slash), relative paths break. Most proxies handle this automatically; for Nginx, a `location /meshcore/ { ... }` block (note the trailing slash) does the right thing.
- For correct PWA install behavior, the proxy should forward `X-Forwarded-Prefix` (set to the sub-path, e.g. `/meshcore`) so the web manifest generates correct `start_url` and `scope` values. `X-Forwarded-Proto` and `X-Forwarded-Host` are also respected for origin resolution.

## HTTPS

WebGPU channel-finding and voice recording require a secure context when you are not on `localhost`.

For an Internet-facing DNS hostname, terminate TLS in a standard reverse proxy such as Caddy, nginx, or Apache and use that proxy's ACME/Let's Encrypt integration. RemoteTerm deliberately does not give its unprivileged web process permission to install system packages, write system certificate paths, or reconfigure services. Keep Uvicorn bound to the trusted host/network on port 8000 behind the proxy. Certificate renewal is then owned by the proxy's normal renewal service.

Generate a local cert and start the backend with TLS:

```bash
openssl req -x509 -newkey rsa:4096 -keyout key.pem -out cert.pem -days 365 -nodes -subj '/CN=localhost'
uv run uvicorn app.main:app --host 0.0.0.0 --port 8000 --ssl-keyfile=key.pem --ssl-certfile=cert.pem
```

For Docker Compose, generate the cert, mount it into the container, and override the launch command:

```yaml
services:
  remoteterm:
    volumes:
      - ./data:/app/data
      - ./cert.pem:/app/cert.pem:ro
      - ./key.pem:/app/key.pem:ro
    command: uv run uvicorn app.main:app --host 0.0.0.0 --port 8000 --ssl-keyfile=/app/key.pem --ssl-certfile=/app/cert.pem
```

Accept the browser warning, or use [mkcert](https://github.com/FiloSottile/mkcert) for locally-trusted certs.

Voice encoding also requires the system Codec2 library (`libcodec2-1.2` on current Debian/Ubuntu releases, `codec2` on Arch Linux). The official container and AUR package include it. When it is absent, RemoteTerm reports voice as unavailable without affecting text messaging.

## Systemd Service

On Linux systems, this is the recommended installation method if you want RemoteTerm set up as a persistent systemd service that starts automatically on boot and restarts automatically if it crashes. Run the installer script from the repo root. It runs as your current user, installs from wherever you cloned the repo, and prints a quick-reference cheatsheet when done — no separate service account or path juggling required.

```bash
bash scripts/setup/install_service.sh
```

You can also rerun the script later to change transport, bot, or auth settings. If the service is already running, the installer stops it, rewrites the unit file, reloads systemd, and starts it again with the new configuration.

## Debug Logging And Bug Reports

If you're experiencing issues or opening a bug report, please start the backend with debug logging enabled. Debug mode provides a much more detailed breakdown of radio communication, packet processing, and other internal operations, which makes it significantly easier to diagnose problems.

```bash
MESHCORE_LOG_LEVEL=DEBUG uv run uvicorn app.main:app --host 0.0.0.0 --port 8000
```

You can also navigate to `/api/debug` (or go to Settings -> About -> "Open debug support snapshot" at the bottom). This debug block contains information about the operating environment, expectations around keys and channels, and radio status. It also includes the most recent logs. **Non-log information reveals no keys, channel names, or other privilege information beyond the names of your bots. The logs, however, may contain channel names or keys (but never your private key).** If you do not wish to include this information, copy up to the `STOP COPYING HERE` marker in the debug body.

## Development Notes

For day-to-day development, see [CONTRIBUTING.md](CONTRIBUTING.md).

Windows note: I've seen an intermittent startup issue like `"Received empty packet: index out of range"` with failed contact sync. I can't figure out why this happens. The issue typically resolves on restart. If you can figure out why this happens, I will buy you a virtual or iRL six pack if you're in the PNW. As a former always-windows-girlie before embracing WSL2, I despise second-classing M$FT users, but I'm just stuck with this one.
