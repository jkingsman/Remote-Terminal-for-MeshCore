## [Unreleased]

* Feature: Bots workspace — a new top-level view (sidebar › Tools › Bots) merging meshcore-bot's functionality: the full meshcore-bot library as 49 built-in bots (weather, solar, mesh info & tracing, sports, fun, emergency alerts, admin tools…) seeded as editable Python scripts in the database, with per-bot settings panels, scope/limits, an in-app code editor, a sandboxed test console, and per-bot run history
* Feature: One bot, many triggers — keyword commands, cron schedules (5-field crontab + @presets, day-of-week 0=Monday), mesh events (new contact), and token-gated inbound webhooks (`POST /api/hooks/{slug}`); new `ctx` API supports cross-channel sends, DMs, persistent state, HTTP, geocoding, and i18n
* Feature: Scheduler tab — standalone cron messages to any channel with mesh-stat placeholders and optional region scoping
* Feature: Feeds tab — RSS/JSON-API subscriptions posting new items to channels, with format templates, previews, and an SSRF guard
* Feature: Bots dashboard, live engine log view (WebSocket), and Engine settings (command prefix, mentions, rate limits, language auto-detection with 10 ported locales, banned users, profanity filter, global admin list)
* Feature: Discord and Telegram one-way channel bridges as fanout integrations
* Feature: Multibyte Rollout panel in Settings › Statistics — node-level multibyte path adoption (contacts and repeaters by direct-route hop width), complementing the packet-level Path Hash Width chart
* Note: meshcore-bot's channelpause and reload commands are intentionally not ported — the workspace's per-bot enable toggles, the disable-all kill switch, and hot reload on save supersede them
* Change: Settings › "MQTT & Automation" is now "Integrations"; legacy fanout Python bots migrate automatically into the Bots workspace (migration 064) and keep their exact behavior via the legacy `def bot(**kwargs)` wrapper

## [3.17.1] - 2026-07-26

* Feature: Auto-retry with flood for no-response-heard repeater login
* Misc: Optimize frontend data handling for improved performance
* Misc: Doc drift, test improvements, and library updates

## [3.17.0] - 2026-07-21

* Feature: Add toggleable UI tweak for hop width display inline on channel messages
* Feature: Launch packet analyzer from visualizer feed
* Feature: Add customizable date binning on map view
* Feature: Add repeater telemetry export as CSV
* Misc: Tidy up Github issue templates

## [3.16.2] - 2026-07-10

* Feature: Zero hop repeater region discover

## [3.16.1] - 2026-07-10

* Feature: Try using direct admin-binary fetch for repeater owner
* Feature: Add packet search to raw feed
* Feature: Add repeater region display
* Bug: Misc bugs around dm region scope + scope display, and flood-scope leak
* Misc: Add clearer error on missing privkey export for community MQTT
* Misc: Fix flaky test

## [3.16.0] - 2026-07-08

* Feature: Add incoming message region to bot kwargs
* Feature: Allow bots to send region scoped messages
* Feature: Show total neighbor count from repeater even if we don't successfully fetch all of them
* Feature: Add duty cycle setting display to repeaters
* Feature: Allow for empty-override regions
* Feature: Use request-scoped subscriptions for console commands (should make repeater comms much more reliable!)
* Bug: Clear out stale HA states
* Bug: Fix floating point gnarliness on recharts
* Bug: Be more forgiving around hashtag channel names
* Bug: More aggressive packet validity checking
* Bug: Defer chart render until node info drawer is done flying in
* Bug: Fix issue in gif rendering with mentions
* Misc: Suppress notification on expected JWT renewal community MQTT reconnect
* Misc: Add issue template

## [3.15.2] - 2026-06-23

* Bugfix: filter out geo from non-geo sensors, and publish gps for device tracking

## [3.15.1] - 2026-06-20

* Feature: Per-message + analyzer region tag support
* Feature: Sortable neighbor list in repeater pane
* Feature: Option to disable autoscroll on packet feed
* Feature: Bot globals
* Feature: Add draft support for Open gifs + reactions
* Bugfix: Use correct last-heard time for repeater recency
* Bugfix: Configurable VAPID subject for iOS web push
* Misc: Library updates, logging, test

## [3.15.0] - 2026-06-11

