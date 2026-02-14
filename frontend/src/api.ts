import type {
  AppSettings,
  AppSettingsUpdate,
  Channel,
  CommandResponse,
  Contact,
  Favorite,
  HealthStatus,
  MaintenanceResult,
  Message,
  MigratePreferencesRequest,
  MigratePreferencesResponse,
  RadioConfig,
  RadioConfigUpdate,
  TelemetryResponse,
  TraceResponse,
  UnreadCounts,
} from './types';

const API_BASE = '/api';

async function fetchJson<T>(url: string, options?: RequestInit): Promise<T> {
  const res = await fetch(`${API_BASE}${url}`, {
    ...options,
    headers: {
      'Content-Type': 'application/json',
      ...options?.headers,
    },
  });
  if (!res.ok) {
    const errorText = await res.text();
    // FastAPI returns errors as {"detail": "message"}, extract the message
    let errorMessage = errorText || res.statusText;
    try {
      const errorJson = JSON.parse(errorText);
      if (errorJson.detail) {
        errorMessage = errorJson.detail;
      }
    } catch {
      // Not JSON, use raw text
    }
    throw new Error(errorMessage);
  }
  return res.json();
}

/** Check if an error is an AbortError (request was cancelled) */
export function isAbortError(err: unknown): boolean {
  // DOMException is thrown by fetch when aborted, and it's not an Error subclass
  if (err instanceof DOMException && err.name === 'AbortError') {
    return true;
  }
  // Also check for Error with AbortError name (for compatibility)
  return err instanceof Error && err.name === 'AbortError';
}

interface DecryptResult {
  started: boolean;
  total_packets: number;
  message: string;
}

