# Frontend AGENTS.md

This document is the frontend working guide for agents and developers.
Keep it aligned with `frontend/src` source code.

## Stack

- React 18 + TypeScript
- Vite
- Vitest + Testing Library
- shadcn/ui primitives
- Tailwind utility classes + local CSS (`index.css`, `styles.css`)
- Sonner (toasts)
- Leaflet / react-leaflet (map)
- `@michaelhart/meshcore-decoder` installed via npm alias to `meshcore-decoder-multibyte-patch`
- `meshcore-hashtag-cracker` + `nosleep.js` (channel cracker)
- Multibyte-aware decoder build published as `meshcore-decoder-multibyte-patch`

## Code Ethos

- Prefer fewer, stronger modules over many thin wrappers.
- Split code only when the new hook/component owns a real invariant or workflow.
- Keep one reasoning unit readable in one place, even if that file is moderately large.
- Avoid dedicated files whose main job is pass-through, prop bundling, or renaming.
- For this repo, "locally dense but semantically obvious" is better than indirection-heavy "clean architecture".
- When refactoring, preserve behavior first and add tests around the seam being moved.

## Frontend Map

```text
frontend/src/
├── main.tsx                # React entry point (StrictMode, root render)
├── App.tsx                 # Data/orchestration entry that wires hooks into AppShell
├── api.ts                  # Typed REST client
├── types.ts                # Shared TS contracts
├── useWebSocket.ts         # WS lifecycle + event dispatch
├── wsEvents.ts             # Typed WS event parsing / discriminated union
├── prefetch.ts             # Consumes prefetched API promises started in index.html
├── index.css               # Global styles/utilities
├── styles.css              # Additional global app styles
├── themes.css              # Color theme definitions
├── contexts/
│   ├── DistanceUnitContext.tsx # Browser-local distance-unit context/provider
│   ├── PathHopWidthContext.tsx # Browser-local path hop-width display preference
│   ├── RichPayloadContext.tsx  # Browser-local rich MeshCore payload rendering preference
│   └── PushSubscriptionContext.tsx # Push subscription state context/provider
├── lib/
│   └── utils.ts            # cn() — clsx + tailwind-merge helper
├── networkGraph/
│   └── packetNetworkGraph.ts # Packet→network graph construction shared by visualizer surfaces
├── stores/
│   └── rawPacketStore.ts   # Overheard packet stream + session stats, outside React
├── hooks/
│   ├── index.ts            # Central re-export of all hooks
│   ├── useConversationActions.ts   # Send/resend/trace/block conversation actions
│   ├── useConversationNavigation.ts # Search target, selection reset, and info-pane navigation state
│   ├── useConversationMessages.ts  # Conversation timeline loading, cache restore, jump-target loading, pagination, dedup, pending ACK buffering
│   ├── useUnreadCounts.ts          # Unread counters, mentions, recent-sort timestamps
│   ├── useRealtimeAppState.ts      # WebSocket event application and reconnect recovery
│   ├── useAppShell.ts              # App-shell view state (settings/sidebar/modals/cracker)
│   ├── useRepeaterDashboard.ts      # Repeater dashboard state (login, panes, console, retries)
│   ├── useRadioControl.ts          # Radio health/config state, reconnection, mesh discovery sweeps
│   ├── useAppSettings.ts           # Settings, favorites, preferences migration
│   ├── useConversationRouter.ts    # URL hash → active conversation routing
│   ├── useContactsAndChannels.ts   # Contact/channel loading, creation, deletion
│   ├── useBrowserNotifications.ts  # Per-conversation browser notification preferences + dispatch
│   ├── usePushSubscription.ts      # Web Push subscription lifecycle, per-conversation filters
│   ├── useFaviconBadge.ts          # Browser tab unread badge state
│   ├── useEntranceSettled.ts       # Defers entrance animation work until layout settles
│   └── useRememberedServerPassword.ts # Browser-local repeater/room password persistence
├── components/
│   ├── AppShell.tsx            # App-shell layout: status, sidebar, search/settings panes, cracker, modals, security warning
│   ├── ConversationPane.tsx    # Active conversation surface selection (map/raw/trace/repeater/room/chat/empty)
│   ├── visualizer/
│   │   ├── useVisualizerData3D.ts   # Packet→graph data pipeline, repeat aggregation, simulation state
│   │   ├── useVisualizer3DScene.ts  # Three.js scene lifecycle, buffers, hover/pin interaction
│   │   ├── VisualizerControls.tsx   # Visualizer legends and control panel overlay
│   │   ├── VisualizerTooltip.tsx    # Hover/pin node detail overlay
│   │   └── shared.ts                # Graph node/link types and shared rendering helpers
│   └── ...
├── utils/
│   ├── urlHash.ts              # Hash parsing and encoding
│   ├── conversationState.ts    # State keys, in-memory + localStorage helpers
│   ├── messageParser.ts        # Message text → rendered segments
│   ├── pathUtils.ts            # Distance/validation helpers for paths + map
│   ├── pubkey.ts               # getContactDisplayName (12-char prefix fallback)
│   ├── contactAvatar.ts        # Avatar color derivation from public key
│   ├── rawPacketIdentity.ts    # observation_id vs id dedup helpers
│   ├── rawPacketStats.ts       # Session packet stats windows, rankings, and coverage helpers
│   ├── regionScope.ts          # Regional flood-scope label/normalization helpers
│   ├── meshcoreOpenPayloads.ts # Rich MeshCore Open payload detection/rendering helpers
│   ├── textReplace.ts          # Shared message text substitution helpers
│   ├── pathHopWidthPreference.ts # LocalStorage persistence for hop-width display toggle
│   ├── richPayloadPreference.ts  # LocalStorage persistence for rich payload rendering toggle
│   ├── visualizerUtils.ts      # 3D visualizer node types, colors, particles
│   ├── visualizerSettings.ts   # LocalStorage persistence for visualizer options
│   ├── a11y.ts                 # Keyboard accessibility helper
│   ├── distanceUnits.ts        # Browser-local distance unit persistence/helpers
│   ├── lastViewedConversation.ts   # localStorage for last-viewed conversation
│   ├── contactMerge.ts            # Merge WS contact updates into list
│   ├── localLabel.ts              # Local label (text + color) in localStorage
│   ├── radioPresets.ts            # LoRa radio preset configurations
│   ├── publicChannel.ts           # Public-channel resolution helpers for routing/hash defaults
│   ├── fontScale.ts               # Browser-local relative font scale persistence/application
│   ├── theme.ts                   # Theme switching helpers
│   ├── autoFocusInput.ts          # Auto-focus input helper
│   ├── batteryDisplay.ts          # Battery level display helpers
│   ├── messageIdentity.ts         # Message identity/dedup helpers
│   ├── rawPacketInspector.ts      # Raw packet inspection helpers
│   ├── serverLoginState.ts        # Server login state helpers
│   └── statusDotPulse.ts          # Status dot pulse animation helpers
├── components/
│   ├── StatusBar.tsx
│   ├── Sidebar.tsx
│   ├── ChatHeader.tsx          # Conversation header (trace, favorite, delete)
│   ├── MessageList.tsx
│   ├── MessageInput.tsx
│   ├── NewMessageModal.tsx
│   ├── SearchView.tsx          # Full-text message search pane
│   ├── SettingsModal.tsx       # Layout shell — delegates to settings/ sections
│   ├── SecurityWarningModal.tsx # Startup warning for trusted-network / bot execution posture
│   ├── RawPacketList.tsx
│   ├── RawPacketFeedView.tsx   # Live raw packet feed + session stats drawer
│   ├── StatisticsView.tsx      # Read-only mesh network stats tool (incl. region-scope adoption)
│   ├── RawPacketDetailModal.tsx # On-demand packet inspector dialog
│   ├── MapView.tsx
│   ├── TracePane.tsx           # Multi-hop route trace builder/results view
│   ├── VisualizerView.tsx
│   ├── PacketVisualizer3D.tsx
│   ├── PathModal.tsx
│   ├── PathRouteMap.tsx
│   ├── CrackerPanel.tsx
│   ├── BotCodeEditor.tsx
│   ├── ContactAvatar.tsx
│   ├── ContactInfoPane.tsx     # Contact detail sheet (stats, name history, paths)
│   ├── ContactStatusInfo.tsx   # Contact status info component
│   ├── ContactPathDiscoveryModal.tsx # Forward/return path discovery dialog
│   ├── ContactRoutingOverrideModal.tsx # Manual direct-route override editor
│   ├── RepeaterDashboard.tsx   # Layout shell — delegates to repeater/ panes
│   ├── RepeaterLogin.tsx       # Repeater login form (password + guest)
│   ├── RoomServerPanel.tsx     # Room-server auth gate + status banner ahead of room chat
│   ├── ServerLoginStatusBanner.tsx # Shared repeater/room login state banner
│   ├── ChannelInfoPane.tsx     # Channel detail sheet (stats, top senders)
│   ├── ChannelFloodScopeOverrideModal.tsx # Per-channel flood-scope override editor
│   ├── ChannelPathHashModeOverrideModal.tsx # Per-channel path hash mode override editor
│   ├── BulkAddChannelResultModal.tsx # Results dialog for bulk channel creation
│   ├── CommandPalette.tsx      # Command palette overlay
│   ├── DirectTraceIcon.tsx     # Shared direct-trace glyph used in header/dashboard
│   ├── NeighborsMiniMap.tsx    # Leaflet mini-map for repeater neighbor locations
│   ├── settings/
│   │   ├── settingsConstants.ts          # Settings section type, ordering, labels
│   │   ├── SettingsRadioSection.tsx      # Name, keys, advert interval, max contacts, radio preset, freq/bw/sf/cr, txPower, lat/lon, reboot, mesh discovery
│   │   ├── SettingsLocalSection.tsx      # Browser-local settings: theme, relative font scale, local label, reopen last conversation
│   │   ├── SettingsFanoutSection.tsx     # Fanout integrations: MQTT, bots, config CRUD
│   │   ├── SettingsRadioAppSection.tsx    # Radio-App Management: tracked telemetry, contact management, blocked lists
│   │   ├── SettingsDatabaseSection.tsx   # Database: DB size, storage cleanup, auto-decrypt
│   │   ├── SettingsAboutSection.tsx     # Version, author, license, links
│   │   ├── ThemeSelector.tsx           # Color theme picker
│   │   └── BulkDeleteContactsModal.tsx # Bulk contact deletion dialog
│   ├── repeater/
│   │   ├── repeaterPaneShared.tsx        # Shared: RepeaterPane, KvRow, format helpers
│   │   ├── RepeaterTelemetryPane.tsx    # Battery, airtime, packet counts
│   │   ├── RepeaterNeighborsPane.tsx    # Neighbor table + lazy mini-map
│   │   ├── RepeaterAclPane.tsx          # Permission table
│   │   ├── RepeaterNodeInfoPane.tsx      # Repeater name, coords, clock drift
│   │   ├── RepeaterRadioSettingsPane.tsx # Radio config + advert intervals
│   │   ├── RepeaterRegionsPane.tsx      # Region hierarchy / flood-allowed region names
│   │   ├── RepeaterLppTelemetryPane.tsx # CayenneLPP sensor data
│   │   ├── RepeaterOwnerInfoPane.tsx    # Owner info + guest password
│   │   ├── RepeaterTelemetryHistoryPane.tsx # Historical telemetry chart/table
│   │   ├── RepeaterActionsPane.tsx      # Send Advert, Sync Clock, Reboot
│   │   └── RepeaterConsolePane.tsx      # CLI console with history
│   └── ui/                     # shadcn/ui primitives
├── types/
│   └── d3-force-3d.d.ts       # Type declarations for d3-force-3d
└── test/                      # Representative frontend test suites (not an exhaustive listing)
    ├── setup.ts
    ├── fixtures/websocket_events.json
    ├── api.test.ts
    ├── appFavorites.test.tsx
    ├── appStartupHash.test.tsx
    ├── conversationPane.test.tsx
    ├── contactAvatar.test.ts
    ├── contactInfoPane.test.tsx
    ├── integration.test.ts
    ├── mapView.test.tsx
    ├── messageCache.test.ts
    ├── messageList.test.tsx
    ├── messageParser.test.ts
    ├── rawPacketList.test.tsx
    ├── pathUtils.test.ts
    ├── prefetch.test.ts
    ├── rawPacketDetailModal.test.tsx
    ├── rawPacketFeedView.test.tsx
    ├── rawPacketIdentity.test.ts
    ├── repeaterDashboard.test.tsx
    ├── repeaterFormatters.test.ts
    ├── repeaterLogin.test.tsx
    ├── repeaterMessageParsing.test.ts
    ├── roomServerPanel.test.tsx
    ├── securityWarningModal.test.tsx
    ├── localLabel.test.ts
    ├── messageInput.test.tsx
    ├── newMessageModal.test.tsx
    ├── settingsModal.test.tsx
    ├── statisticsView.test.tsx
    ├── sidebar.test.tsx
    ├── statusBar.test.tsx
    ├── tracePane.test.tsx
    ├── unreadCounts.test.ts
    ├── urlHash.test.ts
    ├── appSearchJump.test.tsx
    ├── channelInfoKeyVisibility.test.tsx
    ├── chatHeaderKeyVisibility.test.tsx
    ├── searchView.test.tsx
    ├── useConversationActions.test.ts
    ├── useConversationMessages.test.ts
    ├── useConversationMessages.race.test.ts
    ├── useConversationNavigation.test.ts
    ├── useAppShell.test.ts
    ├── useBrowserNotifications.test.ts
    ├── useFaviconBadge.test.ts
    ├── useRepeaterDashboard.test.ts
    ├── useRememberedServerPassword.test.ts
    ├── useContactsAndChannels.test.ts
    ├── useRealtimeAppState.test.ts
    ├── useUnreadCounts.test.ts
    ├── useWebSocket.dispatch.test.ts
    ├── useWebSocket.lifecycle.test.ts
    ├── rawPacketStats.test.ts
    ├── fontScale.test.ts
    └── wsEvents.test.ts

```