* Feature: Enhanced repeater telemetry with scrubbing and better extents
* Feature: Outbound message opt-in for Apprise
* Feature: Reverse-link button on trace pane
* Feature: Add recently traced contacts as own category in repeater pane
* Feature: More compact trace pane display
* Bugfix: Scavenge ACK codes for standalone acks, resolving issues with DM ack detection
* Bugfix: Proper timestamps for community MQTT
* Bugfix: Clearer packet history legend in packet view
* Misc: Add pubkey suffix to repeater neighbors
* Misc: Dependency bumps & test fixes

## [3.14.1] - 2026-06-01

* Feature: Enhance online documentation
* Feature: Chain nav to browser history state
* Feature: Add packet_hash to bot kwargs
* Bug: Fix amp/ma units for HA integration of LPP sensors
* Bug: Don't display blocked contacts on the map
* Bug: Don't trim trailing space from repeater console commands
* Bug: Make the trace pane not unusable with a bunch of hops or a bunch of recents
* Misc: Dependency bumps + test updates

## [3.14.0] - 2026-05-13

* Feature: Support active/intervalized contact telemetry gathering + HA forwarding
* Feature: Stable packet analyzer chart coloring
* Feature: Add packet scope to inscpection
* Feature: Support websocket path config for community mqtt
* Bugfix: Drop token renewal time to 1hr for more sensitive services
* Bugfix: Don't forward unparseable packets to communitya ggregators
* Bugfix: Persist login status for rooms
* Bugfix: Fix gap in repeater/contact/sensor non-ingest logic
* Misc: Revise hop-length buckets to reflect path bit width
* Misc: Remove autocomplete from textarea
* Misc: Test & Dependency updates

## [3.13.0] - 2026-04-30

* Feature: Error counts included in repeater telemetry
* Feature: RX error rate + percentage surfaced and tracked for repeaters
* Feature: Dynamic as-you-type text replacement for Cyrillic byte optimization
* Feature: Permit hourly checks for direct/routed repeaters
* Feature: Allow newlines in input
* Feature: Packet-send radio time added to packet analyzer
* Feature: Enable forced plaintext for Apprise
* Bugfix: Less annoying MQTT failure notifications with backoff
* Bugfis: Don't obscure input; use dvh everywhere
* Bugfix: Clearer save button for advert interval
* Misc: Library updates
* Misc: Rewrite 5xx to 4xx to avoid issues with proxies that don't react well to 503/504

## [3.12.3] - 2026-04-24

* Feature: Customizable Apprise strings
* Feature: Choose contact addition type
* Featuer: Make bulk-delete sortable by last-heard
* Misc: Bypass error on fail-to-unload-contact when it's not there
* Misc: Docs & test updates

## [3.12.2] - 2026-04-21

* Feature: Auto-disambiguate colliding LPP sensor names
* Feature: Radio config import/export
* Bugfix: Don't push stale firmware version/model on community MQTT
* Misc: Expose env vars in debug blob
* Misc: Longer linger for web push error
* Misc: Docs, test, & CI/CD improvements

## [3.12.1] - 2026-04-19

* Feature: Auto-evict/circular-buffer contact load mode (solves potential T-Beam issues)
* Feature: Channel mute
* Misc: HA Documentation improvements
* Misc: Bump deps & update tests
* Misc: Improve warnings around web push in untrusted contexts

## [3.12.0] - 2026-04-17

* Feature: Web Push -- get your mesh notifications on a locked phone or when your browser is closed!
* Feature: Add link to node from map display
* Feature: Map layers
* Feature: Better contact/channel selection for fanout
* Feature: Add glittering status dot option
* Feature: Add airtime math and average packets/min for repeater info displays
* Feature: Offer multiple timing intervals for repeater telemetry aurofetch
* Feature: Add ability to follow OS light/dark mode
* Bugfix: Clear 100% of messages from radio in fallback mode; don't stop at 100
* Bugfix: Don't stop DM retry just because the radio did not provide a radio ack on the wire
* Bugfix: Don't strip outgoing colons on DMs or room servers
* Bugfix: Patch statusbar overlap on PWA
* Bugfix: Patch default map upload URL
* Bugfix: Show learned path in routing override
* Bugfix: Centralize on "only means RF heard" for first_seen/last_seen
* Misc: Reduce frequency of time set failure chirping
* Misc: QoL improvements for Home Assistant integration
* Misc: Overhaul settings styling
* Misc: Documentation + tests updates

