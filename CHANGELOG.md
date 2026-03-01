## [2.5.0] - 2026-03-05

Feature: Far better accessibility across the app (with far to go)
Feature: Add community MQTT stats reporting, and improve over a few commits
Feature: Color schemes and misc. settings reorg
Feature: Add why-active to filtered nodes
Feature: Add channel and contact info box
Feature: Add contact blocking
Feature: Add potential repeater path map display
Feature: Add flood scoping/regions
Feature: Global message search
Feature: Fully safe bot disable
Feature: Add default #remoteterm channel (lol sorry I had to)
Feature: Custom recency pruning in visualizer
Bugfix: Be more cautious around null byte stripping
Bugfix: Clear channel-add interface on not-add-another
Bugfix: Add status/name/MQTT LWT
Bugfix: Channel deletion propagates over WS
Bugfix: Show map location for all nodes on link, not 7-day-limited
Bugfix: Hide private key channel keys by default
Misc: Logline to show if cleanup loop on non-sync'd meshcore radio links fixes anything
Misc: Doc, changelog, and test improvements
Misc: Add, and remove, package lock (sorry Windows users)
Misc: Don't show mark all as read if not necessary
Misc: Fix stale closures and misc. frontend perf/correctness improvements
Misc: Add Windows startup notes
Misc: E2E expansion + improvement
Misc: Move around visualizer settings
Feature: Add Apprise external notifications for incoming messages
Feature: Add notification scope control (all incoming vs selected channels/DMs)
Feature: Add multi-destination Apprise URL support (one URL per line)
Feature: Add webhook identity preservation option for Discord-compatible webhooks
Feature: Add `**via:** [...]` path metadata in DM/channel notifications
Bugfix: Ensure DM notifications include `direct` when path metadata is unavailable

## [2.4.0] - 2026-03-02

Feature: Add community MQTT reporting (e.g. LetsMesh.net)
Misc: Build scripts and library attribution
Misc: Add sign of life to E2E tests

## [2.3.0] - 2026-03-01

Feature: Click path description to reset to flood
Feature: Add MQTT publishing
Feature: Visualizer remembers settings
Bugfix: Fix prefetch usage
Bugfix: Fixed an issue where busy channels can result in double-display of incoming messages
Misc: Drop py3.12 requirement
Misc: Performance, documentation, test, and file structure optimizations
Misc: Add arrows between route nodes on contact info
Misc: Show repeater path/type in title bar

## [2.2.0] - 2026-02-28

Feature: Track advert paths and use to disambiguate repeater identity in visualizer
Feature: Contact info pane
Feature: Overhaul repeater interface
Bugfix: Misc. frontend rendering + perf improvements
Bugfix: Better behavior around radio locking and autofetch/polling
Bugfix: Clear channel name field on new-channel modal tab change
Bugfix: Repeater inforbox can scroll
Bugfix: Better handling of historical DM encrypts
Bugfix: Handle errors if returned in prefetch phase
Misc: Radio event response failure is logged/surfaced better
Misc: Improve test coverage and remove dead code
Misc: Documentation and errata improvements
Misc: Database storage optimization

## [2.1.0] - 2026-02-23

Feature: Add ability to remember last-used channel on load
Feature: Add `docker compose` support (thanks @suymur !)
Feature: Better-aligned favicon (lol)
Bugfix: Disable autocomplete on message field
Bugfix: Legacy hash restoration on page load
Bugfix: Align resend buttons in pathing modal
Bugfix: Update README.md (briefly), then docker-compose.yaml, to reflect correct docker image host
Bugfix: Correct settings pane scroll lock on zoom (thanks @yellowcooln !)
Bugfix: Improved repeater comms on busy meshes
Bugfix: Drain before autofetch from radio
Bugfix: Fix, or document exceptions to, sub-second resolution message failure
Bugfix: Improved handling of radio connection, disconnection, and connection-aliveness-status
Bugfix: Force server-side keystore update when radio key changes
Bugfix: Reduce WS churn for incoming message handling
Bugfix: Fix content type signalling for irrelevant endpoints
Bugfix: Handle stuck post-connect failure state
Misc: Documentation & version parsing improvements
Misc: Hide char counter on mobile for short messages
Misc: Typo fixes in docs and settings
Misc: Add dynamic webmanifest for hosts that can support it
Misc: Improve DB size via dropping unnecessary uniqs, indices, vacuum, and offering ability to drop historical matches packets
Misc: Drop weird rounded bounding box for settings
Misc: Move resend buttons to pathing modal
Misc: Improved comments around database ownership on *nix systems
Misc: Move to SSoT for message dedupe on frontend
Misc: Move DM ack clearing to standard poll, and increase hold time between polling
Misc: Holistic testing overhaul

