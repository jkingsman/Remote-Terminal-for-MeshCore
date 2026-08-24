interface RadioSettings {
  freq: number;
  bw: number;
  sf: number;
  cr: number;
}

export interface RadioConfig {
  public_key: string;
  name: string;
  lat: number;
  lon: number;
  tx_power: number;
  max_tx_power: number;
  radio: RadioSettings;
  path_hash_mode: number;
  path_hash_mode_supported: boolean;
  advert_location_source?: 'off' | 'current';
  multi_acks_enabled?: boolean;
  telemetry_mode_base?: number;
  telemetry_mode_loc?: number;
  telemetry_mode_env?: number;
}

export interface RadioConfigUpdate {
  name?: string;
  lat?: number;
  lon?: number;
  tx_power?: number;
  radio?: RadioSettings;
  path_hash_mode?: number;
  advert_location_source?: 'off' | 'current';
  multi_acks_enabled?: boolean;
  telemetry_mode_base?: number;
  telemetry_mode_loc?: number;
  telemetry_mode_env?: number;
}

export type RadioDiscoveryTarget = 'repeaters' | 'sensors' | 'all';

export interface RadioDiscoveryResult {
  public_key: string;
  name: string | null;
  node_type: 'repeater' | 'sensor';
  heard_count: number;
  local_snr: number | null;
  local_rssi: number | null;
  remote_snr: number | null;
}

export interface RadioDiscoveryResponse {
  target: RadioDiscoveryTarget;
  duration_seconds: number;
  results: RadioDiscoveryResult[];
}

export interface RadioRegionDiscoveryRepeater {
  public_key: string;
  name: string | null;
  answered: boolean;
  regions: string[];
}

export interface RadioRegionDiscoveryResponse {
  repeaters_queried: number;
  repeaters_answered: number;
  /** Deduplicated union of flood-allowed region names across all repeaters. */
  regions: string[];
  results: RadioRegionDiscoveryRepeater[];
}

export type RadioAdvertMode = 'flood' | 'zero_hop';

export interface FanoutStatusEntry {
  name: string;
  type: string;
  status: string;
  last_error?: string | null;
}

export interface AppInfo {
  version: string;
  commit_hash: string | null;
}

export interface RadioStatsSnapshot {
  timestamp: number | null;
  battery_mv: number | null;
  uptime_secs: number | null;
  queue_len: number | null;
  errors: number | null;
  noise_floor: number | null;
  last_rssi: number | null;
  last_snr: number | null;
  tx_air_secs: number | null;
  rx_air_secs: number | null;
  packets_recv: number | null;
  packets_sent: number | null;
  flood_tx: number | null;
  direct_tx: number | null;
  flood_rx: number | null;
  direct_rx: number | null;
}

export interface HealthStatus {
  status: string;
  radio_connected: boolean;
  radio_initializing: boolean;
  radio_state?: 'connected' | 'initializing' | 'connecting' | 'disconnected' | 'paused';
  connection_info: string | null;
  app_info?: AppInfo | null;
  radio_device_info?: {
    model: string | null;
    firmware_build: string | null;
    firmware_version: string | null;
    max_contacts: number | null;
    max_channels: number | null;
  } | null;
  radio_stats?: RadioStatsSnapshot | null;
  database_size_mb: number;
  oldest_undecrypted_timestamp: number | null;
  fanout_statuses: Record<string, FanoutStatusEntry>;
  bots_disabled: boolean;
  bots_disabled_source?: 'env' | 'until_restart' | null;
  basic_auth_enabled?: boolean;
}

export interface FanoutConfig {
  id: string;
  type: string;
  name: string;
  enabled: boolean;
  config: Record<string, unknown>;
  scope: Record<string, unknown>;
  sort_order: number;
  created_at: number;
}

export interface MaintenanceResult {
  packets_deleted: number;
  vacuumed: boolean;
}