## [3.11.3] - 2026-04-12

* Bugfix: Add icons and screenshots for webmanifest
* Bugfix: Use incoming DMs, not just outgoing, for recency ranking for preferential radio contact load

## [3.11.2] - 2026-04-12

* Feature: Unread DMs are always at the top of the DM list no matter what
* Bugfix: Webmanifest needs withCredentials

## [3.11.1] - 2026-04-12

* Feature: Home Assistant MQTT fanout
* Feature: Add dummy service worker to enable PWA
* Bugfix: DB connection plurality issues
* Misc: Migration improvements
* Misc: Search keys from beginning

## [3.11.0] - 2026-04-10

* Feature: Radio health and contact data accessible on fanout bus
* Feature: Local node radio stats (voltage etc.) on WS health bus
* Feature: Battery indicator optional in status bar (configured in Local Settings)
* Bugfix: Fix same-second same-message collision in room servers
* Bugfix: Don't consume DM resend attempt if the radio was just busy
* Bugfix: Assume that a same-second same-message same-first-byte-key DM is more likely an echo than them sending the same message
* Bugfix: Multi-retry for flood scope restoration
* Misc: Testing & documentation improvements

## [3.10.0] - 2026-04-10

* Feature: Add Arch AUR package
* Feature: 72hr packet density view in statistics
* Feature: Add warnings for event loop selection for MQTT on Windows startup
* Bugfix: Bump Apprise to 1.9.9 to fix Matrix bug
* Misc: More memory-conscious on recent contact fetch
* Misc: Fix statistics pane e2e test

## [3.9.0] - 2026-04-06

* Feature: Add hop counts to hop-width selection options
* Feature: Show cached repeater telemetry inline in settings
* Feature: Retain recent traces and make them click-to-re-run
* Feature: Autofocus channel/DM textbox on desktop
* Feature: Favorites on the radio are now imported as favorites
* Bugfix: Be clearer on issue identification for missing HTTPS context in channel finder
* Bugfix: Don't use sender timestamp for message sequence display
* Bugfix: Function on subdomains happily
* Misc: Be gentler, room s/cracker/finder/
* Misc: Test and frontend correctness & test fixes
* Misc: Don't repeat clock sync failure logs
* Misc: Make warning in readme clearer about taking over the radio
* Misc: Improve readme phrasings
* Misc: Better y-axis selection for battery read-out
* Misc: Provide clearer warning on docker setup without docker installed
* Misc: Default visualizer stale pruning to on/5 minutes
* Misc: Migrate favorites to better storage pattern
* Misc: Provide dumper script for API + WS interfaces for prep for HA integration

## [3.8.0] - 2026-04-03

* Feature: Per-channel hop width override
* Feature: Intervalized repeater telemetry collection
* Feature: Auto-resend option for byte-perfect resends on no repeater echo
* Feature: Attach RSSI/SNR to received packets
* Feature: Add motion packet display to map
* Feature: Map dark mode
* Bugfix: Make DB indices more useful around capitalization
* Misc: Bump required Python to 3.11
* Misc: Performance, documentation, and test improvements
* Misc: More yields during long radio operations
* Misc: Dead code & crufty test removal
* Misc: Remove all but stub frontend favorites migration for very very old versions

## [3.7.1] - 2026-04-02

* Feature: Redact Apprise URLs to prevent sensitive information disclosure

## [3.7.0] - 2026-04-02

* Feature: Repeater battery tracking
* Feature: Repeater info pane just like contacts
* Feature: Make repeaters blockable
* Feature: Add new-node advert blocking
* Feature: Add bulk deletion interface
* Feature: Bulk room add on alt+click of new channel button
* Feature: More info in debug endpoint
* Bugfix: Be more conservative around radio load limits and don't exceed radio-reported capacity
* Misc: Default auto-DM decrypt to true
* Misc: Reorganize some settings panes
* Misc: Enable FK pragma
* Misc: Various performance and correctness fixes
* Misc: Correct TCP default port

## [3.6.7] - 2026-03-31

* Misc: Remove armv7 (for now)

## [3.6.6] - 2026-03-31

* Misc: Please I'm begging for the build scripts to be working now