## [2.0.1] - 2026-02-16

Bugfix: Fix missing trigger condition on statistics pane expansion on mobile

## [2.0.0] - 2026-02-16

Feature: Frontend UX + log overhaul
Bugfix: Use contact object from DB for broadcast rather than handrolling
Bugfix: Fix out of order path WS messages overwriting each other
Bugfix: Make broadcast timestamp match fallback logic used in storage code
Bugfix: Fix repeater command timestamp selection logic
Bugfix: Use actual pubkey matching for path update, and don't action serial path update events (use RX packet)
Bugfix: Add missing radio operation locks in a few spots
Bugfix: Fix dedupe for frontend raw packet delivery (mesh visualizer much more active now!)
Bugfix: Less aggressive dedupe for advert packets (we don't care about the payload, we care about the path, duh)
Misc: Visualizer layout refinement & option labels

## [1.10.0] - 2026-02-16

Feature: Collapsible sidebar sections with per-section unread badge (thanks @rgregg !)
Feature: 3D mesh visualizer
Feature: Statistics pane
Feature: Support incoming/outgoing indication for bot invocations
Feature: Quick byte-perfect message resend if you got unlucky with repeats (thanks @rgregg -- we had a parallel implementation but I appreciate your work!)
Bugfix: Fix top padding out outgoing message
Bugfix: Frontend performance, appearance, and Lighthouse improvements (prefetches, form labelling, contrast, channel/roomlist changes)
Bugfix: Multiple-sent messages had path appearing delays until rerender
Bugfix: Fix ack/message race condition that caused dropped ack displays until rerender
Misc: Dedupe contacts/rooms by key and not name to prevent name collisions creating unreachable conversations
Misc: s/stopped/idle/ for room finder

## [1.9.3] - 2026-02-12

Feature: Upgrade the room finder to support two-word rooms

## [1.9.2] - 2026-02-12

Feature: Options dialog sucks less
Bugfix: Move tests to isolated memory DB
Bugfix: Mention case sensitivity
Bugfix: Stale header retention on settings page view
Bugfix: Non-isolated path writing
Bugfix: Nullable contact fields are now passed as real nulls
Bugfix: Look at all fields on message reconcile, not just text
Bugfix: Make mark-all-as-read atomic
Misc: Purge unused WS handlers from back when we did chans and contacts over WS, not API
Misc: Massive test and AGENTS.md overhauls and additions

## [1.9.1] - 2026-02-10

Feature: Contacts and channels use keys, not names
Bugfix: Fix falsy casting of 0 in lat lon and timing data
Bugfix: Show message length in bytes, not chars
Bugfix: Fix phantom unread badges on focused convos
Misc: Bot invocation to async
Misc: Use full key, not prefix, where we can

## [1.9.0] - 2026-02-10

Feature: Favorited contacts are preferentially loaded onto the radio
Feature: Add recent-message caching for fast switching
Feature: Add echo paths modal when echo-heard checkbox is clicked
Feature: Add experimental byte-perfect double-send for bad RF environments to try to punch the message out
Frontend: Better styling on echo + message path display
Bugfix: Prevent frontend static file serving path traversal vuln
Bugfix: Safer prefix-claiming for DMs we don't have the key for
Bugfix: Prevent injection from mentions with special characters
Bugfix: Fix repeaters comms showing in wrong channel when repeater operations are in flight and the channel is changed quickly
Bugfix: App can boot and test without a frontend dir
Misc: Improve and consistent-ify (?) backend radio operation lock management
Misc: Frontend performance and safety enhancements
Misc: Move builds to non-bundled; usage requires building the Frontend
Misc: Update tests and agent docs

## [1.8.0] - 2026-02-07

Feature: Single hop ping
Feature: PWA viewport fixes(thanks @rgregg)
Feature (?): No frontend distribution; build it yourself ;P
Bugfix: Fix channel message send race condition (concurrent sends could corrupt shared radio slot)
Bugfix: Fix TOCTOU race in radio reconnect (duplicate connections under contention)
Bugfix: Better guarding around reconnection
Bugfix: Duplicate websocket connection fixes
Bugfix: Settings tab error cleanliness on tab swap
Bugfix: Fix path traversal vuln
UI: Swap visualizer legend ordering (yay prettier)
Misc: Perf and locking improvements
Misc: Always flood advertisements
Misc: Better packet dupe handling
Misc: Dead code cleanup, test improvements

## [1.7.1] - 2026-02-03

Feature: Clickable hyperlinks
Bugfix: More consistent public key normalization
Bugfix: Use more reliable cursor paging
Bugfix: Fix null timestamp dedupe failure
Bugfix: More consistent prefix-based message claiming on key receipt
Misc: Bot can respond to its own messages
Misc: Additional tests
Misc: Remove unneeded message dedupe logic
Misc: Resync settings after radio settings mutation

## [1.7.0] - 2026-01-27

Feature: Multi-bot functionality
Bugfix: Adjust bot code editor display and add line numbers
Bugfix: Fix clock filtering and contact lookup behavior bugs
Bugfix: Fix repeater message duplication issue
Bugfix: Correct outbound message timestamp assignment (affecting outgoing messages seen as incoming)
UI: Move advertise button to identity tab
Misc: Clarify fallback functionality for missing private key export in logs

## [1.6.0] - 2026-01-26

Feature: Visualizer: extract public key from AnonReq, add heuristic repeater disambiguation, add reset button, draggable nodes
Feature: Customizable advertising interval
Feature: In-app bot setup
Bugfix: Force contact onto radio before DM send
Misc: Remove unused code

## [1.5.0] - 2026-01-19

Feature: Network visualizer

## [1.4.1] - 2026-01-19

Feature: Add option to attempt historical DM decrypt on new-contact advertisement (disabled by default)
Feature: Server-side preference management for favorites, read status, etc.
UI: More compact hop labelling
Bugfix: Misc. race conditions and websocket handling
Bugfix: Reduce fetching cadence by loading all contact data at start to prevent fetches on advertise-driven update

## [1.4.0] - 2026-01-18

UI: Improve button layout for room searcher
UI: Improve favicon coloring
UI: Improve status bar button layout on small screen
Feature: Show multi-path hop display with distance estimates
Feature: Search rooms and contacts by key, not just name
Bugfix: Historical DM decryption now works as expected
Bugfix: Don't double-set active conversation after addition; wait for backend room name normalization

## [1.3.1] - 2026-01-17

UI: Rework restart handling
Feature: Add `dutycyle_start` command to logged-in repeater session to start five min duty cycle tracking
Bug: Improve error message rendering from server-side errors
UI: Remove octothorpe from channel listing

## [1.3.0] - 2026-01-17

Feature: Rework database schema to drop unnecessary columns and dedupe payloads at the DB level
Feature: Massive frontend settings overhaul. It ain't gorgeous but it's easier to navigate.
Feature: Drop repeater login wait time; vestigial from debugging a different issue

## [1.2.1] - 2026-01-17

Update: Update meshcore-hashtag-cracker to include sender-identification correctness check

## [1.2.0] - 2026-01-16

Feature: Add favorites

## [1.1.0] - 2026-01-14

Bugfix: Use actual pathing data from advertisements, not just always flood (oops)
Bugfix: Autosync radio clock periodically to prevent drift (would show up most commonly as issues with repeater comms)

## [1.0.3] - 2026-01-13

Bugfix: Add missing test management packages
Improvement: Drop unnecessary repeater timeouts, and retain timeout for login only -- repeater ops are faster AND more reliable!

## [1.0.2] - 2026-01-13

Improvement: Add delays between router ops to prevent traffic collisions

## [1.0.1] - 2026-01-13

Bugixes: Cleaner DB shutdown, radio reconnect contention, packet dedupe garbage removal

## [1.0.0] - 2026-01-13

Initial full release!
