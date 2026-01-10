/**
 * Type aliases for key types used throughout the application.
 * These are all hex strings but serve different purposes.
 */

/** 64-character hex string identifying a contact/node */
export type PublicKey = string;

/** 12-character hex prefix of a public key (used in message routing) */
export type PubkeyPrefix = string;

/** 32-character hex string identifying a channel */
export type ChannelKey = string;

export interface RadioSettings {
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
  serial_port: string | null;
}

export interface Contact {
  public_key: PublicKey;
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
}

export interface Channel {
  key: ChannelKey;
  name: string;
  is_hashtag: boolean;
  on_radio: boolean;
}

export interface Message {
  id: number;
  type: 'PRIV' | 'CHAN';
  /** For PRIV: sender's PublicKey (or prefix). For CHAN: ChannelKey */
  conversation_key: string;
  text: string;
  sender_timestamp: number | null;
  received_at: number;
  path_len: number | null;
  txt_type: number;
  signature: string | null;
  outgoing: boolean;
  acked: boolean;
}

export type ConversationType = 'contact' | 'channel' | 'raw';

export interface Conversation {
  type: ConversationType;
  /** PublicKey for contacts, ChannelKey for channels, 'raw' for raw feed */
  id: string;
  name: string;
}

export interface RawPacket {
  id: number;
  timestamp: number;
  data: string; // hex
  payload_type: string;
  snr: number | null;  // Signal-to-noise ratio in dB
  rssi: number | null; // Received signal strength in dBm
  decrypted: boolean;
  decrypted_info: {
    channel_name: string | null;
    sender: string | null;
  } | null;
}

export interface AppSettings {
  max_radio_contacts: number;
}

export interface AppSettingsUpdate {
  max_radio_contacts?: number;
}

/** Contact type constant for repeaters */
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

export interface TelemetryResponse {
  pubkey_prefix: string;
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
  neighbors: NeighborInfo[];
  acl: AclEntry[];
}

export interface CommandResponse {
  command: string;
  response: string;
  sender_timestamp: number | null;
}