## [3.6.5] - 2026-03-31

* Bugfix: Maybe fix problem with publish script

## [3.6.4] - 2026-03-31

* Feature: Clarify New Channel/Contact button
* Bugfix: Rename "Best RSSI" to "Strongest Neighbor"
* Bugfix: Improve layout of Trace pane
* Misc: Docker setup improvements

## [3.6.3] - 2026-03-30

* Feature: Add multi-byte trace
* Feature: Show node name on discovered node if we know it
* Feature: Add docker installation script
* Feature: Add historical noise floor to stats
* Feature: Add trace tool
* Bugfix: 100x performance on statistics endpoint with indices and better queries
* Misc: Performance and correctness improvements for backend-of-the-frontend
* Misc: Reorganize scripts

## [3.6.2] - 2026-03-29

* Feature: Be more flexible about timing and volume of full contact offload
* Feature: Improve room server and repeater ops to be much more clearer about auth status
* Feature: Show last error status on integrations
* Feature: Push multi-platform docker builds
* Bugfix: Fix advert interval time unit display
* Bugfix: Don't cast RSSI/SNR to string for community MQTT
* Bugfix: Map uploader follows redirect
* Misc: Thin out unnecessary cruft in unreads endpoint
* Misc: Fall back gracefully if linked to an unknown contact

## [3.6.1] - 2026-03-26

* Feature: MeshCore Map integration
* Feature: Add warning screen about bots
* Feature: Favicon reflects unread message state
* Feature: Show hop map in larger modal
* Feature: Add prebuilt frontend install script
* Feature: Add clean service installer script
* Feature: Swipe in to show menu
* Bugfix: Invalid backend API path serves error, not fallback index
* Bugfix: Fix some spacing/page height issues
* Misc: Misc. bugfixes and performance and test improvements

## [3.6.0] - 2026-03-22

* Feature: Add incoming-packet analytics
* Feature: BYOPacket for analysis
* Feature: Add room activity to stats view
* Bugfix: Handle Heltec v3 serial noise
* Misc: Swap repeaters and room servers for better ordering

## [3.5.0] - 2026-03-19

* Feature: Add room server alpha support
* Feature: Add option to force-reset node clock when it's too far ahead
* Feature: DMs auto-retry before resorting to flood
* Feature: Add impulse zero-hop advert
* Feature: Utilize PATH packets to correctly source a contact's route
* Feature: Metrics view on raw packet pane
* Feature: Metric, Imperial, and Smoots are now selectable for distance display
* Feature: Allow favorites to be sorted
* Feature: Add multi-ack support
* Feature: Password-remember checkbox on repeaters + room servers
* Bugfix: Serialize radio disconnect in a lock
* Bugfix: Fix contact bar layout issues
* Bugfix: Fix sidebar ordering for contacts by advert recency
* Bugfix: Fix version reporting in community MQTT
* Bugfix: Fix Apprise duplicate names
* Bugfix: Be better about identity resolution in the stats pane
* Misc: Docs, test, and performance enhancements
* Misc: Don't prompt "Are you sure" when leaving an unedited integration
* Misc: Log node time on startup
* Misc: Improve community MQTT error bubble-up
* Misc: Unread DMs always have a red unread counter
* Misc: Improve information in the debug view to show DB status

## [3.4.1] - 2026-03-16

* Bugfix: Improve handling of version information on prebuilt bundles
* Bugfix: Improve frontend usability on disconnected radio
* Misc: Docs and readme updates
* Misc: Overhaul DM ingest and frontend state handling

## [3.4.0] - 2026-03-16

* Feature: Add radio model and stats display
* Feature: Add prebuilt frontends, then deleted that and moved to prebuilt release artifacts
* Bugfix: Misc. frontend performance and correctness fixes
* Bugfix: Fix same-second same-content DM send collition
* Bugfix: Discard clearly-wrong GPS data
* Bugfix: Prevent repeater clock skew drift on page nav
* Misc: Use repeater's advertised location if we haven't loaded one from repeater admin
* Misc: Don't permit invalid fanout configs to be saved ever`

## [3.3.0] - 2026-03-13