## Architecture Notes

### State ownership

`App.tsx` is now a thin composition entrypoint over the hook layer. `AppShell.tsx` owns shell layout/composition:
- local label banner
- status bar
- desktop/mobile sidebar container
- search/settings surface switching
- global cracker mount/focus behavior
- new-message modal and info panes
- trusted-network `SecurityWarningModal`

High-level state is delegated to hooks:
- `useAppShell`: app-shell view state (settings section, sidebar, cracker, new-message modal)
- `useRadioControl`: radio health/config state, reconnect/reboot polling
- `useAppSettings`: settings CRUD, favorites, preferences migration
- `useContactsAndChannels`: contact/channel lists, creation, deletion
- `useConversationRouter`: URL hash → active conversation routing
- `useConversationNavigation`: search target, conversation selection reset, and info-pane state
- `useConversationActions`: send/resend/trace/path-discovery/block handlers and channel override updates
- `useConversationMessages`: conversation switch loading, embedded conversation-scoped cache, jump-target loading, pagination, dedup/update helpers, reconnect reconciliation, and pending ACK buffering
- `useUnreadCounts`: unread counters, mention tracking, recent-sort timestamps, server `last_read_ats`, and `first_unread_ids` (the unread-divider anchor)
- `useRealtimeAppState`: typed WS event application, reconnect recovery, cache/unread coordination
- `useRepeaterDashboard`: repeater dashboard state (login, pane data/retries, console, actions)

