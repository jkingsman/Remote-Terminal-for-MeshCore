import type {
  AppSettings,
  AppSettingsUpdate,
  Channel,
  CommandResponse,
  Contact,
  HealthStatus,
  Message,
  RadioConfig,
  RadioConfigUpdate,
  TelemetryResponse,
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
    const error = await res.text();
    throw new Error(error || res.statusText);
  }
  return res.json();
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
  sendAdvertisement: (flood = true) =>
    fetchJson<{ status: string; flood: boolean }>(
      `/radio/advertise?flood=${flood}`,
      { method: 'POST' }
    ),
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
  getContact: (publicKey: string) => fetchJson<Contact>(`/contacts/${publicKey}`),
  syncContacts: () =>
    fetchJson<{ synced: number }>('/contacts/sync', { method: 'POST' }),
  addContactToRadio: (publicKey: string) =>
    fetchJson<{ status: string }>(`/contacts/${publicKey}/add-to-radio`, {
      method: 'POST',
    }),
  removeContactFromRadio: (publicKey: string) =>
    fetchJson<{ status: string }>(`/contacts/${publicKey}/remove-from-radio`, {
      method: 'POST',
    }),
  deleteContact: (publicKey: string) =>
    fetchJson<{ status: string }>(`/contacts/${publicKey}`, {
      method: 'DELETE',
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

  // Channels
  getChannels: () => fetchJson<Channel[]>('/channels'),
  getChannel: (key: string) => fetchJson<Channel>(`/channels/${key}`),
  createChannel: (name: string, key?: string) =>
    fetchJson<Channel>('/channels', {
      method: 'POST',
      body: JSON.stringify({ name, key }),
    }),
  syncChannels: () =>
    fetchJson<{ synced: number }>('/channels/sync', { method: 'POST' }),
  deleteChannel: (key: string) =>
    fetchJson<{ status: string }>(`/channels/${key}`, { method: 'DELETE' }),

  // Messages
  getMessages: (params?: {
    limit?: number;
    offset?: number;
    type?: 'PRIV' | 'CHAN';
    conversation_key?: string;
  }) => {
    const searchParams = new URLSearchParams();
    if (params?.limit) searchParams.set('limit', params.limit.toString());
    if (params?.offset) searchParams.set('offset', params.offset.toString());
    if (params?.type) searchParams.set('type', params.type);
    if (params?.conversation_key)
      searchParams.set('conversation_key', params.conversation_key);
    const query = searchParams.toString();
    return fetchJson<Message[]>(`/messages${query ? `?${query}` : ''}`);
  },
  getMessagesBulk: (
    conversations: Array<{ type: 'PRIV' | 'CHAN'; conversation_key: string }>,
    limitPerConversation: number = 100
  ) =>
    fetchJson<Record<string, Message[]>>(
      `/messages/bulk?limit_per_conversation=${limitPerConversation}`,
      {
        method: 'POST',
        body: JSON.stringify(conversations),
      }
    ),
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

  // Packets
  getUndecryptedPacketCount: () =>
    fetchJson<{ count: number }>('/packets/undecrypted/count'),
  decryptHistoricalPackets: (params: {
    key_type: 'channel' | 'contact';
    channel_key?: string;
    channel_name?: string;
  }) =>
    fetchJson<DecryptResult>('/packets/decrypt/historical', {
      method: 'POST',
      body: JSON.stringify(params),
    }),

  // App Settings
  getSettings: () => fetchJson<AppSettings>('/settings'),
  updateSettings: (settings: AppSettingsUpdate) =>
    fetchJson<AppSettings>('/settings', {
      method: 'PATCH',
      body: JSON.stringify(settings),
    }),
};