export interface Contact {
  public_key: string;
  name: string | null;
  type: number;
  flags: number;
  direct_path: string | null;
  direct_path_len: number;
  direct_path_hash_mode: number;
  direct_path_updated_at?: number | null;
  route_override_path?: string | null;
  route_override_len?: number | null;
  route_override_hash_mode?: number | null;
  effective_route?: ContactRoute | null;
  effective_route_source?: 'override' | 'direct' | 'flood';
  direct_route?: ContactRoute | null;
  route_override?: ContactRoute | null;
  last_advert: number | null;
  lat: number | null;
  lon: number | null;
  last_seen: number | null;
  on_radio: boolean;
  favorite: boolean;
  mcmp_enabled?: boolean;
  mcmp_version?: number;
  last_contacted: number | null;
  last_read_at: number | null;
  first_seen: number | null;
}

export interface ContactRoute {
  path: string;
  path_len: number;
  path_hash_mode: number;
}

export interface ContactAdvertPath {
  path: string;
  path_len: number;
  next_hop: string | null;
  first_seen: number;
  last_seen: number;
  heard_count: number;
}

export interface ContactAdvertPathSummary {
  public_key: string;
  paths: ContactAdvertPath[];
}

export interface ContactNameHistory {
  name: string;
  first_seen: number;
  last_seen: number;
}

export interface ContactActiveRoom {
  channel_key: string;
  channel_name: string;
  message_count: number;
}

export interface NearestRepeater {
  public_key: string;
  name: string | null;
  path_len: number;
  last_seen: number;
  heard_count: number;
}

export interface ContactAnalyticsHourlyBucket {
  bucket_start: number;
  last_24h_count: number;
  last_week_average: number;
  all_time_average: number;
}

export interface ContactAnalyticsWeeklyBucket {
  bucket_start: number;
  message_count: number;
}

export interface ContactAnalytics {
  lookup_type: 'contact' | 'name';
  name: string;
  contact: Contact | null;
  name_first_seen_at: number | null;
  name_history: ContactNameHistory[];
  dm_message_count: number;
  channel_message_count: number;
  includes_direct_messages: boolean;
  most_active_rooms: ContactActiveRoom[];
  advert_paths: ContactAdvertPath[];
  advert_frequency: number | null;
  nearest_repeaters: NearestRepeater[];
  hourly_activity: ContactAnalyticsHourlyBucket[];
  weekly_activity: ContactAnalyticsWeeklyBucket[];
}

export interface Channel {
  key: string;
  name: string;
  is_hashtag: boolean;
  on_radio: boolean;
  flood_scope_override?: string | null;
  path_hash_mode_override?: number | null;
  last_read_at: number | null;
  favorite: boolean;
  muted: boolean;
  mcmp_enabled?: boolean;
  mcmp_version?: number;
}

export interface ChannelMessageCounts {
  last_1h: number;
  last_24h: number;
  last_48h: number;
  last_7d: number;
  all_time: number;
}

export interface ChannelTopSender {
  sender_name: string;
  sender_key: string | null;
  message_count: number;
}

export interface BulkCreateHashtagChannelsResult {
  created_channels: Channel[];
  existing_count: number;
  invalid_names: string[];
  decrypt_started: boolean;
  decrypt_total_packets: number;
  message: string;
}

export interface PathHashWidthStats {
  total_packets: number;
  single_byte: number;
  double_byte: number;
  triple_byte: number;
  single_byte_pct: number;
  double_byte_pct: number;
  triple_byte_pct: number;
}

export interface ChannelDetail {
  channel: Channel;
  message_counts: ChannelMessageCounts;
  first_message_at: number | null;
  unique_sender_count: number;
  top_senders_24h: ChannelTopSender[];
  path_hash_width_24h: PathHashWidthStats;
}

/** A single path that a message took to reach us */
export interface MessagePath {
  /** Hex-encoded routing path */
  path: string;
  /** Unix timestamp when this path was received */
  received_at: number;
  /** Hop count (number of intermediate nodes). Null for legacy data (infer as len(path)/2). */
  path_len?: number | null;
  /** Last-hop RSSI in dBm (null if not available, e.g. older data) */
  rssi?: number | null;
  /** Last-hop SNR in dB (null if not available, e.g. older data) */
  snr?: number | null;
}