`App.tsx` intentionally still does the final `AppShell` prop assembly. That composition layer is considered acceptable here because it keeps the shell contract visible in one place and avoids a prop-bundling hook with little original logic.

**The overheard packet stream is the one piece of app state that deliberately does not live in React.** It is held in `stores/rawPacketStore.ts` and read through `useSyncExternalStore`, because it updates several times a second with every packet the node hears — far more often than anything else — and only four surfaces consume it (`MapView`, `VisualizerView`, `RawPacketFeedView`, `CrackerPanel`). Held in `App` state it re-rendered the entire tree, including `MessageList`, which is neither memoized nor cheap on a long history.

That gives the store a load-bearing invariant: **no ancestor of `MessageList` may call `useRawPackets()` / `useRawPacketStatsSession()`.** Nothing about the prop signatures enforces it — an innocuous-looking subscription added to `App`, `AppShell`, or `ConversationPane` silently restores the original slowdown. `src/test/appPacketIsolation.test.tsx` pins it by mounting the real ancestor chain and asserting `MessageList` does not re-render when packets arrive; it carries a negative control so the assertion cannot pass vacuously. Reach for packets in a new view by subscribing in that view, never by lifting them up.

`ConversationPane.tsx` owns the main active-conversation surface branching:
- empty state
- map view
- visualizer
- raw packet feed
- trace view
- repeater dashboard
- room-server auth/status gate before room chat
- normal chat chrome (`ChatHeader` + `MessageList` + `MessageInput`)