* Feature: Use dashed lines to show collapsed ambiguous router results
* Feature: Jump to unread
* Feature: Local channel management to prevent need to reload channel every time
* Feature: Debug endpoint
* Feature: Force-singleton channel management
* Feature: Local node discovery
* Feature: Node routing discovery
* Bugfix: Don't tell users to us npm ci
* Bugfix: Fallback polling dm message persistence
* Bugfix: All native-JS inputs are now modals
* Bugfix: Same-second send collision resolution
* Bugfix: Proper browser updates on resend
* Bugfix: Don't use last-heard when we actually want last-advert for path discovery for nodes
* Bugfix: Don't treat prefix-matching DM echoes as acks like we do for channel messages
* Misc: Visualizer data layer overhaul for future map work
* Misc: Parallelize docker tests

## [3.2.0] - 2026-03-12

* Feature: Improve ambiguous-sender DM handling and visibility
* Feature: Allow for toggling of node GPS broadcast
* Feature: Add path width to bot and move example to full kwargs
* Feature: Improve node map color contrast
* Bugfix: More accurate tracking of contact data
* Bugfix: Misc. frontend performance and bugfixes
* Misc: Clearer warnings on user-key linkage
* Misc: Documentation improvements

## [3.1.1] - 2026-03-11

* Feature: Add basic auth
* Feature: SQS fanout
* Feature: Enrich contact info pane
* Feature: Search operators for node and channel
* Feature: Pause radio connection attempts from Radio settings
* Feature: New themes! What a great use of time!
* Feature: Github workflows runs for validation
* Bugfix: More consistent log format with times
* Bugfix: Patch meshcore_py bluetooth eager reconnection out during pauses

## [3.1.0] - 2026-03-11

* Feature: Add basic auth
* Feature: SQS fanout
* Feature: Enrich contact info pane
* Feature: Search operators for node and channel
* Feature: Pause radio connection attempts from Radio settings
* Feature: New themes! What a great use of time!
* Feature: Github workflows runs for validation
* Bugfix: More consistent log format with times
* Bugfix: Patch meshcore_py bluetooth eager reconnection out during pauses

## [3.0.0] - 2026-03-10

* Feature: Custom regions per-channel
* Feature: Add custom contact pathing
* Feature: Corrupt packets are more clear that they're corrupt
* Feature: Better, faster patterns around background fetching with explicit opt-in for recurring sync if the app detects you need it
* Feature: More consistent icons
* Feature: Add per-channel local notifications
* Feature: New themes
* Feature: Massive codebase refactor and overhaul
* Bugfix: Fix packet parsing for trace packets
* Bugfix: Refetch channels on reconnect
* Bugfix: Load All on repeater pane on mobile doesn't extend into lower text
* Bugfix: Timestamps in logs
* Bugfix: Correct wrong clock sync command
* Misc: Improve bot error bubble up
* Misc: Update to non-lib-included meshcore-decoder version
* Misc: Revise refactors to be more LLM friendly
* Misc: Fix script executability
* Misc: Better logging format with timestamp
* Misc: Repeater advert buttons separate flood and one-hop
* Misc: Preserve repeater pane on navigation away
* Misc: Clearer iconography and coloring for status bar buttons
* Misc: Search bar to top bar

## [2.7.9] - 2026-03-08

* Bugfix: Don't obscure new integration dropdown on session boundary

## [2.7.8] - 2026-03-08

* Bugfix: Improve frontend asset resolution and fixup the build/push script

## [2.7.1] - 2026-03-08

* Bugfix: Fix historical DM packet length passing
* Misc: Follow better inclusion patterns for the patched meshcore-decoder and just publish the dang package
* Misc: Patch a bewildering browser quirk that cause large raw packet lists to extend past the bottom of the page

## [2.7.0] - 2026-03-08

* Feature: Multibyte path support
* Feature: Add multibyte statistics to statistics pane
* Feature: Add path bittage to contact info pane
* Feature: Put tools in a collapsible

## [2.6.1] - 2026-03-08

* Misc: Fix busted docker builds; we don't have a 2.6.0 build sorry

## [2.6.0] - 2026-03-08

* Feature: A11y improvements
* Feature: New themes
* Feature: Backfill channel sender identity when available
* Feature: Modular fanout bus, including Webhooks, more customizable community MQTT, and Apprise
* Bugfix: Unreads now respect blocklist
* Bugfix: Unreads can't accumulate on an open thread
* Bugfix: Channel name in broadcasts
* Bugfix: Add missing httpx dependency
* Bugfix: Improvements to radio startup frontend-blocking time and radio status reporting
* Misc: Improved button signage for app movement
* Misc: Test, performance, and documentation improvements