export const api = {
  // Health
  getHealth: () => fetchJson<HealthStatus>('/health'),

  // Radio config
  getRadioConfig: () => fetchJson<RadioConfig>('/radio/config'),
  updateRadioConfig: (config: RadioConfigUpdate) =>
    fetchJson<RadioConfig>('/radio/config', {
      method: 'PATCH',
      body: JSON.stringify(config),
    }),
  setPrivateKey: (privateKey: string) =>
    fetchJson<{ status: string }>('/radio/private-key', {
      method: 'PUT',
      body: JSON.stringify({ private_key: privateKey }),
    }),
  sendAdvertisement: () =>
    fetchJson<{ status: string }>('/radio/advertise', {
      method: 'POST',
    }),
  rebootRadio: () =>
    fetchJson<{ status: string; message: string }>('/radio/reboot', {
      method: 'POST',
    }),
  reconnectRadio: () =>
    fetchJson<{ status: string; message: string; connected: boolean }>('/radio/reconnect', {
      method: 'POST',
    }),

  // Contacts
  getContacts: (limit = 100, offset = 0) =>
    fetchJson<Contact[]>(`/contacts?limit=${limit}&offset=${offset}`),
  deleteContact: (publicKey: string) =>
    fetchJson<{ status: string }>(`/contacts/${publicKey}`, {
      method: 'DELETE',
    }),
  createContact: (publicKey: string, name?: string, tryHistorical?: boolean) =>
    fetchJson<Contact>('/contacts', {
      method: 'POST',
      body: JSON.stringify({ public_key: publicKey, name, try_historical: tryHistorical }),
    }),
  markContactRead: (publicKey: string) =>
    fetchJson<{ status: string; public_key: string }>(`/contacts/${publicKey}/mark-read`, {
      method: 'POST',
    }),
  requestTelemetry: (publicKey: string, password: string) =>
    fetchJson<TelemetryResponse>(`/contacts/${publicKey}/telemetry`, {
      method: 'POST',
      body: JSON.stringify({ password }),
    }),
  sendRepeaterCommand: (publicKey: string, command: string) =>
    fetchJson<CommandResponse>(`/contacts/${publicKey}/command`, {
      method: 'POST',
      body: JSON.stringify({ command }),
    }),
  requestTrace: (publicKey: string) =>
    fetchJson<TraceResponse>(`/contacts/${publicKey}/trace`, {
      method: 'POST',
    }),

  // Channels
  getChannels: () => fetchJson<Channel[]>('/channels'),
  createChannel: (name: string, key?: string) =>
    fetchJson<Channel>('/channels', {
      method: 'POST',
      body: JSON.stringify({ name, key }),
    }),
  deleteChannel: (key: string) =>
    fetchJson<{ status: string }>(`/channels/${key}`, { method: 'DELETE' }),
  markChannelRead: (key: string) =>
    fetchJson<{ status: string; key: string }>(`/channels/${key}/mark-read`, {
      method: 'POST',
    }),

  // Messages
  getMessages: (
    params?: {
      limit?: number;
      offset?: number;
      type?: 'PRIV' | 'CHAN';
      conversation_key?: string;
      before?: number;
      before_id?: number;
    },
    signal?: AbortSignal
  ) => {
    const searchParams = new URLSearchParams();
    if (params?.limit) searchParams.set('limit', params.limit.toString());
    if (params?.offset) searchParams.set('offset', params.offset.toString());
    if (params?.type) searchParams.set('type', params.type);
    if (params?.conversation_key) searchParams.set('conversation_key', params.conversation_key);
    if (params?.before !== undefined) searchParams.set('before', params.before.toString());
    if (params?.before_id !== undefined) searchParams.set('before_id', params.before_id.toString());
    const query = searchParams.toString();
    return fetchJson<Message[]>(`/messages${query ? `?${query}` : ''}`, { signal });
  },
  sendDirectMessage: (destination: string, text: string) =>
    fetchJson<Message>('/messages/direct', {
      method: 'POST',
      body: JSON.stringify({ destination, text }),
    }),
  sendChannelMessage: (channelKey: string, text: string) =>
    fetchJson<Message>('/messages/channel', {
      method: 'POST',
      body: JSON.stringify({ channel_key: channelKey, text }),
    }),
  resendMessage: (messageId: number) =>
    fetchJson<{ status: string; message_id: number }>(`/messages/${messageId}/resend`, {
      method: 'POST',
    }),

  // Packets
  getUndecryptedPacketCount: () => fetchJson<{ count: number }>('/packets/undecrypted/count'),
  decryptHistoricalPackets: (params: {
    key_type: 'channel' | 'contact';
    channel_key?: string;
    channel_name?: string;
  }) =>
    fetchJson<DecryptResult>('/packets/decrypt/historical', {
      method: 'POST',
      body: JSON.stringify(params),
    }),
  runMaintenance: (pruneUndecryptedDays: number) =>
    fetchJson<MaintenanceResult>('/packets/maintenance', {
      method: 'POST',
      body: JSON.stringify({ prune_undecrypted_days: pruneUndecryptedDays }),
    }),

  // Read State
  getUnreads: () => fetchJson<UnreadCounts>('/read-state/unreads'),
  markAllRead: () =>
    fetchJson<{ status: string; timestamp: number }>('/read-state/mark-all-read', {
      method: 'POST',
    }),

  // App Settings
  getSettings: () => fetchJson<AppSettings>('/settings'),
  updateSettings: (settings: AppSettingsUpdate) =>
    fetchJson<AppSettings>('/settings', {
      method: 'PATCH',
      body: JSON.stringify(settings),
    }),

  // Favorites
  toggleFavorite: (type: Favorite['type'], id: string) =>
    fetchJson<AppSettings>('/settings/favorites/toggle', {
      method: 'POST',
      body: JSON.stringify({ type, id }),
    }),

  // Preferences migration (one-time, from localStorage to database)
  migratePreferences: (request: MigratePreferencesRequest) =>
    fetchJson<MigratePreferencesResponse>('/settings/migrate', {
      method: 'POST',
      body: JSON.stringify(request),
    }),
};