### Initial load + realtime

- Initial data: REST fetches (`api.ts`) for config/settings/channels/contacts/unreads.
- WebSocket: realtime deltas/events.
- On reconnect, the app refetches channels and contacts, refreshes unread counts, and reconciles the active conversation to recover disconnect-window drift.
- On WS connect, backend sends `health` only; contacts/channels still come from REST.

### New Message modal

`NewMessageModal` resets form state on close. The component instance persists across open/close cycles for smooth animations.

### Message behavior

- Outgoing sends are added to UI after the send API returns (not pre-send optimistic insertion), then persisted server-side.
- Backend also emits WS `message` for outgoing sends so other clients stay in sync.
- ACK/repeat updates arrive as `message_acked` events.
- Outgoing channel messages show a 30-second resend control; resend calls `POST /api/messages/channel/{message_id}/resend`.
- Conversation-scoped message caching now lives inside `useConversationMessages.ts` rather than a standalone `messageCache.ts` module. If you touch message timeline restore/dedup/reconnect behavior, start there.
- `contact_resolved` is a real-time identity migration event, not just a contact-list update. Changes in that area need to consider active conversation state, cached messages, unread state keys, and reconnect reconciliation together.

### Visualizer behavior

- `VisualizerView.tsx` hosts `PacketVisualizer3D.tsx` (desktop split-pane and mobile tabs).
- `PacketVisualizer3D.tsx` is now a thin composition shell over visualizer-specific hooks/components in `components/visualizer/`.
- `PacketVisualizer3D` uses persistent Three.js geometries for links/highlights/particles and updates typed-array buffers in-place per frame.
- Packet repeat aggregation keys prefer decoder `messageHash` (path-insensitive), with hash fallback for malformed packets.
- Raw-packet decoding in `RawPacketList.tsx` and `visualizerUtils.ts` relies on the multibyte-aware decoder fork; keep frontend packet parsing aligned with backend `path_utils.py`.
- Raw packet events carry both:
  - `id`: backend storage row identity (payload-level dedup)
  - `observation_id`: realtime per-arrival identity (session fidelity)