## [2.5.0] - 2026-03-05

* Feature: Far better accessibility across the app (with far to go)
* Feature: Add community MQTT stats reporting, and improve over a few commits
* Feature: Color schemes and misc. settings reorg
* Feature: Add why-active to filtered nodes
* Feature: Add channel and contact info box
* Feature: Add contact blocking
* Feature: Add potential repeater path map display
* Feature: Add flood scoping/regions
* Feature: Global message search
* Feature: Fully safe bot disable
* Feature: Add default #remoteterm channel (lol sorry I had to)
* Feature: Custom recency pruning in visualizer
* Bugfix: Be more cautious around null byte stripping
* Bugfix: Clear channel-add interface on not-add-another
* Bugfix: Add status/name/MQTT LWT
* Bugfix: Channel deletion propagates over WS
* Bugfix: Show map location for all nodes on link, not 7-day-limited
* Bugfix: Hide private key channel keys by default
* Misc: Logline to show if cleanup loop on non-sync'd meshcore radio links fixes anything
* Misc: Doc, changelog, and test improvements
* Misc: Add, and remove, package lock (sorry Windows users)
* Misc: Don't show mark all as read if not necessary
* Misc: Fix stale closures and misc. frontend perf/correctness improvements
* Misc: Add Windows startup notes
* Misc: E2E expansion + improvement
* Misc: Move around visualizer settings

## [2.4.0] - 2026-03-02

* Feature: Add community MQTT reporting (e.g. LetsMesh.net)
* Misc: Build scripts and library attribution
* Misc: Add sign of life to E2E tests

## [2.3.0] - 2026-03-01

* Feature: Click path description to reset to flood
* Feature: Add MQTT publishing
* Feature: Visualizer remembers settings
* Bugfix: Fix prefetch usage
* Bugfix: Fixed an issue where busy channels can result in double-display of incoming messages
* Misc: Drop py3.12 requirement
* Misc: Performance, documentation, test, and file structure optimizations
* Misc: Add arrows between route nodes on contact info
* Misc: Show repeater path/type in title bar

## [2.2.0] - 2026-02-28

* Feature: Track advert paths and use to disambiguate repeater identity in visualizer
* Feature: Contact info pane
* Feature: Overhaul repeater interface
* Bugfix: Misc. frontend rendering + perf improvements
* Bugfix: Better behavior around radio locking and autofetch/polling
* Bugfix: Clear channel name field on new-channel modal tab change
* Bugfix: Repeater inforbox can scroll
* Bugfix: Better handling of historical DM encrypts
* Bugfix: Handle errors if returned in prefetch phase
* Misc: Radio event response failure is logged/surfaced better
* Misc: Improve test coverage and remove dead code
* Misc: Documentation and errata improvements
* Misc: Database storage optimization

## [2.1.0] - 2026-02-23

* Feature: Add ability to remember last-used channel on load
* Feature: Add `docker compose` support (thanks @suymur !)
* Feature: Better-aligned favicon (lol)
* Bugfix: Disable autocomplete on message field
* Bugfix: Legacy hash restoration on page load
* Bugfix: Align resend buttons in pathing modal
* Bugfix: Update README.md (briefly), then docker-compose.yaml, to reflect correct docker image host
* Bugfix: Correct settings pane scroll lock on zoom (thanks @yellowcooln !)
* Bugfix: Improved repeater comms on busy meshes
* Bugfix: Drain before autofetch from radio
* Bugfix: Fix, or document exceptions to, sub-second resolution message failure
* Bugfix: Improved handling of radio connection, disconnection, and connection-aliveness-status
* Bugfix: Force server-side keystore update when radio key changes
* Bugfix: Reduce WS churn for incoming message handling
* Bugfix: Fix content type signalling for irrelevant endpoints
* Bugfix: Handle stuck post-connect failure state
* Misc: Documentation & version parsing improvements
* Misc: Hide char counter on mobile for short messages
* Misc: Typo fixes in docs and settings
* Misc: Add dynamic webmanifest for hosts that can support it
* Misc: Improve DB size via dropping unnecessary uniqs, indices, vacuum, and offering ability to drop historical matches packets
* Misc: Drop weird rounded bounding box for settings
* Misc: Move resend buttons to pathing modal
* Misc: Improved comments around database ownership on *nix systems
* Misc: Move to SSoT for message dedupe on frontend
* Misc: Move DM ack clearing to standard poll, and increase hold time between polling
* Misc: Holistic testing overhaul

