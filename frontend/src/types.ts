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
