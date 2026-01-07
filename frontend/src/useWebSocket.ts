import { useEffect, useRef, useCallback, useState } from 'react';
import type { HealthStatus, Contact, Channel, Message, RawPacket } from './types';

interface WebSocketMessage {
  type: string;
  data: unknown;
}

interface ErrorEvent {
  message: string;
  details?: string;
}

interface UseWebSocketOptions {
  onHealth?: (health: HealthStatus) => void;
  onContacts?: (contacts: Contact[]) => void;
  onChannels?: (channels: Channel[]) => void;
  onMessage?: (message: Message) => void;
  onContact?: (contact: Contact) => void;
  onRawPacket?: (packet: RawPacket) => void;
  onMessageAcked?: (messageId: number) => void;
  onError?: (error: ErrorEvent) => void;
}

export function useWebSocket(options: UseWebSocketOptions) {
  const wsRef = useRef<WebSocket | null>(null);
  const reconnectTimeoutRef = useRef<number | null>(null);
  const [connected, setConnected] = useState(false);

  const connect = useCallback(() => {
    // Determine WebSocket URL based on current location
    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    // In development, connect directly to backend; in production, use same host
    const isDev = window.location.port === '5173';
    const wsUrl = isDev
      ? `ws://localhost:8000/api/ws`
      : `${protocol}//${window.location.host}/api/ws`;

    const ws = new WebSocket(wsUrl);

    ws.onopen = () => {
      console.log('WebSocket connected');
      setConnected(true);
    };

    ws.onclose = () => {
      console.log('WebSocket disconnected');
      setConnected(false);
      wsRef.current = null;

      // Reconnect after 3 seconds
      reconnectTimeoutRef.current = window.setTimeout(() => {
        console.log('Attempting WebSocket reconnect...');
        connect();
      }, 3000);
    };

    ws.onerror = (error) => {
      console.error('WebSocket error:', error);
    };

    ws.onmessage = (event) => {
      try {
        const msg: WebSocketMessage = JSON.parse(event.data);

        switch (msg.type) {
          case 'health':
            options.onHealth?.(msg.data as HealthStatus);
            break;
          case 'contacts':
            options.onContacts?.(msg.data as Contact[]);
            break;
          case 'channels':
            options.onChannels?.(msg.data as Channel[]);
            break;
          case 'message':
            options.onMessage?.(msg.data as Message);
            break;
          case 'contact':
            options.onContact?.(msg.data as Contact);
            break;
          case 'raw_packet':
            options.onRawPacket?.(msg.data as RawPacket);
            break;
          case 'message_acked':
            options.onMessageAcked?.((msg.data as { message_id: number }).message_id);
            break;
          case 'error':
            options.onError?.(msg.data as ErrorEvent);
            break;
          case 'pong':
            // Heartbeat response, ignore
            break;
          default:
            console.log('Unknown WebSocket message type:', msg.type);
        }
      } catch (e) {
        console.error('Failed to parse WebSocket message:', e);
      }
    };

    wsRef.current = ws;
  }, [options]);

  useEffect(() => {
    connect();

    // Ping every 30 seconds to keep connection alive
    const pingInterval = setInterval(() => {
      if (wsRef.current?.readyState === WebSocket.OPEN) {
        wsRef.current.send('ping');
      }
    }, 30000);

    return () => {
      clearInterval(pingInterval);
      if (reconnectTimeoutRef.current) {
        clearTimeout(reconnectTimeoutRef.current);
      }
      if (wsRef.current) {
        wsRef.current.close();
      }
    };
  }, [connect]);

  return { connected };
}