## [2.0.1] - 2026-02-16

* Bugfix: Fix missing trigger condition on statistics pane expansion on mobile

## [2.0.0] - 2026-02-16

* Feature: Frontend UX + log overhaul
* Bugfix: Use contact object from DB for broadcast rather than handrolling
* Bugfix: Fix out of order path WS messages overwriting each other
* Bugfix: Make broadcast timestamp match fallback logic used in storage code
* Bugfix: Fix repeater command timestamp selection logic
* Bugfix: Use actual pubkey matching for path update, and don't action serial path update events (use RX packet)
* Bugfix: Add missing radio operation locks in a few spots
* Bugfix: Fix dedupe for frontend raw packet delivery (mesh visualizer much more active now!)
* Bugfix: Less aggressive dedupe for advert packets (we don't care about the payload, we care about the path, duh)
* Misc: Visualizer layout refinement & option labels

## [1.10.0] - 2026-02-16

* Feature: Collapsible sidebar sections with per-section unread badge (thanks @rgregg !)
* Feature: 3D mesh visualizer
* Feature: Statistics pane
* Feature: Support incoming/outgoing indication for bot invocations
* Feature: Quick byte-perfect message resend if you got unlucky with repeats (thanks @rgregg -- we had a parallel implementation but I appreciate your work!)
* Bugfix: Fix top padding out outgoing message
* Bugfix: Frontend performance, appearance, and Lighthouse improvements (prefetches, form labelling, contrast, channel/roomlist changes)
* Bugfix: Multiple-sent messages had path appearing delays until rerender
* Bugfix: Fix ack/message race condition that caused dropped ack displays until rerender
* Misc: Dedupe contacts/rooms by key and not name to prevent name collisions creating unreachable conversations
* Misc: s/stopped/idle/ for room finder

## [1.9.3] - 2026-02-12

* Feature: Upgrade the room finder to support two-word rooms

## [1.9.2] - 2026-02-12

* Feature: Options dialog sucks less
* Bugfix: Move tests to isolated memory DB
* Bugfix: Mention case sensitivity
* Bugfix: Stale header retention on settings page view
* Bugfix: Non-isolated path writing
* Bugfix: Nullable contact fields are now passed as real nulls
* Bugfix: Look at all fields on message reconcile, not just text
* Bugfix: Make mark-all-as-read atomic
* Misc: Purge unused WS handlers from back when we did chans and contacts over WS, not API
* Misc: Massive test and AGENTS.md overhauls and additions

## [1.9.1] - 2026-02-10

* Feature: Contacts and channels use keys, not names
* Bugfix: Fix falsy casting of 0 in lat lon and timing data
* Bugfix: Show message length in bytes, not chars
* Bugfix: Fix phantom unread badges on focused convos
* Misc: Bot invocation to async
* Misc: Use full key, not prefix, where we can

## [1.9.0] - 2026-02-10

* Feature: Favorited contacts are preferentially loaded onto the radio
* Feature: Add recent-message caching for fast switching
* Feature: Add echo paths modal when echo-heard checkbox is clicked
* Feature: Add experimental byte-perfect double-send for bad RF environments to try to punch the message out
Frontend: Better styling on echo + message path display
* Bugfix: Prevent frontend static file serving path traversal vuln
* Bugfix: Safer prefix-claiming for DMs we don't have the key for
* Bugfix: Prevent injection from mentions with special characters
* Bugfix: Fix repeaters comms showing in wrong channel when repeater operations are in flight and the channel is changed quickly
* Bugfix: App can boot and test without a frontend dir
* Misc: Improve and consistent-ify (?) backend radio operation lock management
* Misc: Frontend performance and safety enhancements
* Misc: Move builds to non-bundled; usage requires building the Frontend
* Misc: Update tests and agent docs

## [1.8.0] - 2026-02-07

* Feature: Single hop ping
* Feature: PWA viewport fixes(thanks @rgregg)
Feature (?): No frontend distribution; build it yourself ;P
* Bugfix: Fix channel message send race condition (concurrent sends could corrupt shared radio slot)
* Bugfix: Fix TOCTOU race in radio reconnect (duplicate connections under contention)
* Bugfix: Better guarding around reconnection
* Bugfix: Duplicate websocket connection fixes
* Bugfix: Settings tab error cleanliness on tab swap
* Bugfix: Fix path traversal vuln
UI: Swap visualizer legend ordering (yay prettier)
* Misc: Perf and locking improvements
* Misc: Always flood advertisements
* Misc: Better packet dupe handling
* Misc: Dead code cleanup, test improvements

## [1.7.1] - 2026-02-03

* Feature: Clickable hyperlinks
* Bugfix: More consistent public key normalization
* Bugfix: Use more reliable cursor paging
* Bugfix: Fix null timestamp dedupe failure
* Bugfix: More consistent prefix-based message claiming on key receipt
* Misc: Bot can respond to its own messages
* Misc: Additional tests
* Misc: Remove unneeded message dedupe logic
* Misc: Resync settings after radio settings mutation

## [1.7.0] - 2026-01-27

* Feature: Multi-bot functionality
* Bugfix: Adjust bot code editor display and add line numbers
* Bugfix: Fix clock filtering and contact lookup behavior bugs
* Bugfix: Fix repeater message duplication issue
* Bugfix: Correct outbound message timestamp assignment (affecting outgoing messages seen as incoming)
UI: Move advertise button to identity tab
* Misc: Clarify fallback functionality for missing private key export in logs

## [1.6.0] - 2026-01-26

* Feature: Visualizer: extract public key from AnonReq, add heuristic repeater disambiguation, add reset button, draggable nodes
* Feature: Customizable advertising interval
* Feature: In-app bot setup
* Bugfix: Force contact onto radio before DM send
* Misc: Remove unused code

## [1.5.0] - 2026-01-19

* Feature: Network visualizer

## [1.4.1] - 2026-01-19

* Feature: Add option to attempt historical DM decrypt on new-contact advertisement (disabled by default)
* Feature: Server-side preference management for favorites, read status, etc.
UI: More compact hop labelling
* Bugfix: Misc. race conditions and websocket handling
* Bugfix: Reduce fetching cadence by loading all contact data at start to prevent fetches on advertise-driven update

## [1.4.0] - 2026-01-18

UI: Improve button layout for room searcher
UI: Improve favicon coloring
UI: Improve status bar button layout on small screen
* Feature: Show multi-path hop display with distance estimates
* Feature: Search rooms and contacts by key, not just name
* Bugfix: Historical DM decryption now works as expected
* Bugfix: Don't double-set active conversation after addition; wait for backend room name normalization

## [1.3.1] - 2026-01-17

UI: Rework restart handling
* Feature: Add `dutycyle_start` command to logged-in repeater session to start five min duty cycle tracking
Bug: Improve error message rendering from server-side errors
UI: Remove octothorpe from channel listing

## [1.3.0] - 2026-01-17

* Feature: Rework database schema to drop unnecessary columns and dedupe payloads at the DB level
* Feature: Massive frontend settings overhaul. It ain't gorgeous but it's easier to navigate.
* Feature: Drop repeater login wait time; vestigial from debugging a different issue

## [1.2.1] - 2026-01-17

Update: Update meshcore-hashtag-cracker to include sender-identification correctness check

## [1.2.0] - 2026-01-16

* Feature: Add favorites

## [1.1.0] - 2026-01-14

* Bugfix: Use actual pathing data from advertisements, not just always flood (oops)
* Bugfix: Autosync radio clock periodically to prevent drift (would show up most commonly as issues with repeater comms)

## [1.0.3] - 2026-01-13

* Bugfix: Add missing test management packages
* Improvement: Drop unnecessary repeater timeouts, and retain timeout for login only -- repeater ops are faster AND more reliable!

## [1.0.2] - 2026-01-13

* Improvement: Add delays between router ops to prevent traffic collisions

## [1.0.1] - 2026-01-13

* Bugixes: Cleaner DB shutdown, radio reconnect contention, packet dedupe garbage removal

## [1.0.0] - 2026-01-13

* Initial full release!

