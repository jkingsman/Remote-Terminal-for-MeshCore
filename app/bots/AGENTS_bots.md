# Bots Workspace Architecture

The bots system replaces the fanout `bot` config type (migration 064) and merges
in meshcore-bot's command/service/scheduler/feed features as one engine. Bots
are Python scripts stored in the `bots` table, edited in the frontend Bots
workspace, and executed in-process — the same trust model the fanout bot editor
always had (`SECURITY.md` posture unchanged: trusted networks, trusted
operators).

## The pieces

- `api.py` — the authoring surface (`from remoteterm import bot` + `BotContext`).
  Decorators register handlers into a collector while `runtime.load_bot_code`
  exec()s the source. `BotContext` carries sends (`reply`/`send`/`send_dm`),
  `settings`, persistent `state`, `http` (httpx), `geocode`, i18n (`t`),
  `mesh_stats`, `get_enabled_bots`, logging. Test runs capture sends instead of
  transmitting.
- `runtime.py` — load/validate source. Two styles: decorated handlers, or a
  legacy module-level `def bot(...)` (auto-wrapped; executed via the original
  `app/fanout/bot_exec.execute_bot_code`, so migrated bots behave identically).
- `engine.py` — the singleton `bot_engine`. Fed `message` and `contact` events
  from `websocket.broadcast_event` (same tap as the fanout bus). Keyword
  triggers pass banned/hops/scope/prefix/mention/admin gates plus global,
  per-user, and per-bot cooldown limiters (with a queue window); catch-all
  `on_message` handlers and legacy bots see every in-scope message and filter
  themselves. One 15s ticker drives bot cron triggers, the `bot_schedules`
  table, and `bot_feeds` polling. All sends serialize behind one TX-spacing
  lock (engine settings). Runs are recorded to `bot_runs`; logs go to a ring
  buffer + `bot_log` WS events.
- `cron.py` — dependency-free 5-field crontab (+`@presets`). **Day-of-week is
  0=Monday** (APScheduler numbering, meshcore-bot compatible), and
  dom+dow-both-restricted uses standard cron OR semantics.
- `feeds.py` — RSS 2.0/Atom + JSON API polling, `{field|filter:arg}` format
  templates, SSRF guard (private/loopback hosts refused), first-check-only
  marks position (no history flood).
- `translate.py` — 10 locales in `translations/` (ported from meshcore-bot),
  dotted-key lookup with locale fallback, keyword-based language detection.
- `moderation.py` — banned senders (prefix match) + outgoing profanity filter.
- `placeholders.py` — `{total_contacts}`-style tokens for scheduled messages.
- `library/` — built-in bots as real `.py` files under `library/code/`, each
  self-describing via a module-level `BOT_META` dict (metadata +
  `settings_schema`). Seeded at startup (`ensure_seeded`): inserts are
  **disabled by default**; unmodified built-ins refresh on version bumps;
  operator-modified ones are never touched. "Reset to default" restores from
  the shipped file.

## Data model

`bots` (code, settings_schema, settings, scope, limits, ui_triggers, state,
builtin lineage), `bot_runs` (bounded history, feeds the dashboard),
`bot_schedules` (standalone cron messages), `bot_feeds`, and the singleton
`bot_engine_settings` (prefix, mention mode, rate limits, language, moderation,
admin users). Repository: `app/repository/bots.py`.

## API

`/api/bots` CRUD + `/library`, `/engine` (GET/PATCH),
`/engine/disable-until-restart`, `/logs`, `/stats?window=`, `/runs`,
`/{id}/test` (sandboxed run), `/{id}/reset`, `/schedules/*`, `/feeds/*`, and
`POST /api/hooks/{slug}` for `@bot.on_webhook` (gated on the bot's
`webhook_token` setting). Route-order gotcha: fixed paths that share the
`POST /<segment>/test` shape must be registered before `POST /{bot_id}/test`.

## Invariants worth keeping

- Legacy `def bot(**kwargs)` sources must keep running unchanged (migration
  064 moved them here verbatim).
- Seeded bots ship disabled — enabling what a node answers is an operator act.
- `ui_triggers` only feed handlers declared with **no-argument** decorators
  (`@bot.on_keyword()` / `@bot.on_cron()`); code-declared triggers are derived
  at load time and never stored.
- The engine never raises into `broadcast_event` — every handler run is
  wrapped, recorded, and logged.
- Frontend: the Bots view lives at `#bots` / `#bots/{botId}`
  (`frontend/src/components/bots/`), live logs ride the module-level
  `stores/botLogStore.ts` exactly like raw packets (never lift into App state).
