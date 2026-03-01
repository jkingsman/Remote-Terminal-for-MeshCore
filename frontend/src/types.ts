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
}

export interface RadioConfigUpdate {
  name?: string;
  lat?: number;
  lon?: number;
  tx_power?: number;
  radio?: RadioSettings;
}

export interface HealthStatus {
  status: string;
  radio_connected: boolean;
  connection_info: string | null;
  database_size_mb: number;
  oldest_undecrypted_timestamp: number | null;
  mqtt_status: string | null;
  community_mqtt_status: string | null;
  bots_disabled: boolean;
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
  last_path: string | null;
  last_path_len: number;
  last_advert: number | null;
  lat: number | null;
  lon: number | null;
  last_seen: number | null;
  on_radio: boolean;
  last_contacted: number | null;
  last_read_at: number | null;
  first_seen: number | null;
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

export interface ContactDetail {
  contact: Contact;
  name_history: ContactNameHistory[];
  dm_message_count: number;
  channel_message_count: number;
  most_active_rooms: ContactActiveRoom[];
  advert_paths: ContactAdvertPath[];
  advert_frequency: number | null;
  nearest_repeaters: NearestRepeater[];
}

export interface Channel {
  key: string;
  name: string;
  is_hashtag: boolean;
  on_radio: boolean;
  last_read_at: number | null;
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

export interface ChannelDetail {
  channel: Channel;
  message_counts: ChannelMessageCounts;
  first_message_at: number | null;
  unique_sender_count: number;
  top_senders_24h: ChannelTopSender[];
}

/** A single path that a message took to reach us */
export interface MessagePath {
  /** Hex-encoded routing path (2 chars per hop) */
  path: string;
  /** Unix timestamp when this path was received */
  received_at: number;
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
}

export interface MessagesAroundResponse {
  messages: Message[];
  has_older: boolean;
  has_newer: boolean;
}

type ConversationType = 'contact' | 'channel' | 'raw' | 'map' | 'visualizer' | 'search';

export interface Conversation {
  type: ConversationType;
  /** PublicKey for contacts, ChannelKey for channels, 'raw'/'map' for special views */
  id: string;
  name: string;
  /** For map view: public key prefix to focus on */
  mapFocusKey?: string;
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
  } | null;
}

export interface Favorite {
  type: 'channel' | 'contact';
  id: string; // channel key or contact public key
}

export interface BotConfig {
  id: string; // UUID for stable identity across renames/reorders
  name: string; // User-editable name
  enabled: boolean; // Whether this bot is enabled
  code: string; // Python code for this bot
}

export interface AppSettings {
  max_radio_contacts: number;
  favorites: Favorite[];
  auto_decrypt_dm_on_advert: boolean;
  sidebar_sort_order: 'recent' | 'alpha';
  last_message_times: Record<string, number>;
  preferences_migrated: boolean;
  advert_interval: number;
  last_advert_time: number;
  bots: BotConfig[];
  mqtt_broker_host: string;
  mqtt_broker_port: number;
  mqtt_username: string;
  mqtt_password: string;
  mqtt_use_tls: boolean;
  mqtt_tls_insecure: boolean;
  mqtt_topic_prefix: string;
  mqtt_publish_messages: boolean;
  mqtt_publish_raw_packets: boolean;
  apprise_enabled: boolean;
  apprise_url: string;
  apprise_mode: 'all' | 'selected';
  apprise_preserve_identity: boolean;
  apprise_targets: Favorite[];
  community_mqtt_enabled: boolean;
  community_mqtt_iata: string;
  community_mqtt_broker_host: string;
  community_mqtt_broker_port: number;
  community_mqtt_email: string;
  flood_scope: string;
  blocked_keys: string[];
  blocked_names: string[];
}

export interface AppSettingsUpdate {
  max_radio_contacts?: number;
  auto_decrypt_dm_on_advert?: boolean;
  sidebar_sort_order?: 'recent' | 'alpha';
  advert_interval?: number;
  bots?: BotConfig[];
  mqtt_broker_host?: string;
  mqtt_broker_port?: number;
  mqtt_username?: string;
  mqtt_password?: string;
  mqtt_use_tls?: boolean;
  mqtt_tls_insecure?: boolean;
  mqtt_topic_prefix?: string;
  mqtt_publish_messages?: boolean;
  mqtt_publish_raw_packets?: boolean;
  apprise_enabled?: boolean;
  apprise_url?: string;
  apprise_mode?: 'all' | 'selected';
  apprise_preserve_identity?: boolean;
  apprise_targets?: Favorite[];
  community_mqtt_enabled?: boolean;
  community_mqtt_iata?: string;
  community_mqtt_broker_host?: string;
  community_mqtt_broker_port?: number;
  community_mqtt_email?: string;
  flood_scope?: string;
  blocked_keys?: string[];
  blocked_names?: string[];
}

export interface MigratePreferencesRequest {
  favorites: Favorite[];
  sort_order: string;
  last_message_times: Record<string, number>;
}

export interface MigratePreferencesResponse {
  migrated: boolean;
  settings: AppSettings;
}

/** Contact type constants */
export const CONTACT_TYPE_REPEATER = 2;

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
}

export interface RepeaterNeighborsResponse {
  neighbors: NeighborInfo[];
}

export interface RepeaterAclResponse {
  acl: AclEntry[];
}

export interface RepeaterRadioSettingsResponse {
  firmware_version: string | null;
  radio: string | null;
  tx_power: string | null;
  airtime_factor: string | null;
  repeat_enabled: string | null;
  flood_max: string | null;
  name: string | null;
  lat: string | null;
  lon: string | null;
  clock_utc: string | null;
}

export interface RepeaterAdvertIntervalsResponse {
  advert_interval: string | null;
  flood_advert_interval: string | null;
}

export interface RepeaterOwnerInfoResponse {
  owner_info: string | null;
  guest_password: string | null;
}

export interface LppSensor {
  channel: number;
  type_name: string;
  value: number | Record<string, number>;
}

export interface RepeaterLppTelemetryResponse {
  sensors: LppSensor[];
}

export type PaneName =
  | 'status'
  | 'neighbors'
  | 'acl'
  | 'radioSettings'
  | 'advertIntervals'
  | 'ownerInfo'
  | 'lppTelemetry';

export interface PaneState {
  loading: boolean;
  attempt: number;
  error: string | null;
}

export interface TraceResponse {
  remote_snr: number | null;
  local_snr: number | null;
  path_len: number;
}

export interface UnreadCounts {
  counts: Record<string, number>;
  mentions: Record<string, boolean>;
  last_message_times: Record<string, number>;
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
}