export interface Message {
  id: number;
  type: 'PRIV' | 'CHAN';
  /** For PRIV: sender's PublicKey (or prefix). For CHAN: ChannelKey */
  conversation_key: string;
  text: string;
  sender_timestamp: number | null;
  received_at: number;
  /** List of routing paths this message arrived via. Null for outgoing messages. */
  paths: MessagePath[] | null;
  txt_type: number;
  signature: string | null;
  sender_key: string | null;
  outgoing: boolean;
  /** ACK count: 0 = not acked, 1+ = number of acks/flood echoes received */
  acked: number;
  sender_name: string | null;
  channel_name?: string | null;
  packet_id?: number | null;
  /** Region scope transport code (uint16) when this arrived via a transport-routed packet. */
  transport_code?: number | null;
  /** Resolved region name for the transport code, if it matched a known region. */
  region?: string | null;
}

export interface MessagesAroundResponse {
  messages: Message[];
  has_older: boolean;
  has_newer: boolean;
}

export interface ResendChannelMessageResponse {
  status: string;
  message_id: number;
  message?: Message;
}

type ConversationType =
  | 'contact'
  | 'channel'
  | 'raw'
  | 'map'
  | 'visualizer'
  | 'search'
  | 'trace'
  | 'bots'
  | 'statistics';

export interface Conversation {
  type: ConversationType;
  /** PublicKey for contacts, ChannelKey for channels, 'raw'/'map' for special views */
  id: string;
  name: string;
  /** For map view: public key prefix to focus on */
  mapFocusKey?: string;
  /** For bots view: bot id to open in the editor */
  botId?: string;
}

export interface RawPacket {
  id: number;
  /** Per-observation WS identity (unique per RF arrival, may be absent in older payloads) */
  observation_id?: number;
  timestamp: number;
  data: string; // hex
  payload_type: string;
  snr: number | null; // Signal-to-noise ratio in dB
  rssi: number | null; // Received signal strength in dBm
  decrypted: boolean;
  decrypted_info: {
    channel_name: string | null;
    sender: string | null;
    channel_key: string | null;
    contact_key: string | null;
    sender_timestamp: number | null;
    message: string | null;
  } | null;
  /** Region scope transport code (uint16) for TransportFlood/TransportDirect packets. */
  transport_code?: number | null;
  /** Resolved region name for the transport code, if it matched a known region. */
  region?: string | null;
}

export interface AppSettings {
  max_radio_contacts: number;
  auto_decrypt_dm_on_advert: boolean;
  last_message_times: Record<string, number>;
  advert_interval: number;
  last_advert_time: number;
  flood_scope: string;
  known_regions: string[];
  blocked_keys: string[];
  blocked_names: string[];
  discovery_blocked_types: number[];
  tracked_telemetry_repeaters: string[];
  tracked_telemetry_contacts: string[];
  auto_resend_channel: boolean;
  telemetry_interval_hours: number;
  telemetry_routed_hourly: boolean;
}

export interface AppSettingsUpdate {
  max_radio_contacts?: number;
  auto_decrypt_dm_on_advert?: boolean;
  advert_interval?: number;
  auto_resend_channel?: boolean;
  flood_scope?: string;
  known_regions?: string[];
  blocked_keys?: string[];
  blocked_names?: string[];
  discovery_blocked_types?: number[];
  telemetry_interval_hours?: number;
  telemetry_routed_hourly?: boolean;
}

export interface TelemetrySchedule {
  preferred_hours: number;
  effective_hours: number;
  options: number[];
  tracked_count: number;
  max_tracked: number;
  next_run_at: number | null;
  routed_hourly: boolean;
  next_routed_run_at: number | null;
}

export interface TrackedTelemetryResponse {
  tracked_telemetry_repeaters: string[];
  names: Record<string, string>;
  schedule: TelemetrySchedule;
}

/** Contact type constants */
export const CONTACT_TYPE_REPEATER = 2;
export const CONTACT_TYPE_ROOM = 3;

export interface NeighborInfo {
  pubkey_prefix: string;
  name: string | null;
  snr: number;
  last_heard_seconds: number;
}

export interface AclEntry {
  pubkey_prefix: string;
  name: string | null;
  permission: number;
  permission_name: string;
}

export interface CommandResponse {
  command: string;
  response: string;
  sender_timestamp: number | null;
}

// --- Granular repeater endpoint types ---

export interface RepeaterLoginResponse {
  status: string;
  authenticated: boolean;
  message: string | null;
}

/** Room poll subscription status. Never carries the stored credential value. */
export interface RoomPollStatus {
  room_key: string;
  has_stored_credential: boolean;
  is_guest_credential: boolean;
  poll_enabled: boolean;
  interval_seconds: number;
  last_poll_at: number | null;
  last_result: string | null;
  last_error: string | null;
  consecutive_errors: number;
}

