import type {
  BotLogEntry,
  Channel,
  Contact,
  HealthStatus,
  Message,
  MessagePath,
  RawPacket,
} from './types';

export interface MessageAckedPayload {
  message_id: number;
  ack_count: number;
  paths?: MessagePath[];
  packet_id?: number | null;
}

export interface ContactDeletedPayload {
  public_key: string;
}

export interface ContactResolvedPayload {
  previous_public_key: string;
  contact: Contact;
}

export interface ChannelDeletedPayload {
  key: string;
}

export interface ToastPayload {
  message: string;
  details?: string;
}

export type KnownWsEvent =
  | { type: 'health'; data: HealthStatus }
  | { type: 'message'; data: Message }
  | { type: 'contact'; data: Contact }
  | { type: 'contact_resolved'; data: ContactResolvedPayload }
  | { type: 'channel'; data: Channel }
  | { type: 'contact_deleted'; data: ContactDeletedPayload }
  | { type: 'channel_deleted'; data: ChannelDeletedPayload }
  | { type: 'raw_packet'; data: RawPacket }
  | { type: 'message_acked'; data: MessageAckedPayload }
  | { type: 'bot_log'; data: BotLogEntry }
  | { type: 'error'; data: ToastPayload }
  | { type: 'success'; data: ToastPayload }
  | { type: 'pong'; data?: null };

export interface UnknownWsEvent {
  type: 'unknown';
  rawType: string;
  data: unknown;
}

export type ParsedWsEvent = KnownWsEvent | UnknownWsEvent;

interface RawWsEnvelope {
  type?: unknown;
  data?: unknown;
}

export function parseWsEvent(raw: string): ParsedWsEvent {
  const parsed: RawWsEnvelope = JSON.parse(raw);
  if (!parsed || typeof parsed !== 'object' || typeof parsed.type !== 'string') {
    throw new Error('Invalid WebSocket event envelope');
  }

  switch (parsed.type) {
    case 'health':
    case 'message':
    case 'contact':
    case 'contact_resolved':
    case 'channel':
    case 'contact_deleted':
    case 'channel_deleted':
    case 'raw_packet':
    case 'message_acked':
    case 'bot_log':
    case 'error':
    case 'success':
      return {
        type: parsed.type,
        data: parsed.data,
      } as KnownWsEvent;
    case 'pong':
      return { type: 'pong', data: parsed.data as null | undefined };
    default:
      return {
        type: 'unknown',
        rawType: parsed.type,
        data: parsed.data,
      };
  }
}
