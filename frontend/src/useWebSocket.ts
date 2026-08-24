import { useEffect, useRef, useCallback } from 'react';
import type {
  BotLogEntry,
  Channel,
  HealthStatus,
  Contact,
  Message,
  MessagePath,
  RawPacket,
} from './types';
import { recordBotLog } from './stores/botLogStore';
import { parseWsEvent } from './wsEvents';

interface ErrorEvent {
  message: string;
  details?: string;
}

interface SuccessEvent {
  message: string;
  details?: string;
}

export interface UseWebSocketOptions {
  onHealth?: (health: HealthStatus) => void;
  onMessage?: (message: Message) => void;
  onContact?: (contact: Contact) => void;
  onContactResolved?: (previousPublicKey: string, contact: Contact) => void;
  onContactDeleted?: (publicKey: string) => void;
  onChannel?: (channel: Channel) => void;
  onChannelDeleted?: (key: string) => void;
  onRawPacket?: (packet: RawPacket) => void;
  onMessageAcked?: (
    messageId: number,
    ackCount: number,
    paths?: MessagePath[],
    packetId?: number | null
  ) => void;
  onError?: (error: ErrorEvent) => void;
  onSuccess?: (success: SuccessEvent) => void;
  onReconnect?: () => void;
}

export function useWebSocket(options: UseWebSocketOptions) {
  const wsRef = useRef<WebSocket | null>(null);
  const reconnectTimeoutRef = useRef<number | null>(null);
  const shouldReconnectRef = useRef(true);
  const hasConnectedRef = useRef(false);

  // Store options in ref to avoid stale closures in WebSocket handlers.
  // The onmessage callback captures this ref, and we keep the ref updated
  // with the latest handlers. This way, even though the WebSocket connection
  // is only created once, it always calls the current handlers.
  const optionsRef = useRef<UseWebSocketOptions>(options);

  // Keep the ref updated with latest options
  useEffect(() => {
    optionsRef.current = options;
  }, [options]);

  // Connect function - uses ref for handlers to avoid stale closures
  // No dependencies needed since we access handlers through ref
  const connect = useCallback(() => {
    // Determine WebSocket URL based on current location
    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    // Resolve relative to the page so sub-path reverse proxies work
    const base = new URL('./api/ws', window.location.href);
    const wsUrl = `${protocol}//${base.host}${base.pathname}`;

    const ws = new WebSocket(wsUrl);

    ws.onopen = () => {
      // Connection established (or re-established after disconnect)
      if (reconnectTimeoutRef.current) {
        clearTimeout(reconnectTimeoutRef.current);
        reconnectTimeoutRef.current = null;
      }
      if (hasConnectedRef.current) {
        optionsRef.current.onReconnect?.();
      }
      hasConnectedRef.current = true;
    };

    ws.onclose = () => {
      // Connection lost — will auto-reconnect after delay
      wsRef.current = null;

      if (!shouldReconnectRef.current) {
        return;
      }

      // Reconnect after 3 seconds
      if (reconnectTimeoutRef.current) {
        clearTimeout(reconnectTimeoutRef.current);
      }
      reconnectTimeoutRef.current = window.setTimeout(() => {
        // Reconnect attempt after disconnect
        connect();
      }, 3000);
    };

    ws.onerror = (error) => {
      console.error('WebSocket error:', error);
    };

    ws.onmessage = (event) => {
      try {
        const msg = parseWsEvent(event.data);
        // Access handlers through ref to always use current versions
        const handlers = optionsRef.current;

        switch (msg.type) {
          case 'health':
            handlers.onHealth?.(msg.data as HealthStatus);
            break;
          case 'message':
            handlers.onMessage?.(msg.data as Message);
            break;
          case 'contact':
            handlers.onContact?.(msg.data as Contact);
            break;
          case 'contact_resolved': {
            const resolved = msg.data as {
              previous_public_key: string;
              contact: Contact;
            };
            handlers.onContactResolved?.(resolved.previous_public_key, resolved.contact);
            break;
          }
          case 'channel':
            handlers.onChannel?.(msg.data as Channel);
            break;
          case 'contact_deleted':
            handlers.onContactDeleted?.((msg.data as { public_key: string }).public_key);
            break;
          case 'channel_deleted':
            handlers.onChannelDeleted?.((msg.data as { key: string }).key);
            break;
          case 'raw_packet':
            handlers.onRawPacket?.(msg.data as RawPacket);
            break;
          case 'bot_log':
            // Routed straight to the module-level store (like raw packets):
            // only the Bots view's Logs tab subscribes.
            recordBotLog(msg.data as BotLogEntry);
            break;
          case 'message_acked': {
            const ackData = msg.data as {
              message_id: number;
              ack_count: number;
              paths?: MessagePath[];
              packet_id?: number | null;
            };
            handlers.onMessageAcked?.(
              ackData.message_id,
              ackData.ack_count,
              ackData.paths,
              ackData.packet_id
            );
            break;
          }
          case 'error':
            handlers.onError?.(msg.data as ErrorEvent);
            break;
          case 'success':
            handlers.onSuccess?.(msg.data as SuccessEvent);
            break;
          case 'pong':
            // Heartbeat response, ignore
            break;
          case 'unknown':
            console.warn('Unknown WebSocket message type:', msg.rawType);
        }
      } catch (e) {
        console.error('Failed to parse WebSocket message:', e);
      }
    };

    wsRef.current = ws;
  }, []); // No dependencies - handlers accessed through ref

  useEffect(() => {
    shouldReconnectRef.current = true;
    connect();

    // Ping every 30 seconds to keep connection alive
    const pingInterval = setInterval(() => {
      if (wsRef.current?.readyState === WebSocket.OPEN) {
        wsRef.current.send('ping');
      }
    }, 30000);

    return () => {
      shouldReconnectRef.current = false;
      clearInterval(pingInterval);
      if (reconnectTimeoutRef.current) {
        clearTimeout(reconnectTimeoutRef.current);
        reconnectTimeoutRef.current = null;
      }
      if (wsRef.current) {
        wsRef.current.close();
      }
    };
  }, [connect]);
}