export interface RoomPollConfigRequest {
  enabled?: boolean;
  interval_seconds?: number;
  credential_action?: 'keep' | 'set' | 'clear';
  credential?: string | null;
}

export interface RepeaterStatusResponse {
  battery_volts: number;
  tx_queue_len: number;
  noise_floor_dbm: number;
  last_rssi_dbm: number;
  last_snr_db: number;
  packets_received: number;
  packets_sent: number;
  airtime_seconds: number;
  rx_airtime_seconds: number;
  uptime_seconds: number;
  sent_flood: number;
  sent_direct: number;
  recv_flood: number;
  recv_direct: number;
  flood_dups: number;
  direct_dups: number;
  full_events: number;
  recv_errors: number | null;
  telemetry_history: TelemetryHistoryEntry[];
}

export interface RepeaterNeighborsResponse {
  neighbors: NeighborInfo[];
  // Total neighbor count reported by the repeater firmware, independent of how many
  // entries were actually returned. Exceeds neighbors.length when a multi-chunk fetch
  // is incomplete. Null on older firmware / failed fetches.
  reported_count?: number | null;
}

export interface RepeaterAclResponse {
  acl: AclEntry[];
}

export interface RepeaterNodeInfoResponse {
  name: string | null;
  lat: string | null;
  lon: string | null;
  clock_utc: string | null;
}

export interface RepeaterRadioSettingsResponse {
  firmware_version: string | null;
  radio: string | null;
  tx_power: string | null;
  airtime_factor: string | null;
  // Configured duty-cycle limit (e.g. "25.0%"), firmware-derived from airtime_factor.
  // Only present on firmware >= 1.15; null on older nodes.
  duty_cycle_limit: string | null;
  repeat_enabled: string | null;
  flood_max: string | null;
}

export interface RepeaterAdvertIntervalsResponse {
  advert_interval: string | null;
  flood_advert_interval: string | null;
}

export interface RepeaterOwnerInfoResponse {
  owner_info: string | null;
  firmware_version: string | null;
  name: string | null;
  guest_password: string | null;
}

export interface RepeaterRegionEntry {
  name: string;
  depth: number;
  flood_allowed: boolean;
  is_home: boolean;
}

export interface RepeaterRegionsResponse {
  regions: RepeaterRegionEntry[];
  raw: string | null;
  truncated: boolean;
  /** 'cli' = full admin hierarchy; 'anon' = guest flood-allowed names only. */
  source: 'cli' | 'anon' | null;
}

export interface LppSensor {
  channel: number;
  type_name: string;
  value: number | Record<string, number>;
}

export interface RepeaterLppTelemetryResponse {
  sensors: LppSensor[];
}

export interface ContactTelemetryResponse {
  sensors: LppSensor[];
  fetched_at: number;
  telemetry_history: TelemetryHistoryEntry[];
}

export interface TrackedTelemetryContactsResponse {
  tracked_telemetry_contacts: string[];
  names: Record<string, string>;
  schedule: TelemetrySchedule;
}

export type PaneName =
  | 'status'
  | 'nodeInfo'
  | 'neighbors'
  | 'acl'
  | 'radioSettings'
  | 'advertIntervals'
  | 'ownerInfo'
  | 'lppTelemetry'
  | 'regions';

export interface PaneState {
  loading: boolean;
  attempt: number;
  error: string | null;
  fetched_at?: number | null;
}

export interface TelemetryLppSensor {
  channel: number;
  type_name: string;
  value: number;
}

export interface TelemetryHistoryEntry {
  timestamp: number;
  data: Record<string, number> & { lpp_sensors?: TelemetryLppSensor[] };
}

export interface PushSubscriptionInfo {
  id: string;
  endpoint: string;
  p256dh: string;
  auth: string;
  label: string;
  created_at: number;
  last_success_at: number | null;
  failure_count: number;
}

export interface TraceResponse {
  remote_snr: number | null;
  local_snr: number | null;
  path_len: number;
}

export interface RadioTraceNode {
  role: 'repeater' | 'custom' | 'local';
  public_key: string | null;
  name: string | null;
  observed_hash: string | null;
  snr: number | null;
}