- Packet feed/visualizer render keys and dedup logic should use `observation_id` (fallback to `id` only for older payloads).
- The dedicated raw packet feed view now includes a frontend-only stats drawer. It tracks a separate lightweight per-observation session history for charts/rankings, so its windows are not limited by the visible packet list cap. Coverage messaging should stay honest when detailed in-memory stats history has been trimmed or the selected window predates the current browser session.

### Virtualization (`MessageList`)

The message list is windowed with `@tanstack/react-virtual`; only the visible rows are mounted, so render cost no longer scales with conversation length. Three details are load-bearing and easy to break:

- **`scrollMargin`** is measured from the virtual spacer's offset within the scroll container, because the container carries `p-4` and can show an "older messages" banner above the rows. Without it every `scrollToIndex` with `start`/`center` lands 16–48px high, and the error shifts as the banner appears during pagination. Rows must subtract it back out in their `translateY`.
- **The bottom-pin is deferred and re-asserted** across a bounded run of frames rather than performed once, because row heights start as estimates and a single `scrollToIndex` gets undone as they converge (completely so under StrictMode's double-invoked effects). It is cancelled by a pending `targetMessageId` and by any deliberate scroll gesture.
- **`getItemKey` returns a string sentinel** for indices past the end of a shrunken list; a bare index would collide with the numeric message-id keyspace and poison the measurement cache.

jsdom has no layout engine, so none of this is observable from the vitest suite — it needs a real browser.

### Radio settings behavior

- `SettingsRadioSection.tsx` surfaces `path_hash_mode` only when `config.path_hash_mode_supported` is true.
- `SettingsRadioSection.tsx` also exposes `multi_acks_enabled` as a checkbox for the radio's extra direct-ACK transmission behavior.
- Advert-location control is intentionally only `off` vs `include node location`. Companion-radio firmware does not reliably distinguish saved coordinates from live GPS in this path.
- The advert action is mode-aware: the radio settings section exposes both flood and zero-hop manual advert buttons, both routed through the same `onAdvertise(mode)` seam.
- Mesh discovery in the radio section is limited to node classes that currently answer discovery control-data requests in firmware: repeaters and sensors.
- Frontend `path_len` fields are hop counts, not raw byte lengths; multibyte path rendering must use the accompanying metadata before splitting hop identifiers.

## WebSocket (`useWebSocket.ts`)

- Auto reconnect (3s) with cleanup guard on unmount.
- Heartbeat ping every 30s.
- Incoming JSON is parsed through `wsEvents.ts`, which validates the top-level envelope and known event type strings, then casts payloads at the handler boundary. It does not schema-validate per-event payload shapes.
- Event handlers: `health`, `message`, `contact`, `contact_resolved`, `channel`, `raw_packet`, `message_acked`, `contact_deleted`, `channel_deleted`, `error`, `success`, `pong` (ignored).
- For `raw_packet` events, use `observation_id` as event identity; `id` is a storage reference and may repeat.

## URL Hash Navigation (`utils/urlHash.ts`)

Supported routes:
- `#raw`
- `#map`
- `#map/focus/{pubkey_or_prefix}`
- `#visualizer`
- `#search`
- `#trace`
- `#statistics`
- `#settings/{section}`
- `#channel/{channelKey}`
- `#channel/{channelKey}/{label}`
- `#contact/{publicKey}`
- `#contact/{publicKey}/{label}`

Where `{section}` is one of `radio`, `local`, `radio-app`, `database`, `fanout`, or `about`.

Legacy name-based channel/contact hashes are still accepted for compatibility, and the
pre-move `#settings/statistics` hash redirects to the `#statistics` tool.

## Conversation State Keys (`utils/conversationState.ts`)

`getStateKey(type, id)` produces:
- channels: `channel-{channelKey}`
- contacts: `contact-{publicKey}`

Use full contact public key here (not 12-char prefix).

`conversationState.ts` keeps an in-memory cache and localStorage helpers used for migration/compatibility.
Canonical persistence for unread and sort metadata is server-side (`app_settings` + read-state endpoints).

## Utilities

### `utils/pubkey.ts`

Current public export:
- `getContactDisplayName(name, pubkey)`

It falls back to a 12-char prefix when `name` is missing.

### `utils/pathUtils.ts`

Distance/validation helpers used by path + map UI.

## Types and Contracts (`types.ts`)

`AppSettings` currently includes:
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

Note: MQTT, bot, and community MQTT settings were migrated to the `fanout_configs` table (managed via `/api/fanout`). They are no longer part of `AppSettings`.

`HealthStatus` includes `fanout_statuses: Record<string, FanoutStatusEntry>` mapping config IDs to `{name, type, status}`. Also includes `bots_disabled: boolean`.

`FanoutConfig` represents a single fanout integration: `{id, type, name, enabled, config, scope, sort_order, created_at}`.

`RawPacket.decrypted_info` includes `channel_key` and `contact_key` for MQTT topic routing.

`UnreadCounts` includes `counts`, `mentions`, `last_message_times`, `last_read_ats`, and `first_unread_ids`.

The unread divider is anchored to `first_unread_ids` — the id of the oldest unread message per conversation — not to a timestamp. `MessageList` locates it with `findIndex(msg.id === unreadMarkerMessageId)`, which returns `-1` when that message is not in the loaded window; that is the signal to offer "Jump to unread" (routed through the `targetMessageId`/`getMessagesAround` path) rather than render a divider. Locating by timestamp instead would return index 0 whenever the boundary sits further back than the loaded window, silently placing the divider on the wrong message.

Counts are incremented live over WebSocket while `first_unread_ids` only arrives with a full `/read-state/unreads` fetch, so `useUnreadCounts.incrementUnread` seeds the boundary itself on the read→unread transition. A channel going unread while the app is open would otherwise have a count but no boundary, and no divider at all.

## Contact Info Pane

Clicking a contact's avatar in `ChatHeader` or `MessageList` opens a `ContactInfoPane` sheet (right drawer) showing comprehensive contact details fetched from `GET /api/contacts/analytics` using either `?public_key=...` or `?name=...`:

- Header: avatar, name, public key, type badge, on-radio badge
- Info grid: last seen, first heard, last contacted, distance, hops
- GPS location (clickable → map)
- On-demand LPP telemetry: "Request" button fetches `POST /contacts/{key}/telemetry`, displays sensor readings via `LppSensorRow`, optional GPS mini-map (Leaflet), and history chart (Recharts). Opt-in tracking toggle uses `POST /settings/tracked-telemetry-contacts/toggle`.
- Favorite toggle
- Name history ("Also Known As") — shown only when the contact has used multiple names
- Message stats: DM count, channel message count
- Most active rooms (clickable → navigate to channel)
- Route details from the canonical backend surface (`effective_route`, `effective_route_source`, `direct_route`, `route_override`)
- Advert observation rate
- Nearest repeaters (resolved from first-hop path prefixes)
- Recent advert paths (informational only; not part of DM route selection)

State: `useConversationNavigation` controls open/close via `infoPaneContactKey`. Live contact data from WebSocket updates is preferred over the initial detail snapshot.

## Channel Info Pane

Clicking a channel name in `ChatHeader` opens a `ChannelInfoPane` sheet (right drawer) showing channel details fetched from `GET /api/channels/{key}/detail`:

- Header: channel name, key (clickable copy), type badge (hashtag/private key), on-radio badge
- Favorite toggle
- Message activity: time-windowed counts (1h, 24h, 48h, 7d, all time) + unique senders
- First message date
- Top senders in last 24h (name + count)

State: `useConversationNavigation` controls open/close via `infoPaneChannelKey`. Live channel data from the `channels` array is preferred over the initial detail snapshot.

## Repeater Dashboard

For repeater contacts (`type=2`), `ConversationPane.tsx` renders `RepeaterDashboard` instead of the normal chat UI (ChatHeader + MessageList + MessageInput).

**Login**: `RepeaterLogin` component — password or guest login via `POST /api/contacts/{key}/repeater/login`. The frontend sends exactly one request; the backend internally escalates a timed-out login to one flood retry (see `app/AGENTS.md` § "Server login route escalation"), so a single call may take up to two response windows. Do not add a client-side login retry loop on top — a `LOGIN_FAILED` result means the password was refused, not that the route needs another attempt.

**Dashboard panes** (after login): Telemetry, Node Info, Neighbors, ACL, Radio Settings, Regions, Advert Intervals, Owner Info — each fetched via granular `POST /api/contacts/{key}/repeater/{pane}` endpoints. The Regions pane prefers the admin CLI hierarchy and falls back to the guest anon flood-allowed names, so its payload carries a `source` of `cli` or `anon`. Panes retry up to 3 times client-side. `Neighbors` depends on the smaller `node-info` fetch for repeater GPS, not the heavier radio-settings batch. "Load All" fetches all panes serially (parallel would queue behind the radio lock).

**Actions pane**: Send Advert, Sync Clock, Reboot — all send CLI commands via `POST /api/contacts/{key}/command`.

**Console pane**: Full CLI access via the same command endpoint. History is ephemeral (not persisted to DB).

All state is managed by `useRepeaterDashboard` hook. State resets on conversation change.

## Room Server Panel

For room contacts (`type=3`), `ConversationPane.tsx` keeps the normal chat surface but inserts `RoomServerPanel` above it. That panel handles room-server login/status messaging and gates room chat behind the room-authenticated state when required.

`ServerLoginStatusBanner` is shared between repeater and room login surfaces for inline status/error display.

**Auto-open + sync:** on open, the panel fetches `api.getRoomPoll`; if a credential is stored server-side (`has_stored_credential`, which includes a guest `""` credential — checked as such, never by truthiness) it auto-logs-in with `api.roomLogin(key, { useStoredCredential: true })` and skips the password form, falling back to the form on failure. The authenticated view has a "Keep this room synced" toggle + interval that drives `api.setRoomPoll`; enabling it stores the current session's credential (`credential_action: 'set'`, `credential` may be `""` for guest) so the backend poller can log in unattended. The plaintext credential is never sent back to the client — the status payload carries booleans only.

## Message Search Pane

The `SearchView` component (`components/SearchView.tsx`) provides full-text search across all DMs and channel messages. Key behaviors:

- **State**: `targetMessageId` is shared between `useConversationNavigation` and `useConversationMessages`. When a search result is clicked, `handleNavigateToMessage` sets the target ID and switches to the target conversation.
- **Same-conversation clear**: when `targetMessageId` is cleared after the target is reached, the hook preserves the around-loaded mid-history view instead of replacing it with the latest page.
- **Persistence**: `SearchView` stays mounted after first open using the same `hidden` class pattern as `CrackerPanel`, preserving search state when navigating to results.
- **Jump-to-message**: `useConversationMessages` handles optional `targetMessageId` by calling `api.getMessagesAround()` instead of the normal latest-page fetch, loading context around the target message. `MessageList` resolves the target to an index and calls `virtualizer.scrollToIndex(...)`, then applies a `message-highlight` CSS animation. A pending target suppresses the bottom-pin (see Virtualization below), since the around-load clears the list first and would otherwise be yanked to the newest message.
- **Bidirectional pagination**: After jumping mid-history, `hasNewerMessages` enables forward pagination via `fetchNewerMessages`. The scroll-to-bottom button calls `jumpToBottom` (re-fetches latest page) instead of just scrolling.
- **WS message suppression**: When `hasNewerMessages` is true, incoming WS messages for the active conversation are not added to the message list (the user is viewing historical context, not the latest page).

## Web Push Notifications

Web Push allows notifications even when the browser tab is closed. Requires HTTPS (self-signed OK).

- **Service worker**: `frontend/public/sw.js` handles `push` events (show notification) and `notificationclick` (focus/open tab, navigate via `url_hash`). Registered in `main.tsx` on secure contexts only.
- **`usePushSubscription` hook**: manages the full subscription lifecycle — subscribe (register SW → `PushManager.subscribe()` → POST to backend), unsubscribe, global push-conversation toggles, device listing, and deletion.
- **ChatHeader integration**: `BellRing` icon (amber when active) appears next to the existing desktop notification `Bell` on secure contexts. First click subscribes the browser and enables push for that conversation; subsequent clicks toggle the conversation on/off.
- **Settings > Local**: `PushDeviceManagement` component shows subscription status, lists all registered devices with test/delete buttons. Uses `usePushSubscription` hook directly.
- Auto-generates device labels from User-Agent (e.g., "Chrome on macOS").
- `PushSubscriptionInfo` type in `types.ts`; API methods in `api.ts`.

## Styling

UI styling is mostly utility-class driven (Tailwind-style classes in JSX) plus shared globals in `index.css` and `styles.css`.
Do not rely on old class-only layout assumptions.

### Canonical style reference

`SettingsLocalSection.tsx` contains a **ThemePreview** component with a collapsible "Canonical style reference" section. This is the authoritative catalog of text sizes, button variants, badge patterns, and interactive elements used throughout the app. **When adding or modifying UI, match the patterns shown there rather than inventing new ones.**

Key conventions documented in the reference:

- **Text sizes** use `rem`-based Tailwind values so they scale with the user's font-size slider. Do not use hard-locked `px` values (e.g., `text-[10px]`). The canonical sizes are `text-[0.625rem]` (10px), `text-[0.6875rem]` (11px), `text-[0.8125rem]` (13px), plus standard Tailwind `text-xs`/`text-sm`/`text-base`/`text-lg`/`text-xl`.
- **Group titles** (sub-section headings within settings tabs) use `<h3 className="text-base font-semibold tracking-tight">`. These separate major groups like "Connection", "Identity", "MQTT Broker". When a group contains named sub-items (e.g. "Contact Management" → "Blocked Contacts", "Bulk Delete"), use `<h4 className="text-sm font-semibold">` for the children and nest them inside the parent group's `div` instead of separating with `<Separator />`.
- **Helper / description text** uses `text-[0.8125rem] text-muted-foreground` (13px). This is for explanatory paragraphs under inputs or sections — not for metadata, timestamps, or alert text which stay at `text-xs`.
- **Metadata labels** use `text-[0.625rem] uppercase tracking-wider text-muted-foreground font-medium` for compact category tags like "Push-enabled conversations" or "Registered Devices".
- **Buttons** use the shadcn `<Button>` component. Semantic color overrides (danger, warning, success) use `variant="outline"` with `className="border-{color}/50 text-{color} hover:bg-{color}/10"`.
- **Badges/tags** use `text-[0.625rem] uppercase tracking-wider px-1.5 py-0.5 rounded` with `bg-muted` (neutral) or `bg-primary/10` (active).
- **Clickable text** (copy-to-clipboard, navigational links) uses `role="button" tabIndex={0}` with `cursor-pointer hover:text-primary transition-colors`.

### Region-scope adoption panel

`StatisticsView.tsx` (sidebar › Tools › Statistics) renders `stats.region_scope_24h` via `RegionScopeStatsPanel`. Two presentation rules exist because regional adoption is currently very sparse, and both are deliberate:

- **Fractions, not bare percentages.** "3 of 117" carries the sample size that "2.6%" hides.
- **The traffic percentage is withheld** when the scoped count is at or below `false_positive_floor` (corrupt-capture noise) or when the share would round to `0.0%`. The floor caveat is always shown alongside a non-zero scoped count. The sender figure is never suppressed — it requires successful decryption and so carries no noise.

Traffic and sender figures use different denominators (all channels vs. decryptable-only) and are not expected to match.

## Security Posture (intentional)

- No authentication UI.
- Frontend assumes trusted network usage.
- Bot editor intentionally allows arbitrary backend bot code configuration.

## Testing

Run all quality checks (backend + frontend) from the repo root:

```bash
./scripts/quality/all_quality.sh
```

Or run frontend checks individually:

```bash
cd frontend
npm run test:run
npm run build
```

`npm run packaged-build` is release-only. It writes the fallback `frontend/prebuilt`
directory used by the downloadable prebuilt release zip; normal development and
validation should stick to `npm run build`.

When touching cross-layer contracts, also run backend tests from repo root:

```bash
PYTHONPATH=. uv run pytest tests/ -v
```

## Errata & Known Non-Issues

### Contacts use mention styling for unread DMs

This is intentional. In the sidebar, unread direct messages for actual contact conversations are treated as mention-equivalent for badge styling. That means both the Contacts section header and contact unread badges themselves use the highlighted mention-style colors for unread DMs, including when those contacts appear in Favorites. Repeaters do not inherit this rule, and channel badges still use mention styling only for real `@[name]` mentions.

### RawPacketList autoscroll

`RawPacketList` sticks to the latest packet on every update when its `autoScroll` prop is true (the default). `RawPacketFeedView` exposes an "Autoscroll" checkbox next to the type filters (default ticked, session-only — intentionally not persisted) so users can pause scrolling to correlate older packets. Toggling it back on jumps to the bottom immediately (`autoScroll` is an effect dependency).

## Editing Checklist

1. If API/WS payloads change, update `types.ts`, handlers, and tests.
2. If URL/hash behavior changes, update `utils/urlHash.ts` tests.
3. If read/unread semantics change, update `useUnreadCounts` tests.
4. Keep this file concise; prefer source links over speculative detail.
