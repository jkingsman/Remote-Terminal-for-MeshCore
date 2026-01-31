/**
 * Direct REST API helpers for E2E test setup and teardown.
 * These bypass the UI to set up preconditions and verify backend state.
 */

const BASE_URL = 'http://localhost:8000/api';

async function fetchJson<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE_URL}${path}`, {
    headers: { 'Content-Type': 'application/json', ...init?.headers },
    ...init,
  });
  if (!res.ok) {
    const body = await res.text();
    throw new Error(`API ${init?.method || 'GET'} ${path} returned ${res.status}: ${body}`);
  }
  return res.json() as Promise<T>;
}

// --- Health ---

export interface HealthStatus {
  radio_connected: boolean;
  serial_port: string | null;
}

export function getHealth(): Promise<HealthStatus> {
  return fetchJson('/health');
}

// --- Radio Config ---

export interface RadioConfig {
  name: string;
  public_key: string;
  lat: number;
  lon: number;
  tx_power: number;
  freq: number;
  bw: number;
  sf: number;
  cr: number;
}

export function getRadioConfig(): Promise<RadioConfig> {
  return fetchJson('/radio/config');
}

export function updateRadioConfig(patch: Partial<RadioConfig>): Promise<RadioConfig> {
  return fetchJson('/radio/config', {
    method: 'PATCH',
    body: JSON.stringify(patch),
  });
}

export function rebootRadio(): Promise<{ status: string; message: string }> {
  return fetchJson('/radio/reboot', { method: 'POST' });
}

// --- Channels ---

export interface Channel {
  key: string;
  name: string;
  is_hashtag: boolean;
  on_radio: boolean;
}

export function getChannels(): Promise<Channel[]> {
  return fetchJson('/channels');
}

export function createChannel(name: string): Promise<Channel> {
  return fetchJson('/channels', {
    method: 'POST',
    body: JSON.stringify({ name }),
  });
}

export function deleteChannel(key: string): Promise<void> {
  return fetchJson(`/channels/${key}`, { method: 'DELETE' });
}

// --- Messages ---

export interface MessagePath {
  path: string;
  received_at: number;
}

export interface Message {
  id: number;
  type: 'PRIV' | 'CHAN';
  conversation_key: string;
  text: string;
  outgoing: boolean;
  acked: number;
  received_at: number;
  sender_timestamp: number | null;
  paths: MessagePath[] | null;
}

export function getMessages(params: {
  type?: string;
  conversation_key?: string;
  limit?: number;
}): Promise<Message[]> {
  const qs = new URLSearchParams();
  if (params.type) qs.set('type', params.type);
  if (params.conversation_key) qs.set('conversation_key', params.conversation_key);
  if (params.limit) qs.set('limit', String(params.limit));
  return fetchJson(`/messages?${qs}`);
}

export function sendChannelMessage(
  channelKey: string,
  text: string
): Promise<{ status: string; message_id: number }> {
  return fetchJson('/messages/channel', {
    method: 'POST',
    body: JSON.stringify({ channel_key: channelKey, text }),
  });
}

// --- Settings ---

export interface BotConfig {
  id: string;
  name: string;
  enabled: boolean;
  code: string;
}

export interface AppSettings {
  max_radio_contacts: number;
  favorites: { type: string; id: string }[];
  auto_decrypt_dm_on_advert: boolean;
  sidebar_sort_order: string;
  last_message_times: Record<string, number>;
  preferences_migrated: boolean;
  bots: BotConfig[];
  advert_interval: number;
}

export function getSettings(): Promise<AppSettings> {
  return fetchJson('/settings');
}

export function updateSettings(patch: Partial<AppSettings>): Promise<AppSettings> {
  return fetchJson('/settings', {
    method: 'PATCH',
    body: JSON.stringify(patch),
  });
}

// --- Helpers ---

/**
 * Ensure #flightless channel exists, creating it if needed.
 * Returns the channel object.
 */
export async function ensureFlightlessChannel(): Promise<Channel> {
  const channels = await getChannels();
  const existing = channels.find((c) => c.name === '#flightless');
  if (existing) return existing;
  return createChannel('#flightless');
}

/**
 * Wait for health to show radio_connected, polling with retries.
 */
export async function waitForRadioConnected(
  timeoutMs: number = 30_000,
  intervalMs: number = 2000
): Promise<void> {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    try {
      const health = await getHealth();
      if (health.radio_connected) return;
    } catch {
      // Backend might be restarting
    }
    await new Promise((r) => setTimeout(r, intervalMs));
  }
  throw new Error(`Radio did not reconnect within ${timeoutMs}ms`);
}