export interface RadioTraceHopRequest {
  public_key?: string | null;
  hop_hex?: string | null;
}

export interface RadioTraceResponse {
  path_len: number;
  timeout_seconds: number;
  nodes: RadioTraceNode[];
}

export interface PathDiscoveryRoute {
  path: string;
  path_len: number;
  path_hash_mode: number;
}

export interface PathDiscoveryResponse {
  contact: Contact;
  forward_path: PathDiscoveryRoute;
  return_path: PathDiscoveryRoute;
}

export interface UnreadCounts {
  counts: Record<string, number>;
  mentions: Record<string, boolean>;
  last_message_times: Record<string, number>;
  last_read_ats: Record<string, number | null>;
  /** stateKey -> id of the oldest unread message. Locates the unread divider. */
  first_unread_ids: Record<string, number | null>;
}

interface BusyChannel {
  channel_key: string;
  channel_name: string;
  message_count: number;
}

interface ContactActivityCounts {
  last_hour: number;
  last_24_hours: number;
  last_week: number;
}

export interface NoiseFloorSample {
  timestamp: number;
  noise_floor_dbm: number;
}

export interface NoiseFloorHistoryStats {
  sample_interval_seconds: number;
  coverage_seconds: number;
  latest_noise_floor_dbm: number | null;
  latest_timestamp: number | null;
  samples: NoiseFloorSample[];
}

interface PacketsPerHourBucket {
  timestamp: number;
  count: number;
}

/**
 * Regional flood-scope adoption over the last 24h. Two views with different
 * denominators that will not agree — traffic spans all channels including
 * undecryptable ones (so it carries a false-positive floor from corrupt RF
 * captures), while senders requires decryption and is therefore noise-free but
 * limited to channels we hold keys for.
 */
export interface RegionScopeStats {
  total_messages: number;
  scoped_messages: number;
  scoped_pct: number;
  /** Estimated false positives in scoped_messages. At or below this = not adoption. */
  false_positive_floor: number;
  total_senders: number;
  scoped_senders: number;
  scoped_senders_pct: number;
}

export interface StatisticsResponse {
  busiest_channels_24h: BusyChannel[];
  contact_count: number;
  repeater_count: number;
  channel_count: number;
  total_packets: number;
  decrypted_packets: number;
  undecrypted_packets: number;
  total_dms: number;
  total_channel_messages: number;
  total_outgoing: number;
  contacts_heard: ContactActivityCounts;
  repeaters_heard: ContactActivityCounts;
  known_channels_active: ContactActivityCounts;
  path_hash_width_24h: {
    total_packets: number;
    single_byte: number;
    double_byte: number;
    triple_byte: number;
    single_byte_pct: number;
    double_byte_pct: number;
    triple_byte_pct: number;
  };
  region_scope_24h: RegionScopeStats;
  multibyte_rollout: MultibyteRolloutStats;
  packets_per_hour_72h: PacketsPerHourBucket[];
  noise_floor_24h: NoiseFloorHistoryStats;
}

/** Contact-level multibyte path adoption (nodes, not traffic). */
export interface MultibyteRolloutStats {
  contacts_with_route: number;
  contacts_multibyte: number;
  single_byte: number;
  double_byte: number;
  triple_byte: number;
  repeaters_with_route: number;
  repeaters_multibyte: number;
}

// ---------------------------------------------------------------------------
// Bots workspace
// ---------------------------------------------------------------------------

export interface BotUiTrigger {
  kind: 'keyword' | 'cron';
  spec: string;
}

interface BotSettingsSchemaFieldBase {
  key: string;
  label: string;
  default?: unknown;
  help?: string;
  show_when?: { key: string; value: string };
}

export interface BotSettingsValueField extends BotSettingsSchemaFieldBase {
  type: 'text' | 'password' | 'int' | 'float' | 'number' | 'bool' | 'select' | 'url';
  min?: number;
  max?: number;
  options?: { value: string; label: string }[];
}

export interface BotSettingsGeneratedUrlField extends BotSettingsSchemaFieldBase {
  type: 'generated_url';
  template: string;
  warning?: string;
  copy_label?: string;
  testable?: boolean;
  test_label?: string;
}

export type BotSettingsSchemaField = BotSettingsValueField | BotSettingsGeneratedUrlField;

export interface Bot {
  id: string;
  name: string;
  category: string;
  description: string;
  code: string;
  enabled: boolean;
  admin_only: boolean;
  respond_to_dms: boolean;
  scope: { channels: 'all' | 'none' | { only?: string[]; except?: string[] } };
  cooldown_seconds: number;
  per_user_cooldown_seconds: number;
  queue_threshold_seconds: number;
  settings_schema: BotSettingsSchemaField[];
  settings: Record<string, unknown>;
  ui_triggers: BotUiTrigger[];
  builtin_key: string | null;
  builtin_version: string | null;
  modified: boolean;
  last_error: string | null;
  sort_order: number;
  created_at: number;
  updated_at: number;
  declared_keywords: string[];
  declared_crons: string[];
  declared_events: string[];
  declared_webhooks: string[];
  is_legacy: boolean;
  load_error: string | null;
  runs_24h: number;
}

export interface BotUpdatePayload {
  name?: string;
  category?: string;
  description?: string;
  code?: string;
  enabled?: boolean;
  admin_only?: boolean;
  respond_to_dms?: boolean;
  scope?: Bot['scope'];
  cooldown_seconds?: number;
  per_user_cooldown_seconds?: number;
  queue_threshold_seconds?: number;
  settings?: Record<string, unknown>;
  ui_triggers?: BotUiTrigger[];
}

export interface BotLibraryEntry {
  key: string;
  name: string;
  category: string;
  description: string;
  version: string;
  installed: boolean;
}

export interface BotRun {
  id: number;
  bot_id: string;
  bot_name: string;
  started_at: number;
  duration_ms: number | null;
  trigger: string;
  sender_name: string | null;
  sender_key: string | null;
  channel_key: string | null;
  channel_name: string | null;
  is_dm: boolean;
  result: string;
  replies: number;
  error: string | null;
  test_run: boolean;
}

export interface BotTestResponse {
  matched: boolean;
  trigger: string | null;
  duration_ms: number;
  replies: {
    is_dm: boolean;
    destination: string | null;
    channel_key: string | null;
    text: string;
    region: string | null;
  }[];
  error: string | null;
  logs: string[];
}

export interface BotSchedule {
  id: string;
  label: string;
  cron: string;
  channel_key: string;
  flood_scope: string | null;
  message: string;
  enabled: boolean;
  last_run_at: number | null;
  last_result: string | null;
  created_at: number;
  next_run_at: number | null;
  channel_name: string | null;
}

export interface BotFeed {
  id: string;
  name: string;
  feed_type: 'rss' | 'api';
  url: string;
  channel_key: string;
  interval_seconds: number;
  format: string;
  items_path: string | null;
  enabled: boolean;
  last_item_id: string | null;
  last_check_at: number | null;
  last_error: string | null;
  error_count: number;
  items_posted: number;
  max_posts_per_check: number;
  created_at: number;
  channel_name: string | null;
}

export interface BotAdminUser {
  public_key: string;
  name: string;
}

export interface BotEngineSettings {
  command_prefix: string;
  require_prefix: boolean;
  mention_mode: 'also' | 'only' | 'off';
  global_reply_seconds: number;
  per_user_seconds: number;
  tx_spacing_seconds: number;
  max_response_hops: number;
  default_language: string;
  auto_detect_language: boolean;
  banned_users: string[];
  profanity_mode: 'off' | 'censor' | 'drop';
  admin_users: BotAdminUser[];
}

export interface BotEngineStatus {
  settings: BotEngineSettings;
  disabled_until_restart: boolean;
  disabled_by_env: boolean;
  total_bots: number;
  enabled_bots: number;
  erroring_bots: number;
  runs_24h: number;
}

export interface BotLogEntry {
  timestamp: number;
  level: string;
  source: string;
  message: string;
}

export interface BotStatsRanked {
  label: string;
  count: number;
}

export interface BotStats {
  runs: number;
  replies: number;
  reply_rate: number;
  errors: number;
  unique_users: number;
  avg_duration_ms: number;
  top_bots: BotStatsRanked[];
  top_channels: BotStatsRanked[];
  top_users: BotStatsRanked[];
  error_bots: BotStatsRanked[];
  runs_by_hour: { timestamp: number; count: number }[];
}
