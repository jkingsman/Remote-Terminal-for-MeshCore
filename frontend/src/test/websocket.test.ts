/**
 * Tests for WebSocket message parsing.
 *
 * These tests verify that WebSocket messages are correctly parsed
 * and routed to the appropriate handlers.
 */

import { describe, it, expect, vi } from 'vitest';
import type { HealthStatus, Contact, Channel, Message, RawPacket } from '../types';

/**
 * Parse and route a WebSocket message.
 * Extracted logic from useWebSocket.ts for testing.
 */
function parseWebSocketMessage(
  data: string,
  handlers: {
    onHealth?: (health: HealthStatus) => void;
    onContacts?: (contacts: Contact[]) => void;
    onChannels?: (channels: Channel[]) => void;
    onMessage?: (message: Message) => void;
    onContact?: (contact: Contact) => void;
    onRawPacket?: (packet: RawPacket) => void;
    onMessageAcked?: (messageId: number) => void;
  }
): { type: string; handled: boolean } {
  try {
    const msg = JSON.parse(data);

    switch (msg.type) {
      case 'health':
        handlers.onHealth?.(msg.data as HealthStatus);
        return { type: msg.type, handled: !!handlers.onHealth };
      case 'contacts':
        handlers.onContacts?.(msg.data as Contact[]);
        return { type: msg.type, handled: !!handlers.onContacts };
      case 'channels':
        handlers.onChannels?.(msg.data as Channel[]);
        return { type: msg.type, handled: !!handlers.onChannels };
      case 'message':
        handlers.onMessage?.(msg.data as Message);
        return { type: msg.type, handled: !!handlers.onMessage };
      case 'contact':
        handlers.onContact?.(msg.data as Contact);
        return { type: msg.type, handled: !!handlers.onContact };
      case 'raw_packet':
        handlers.onRawPacket?.(msg.data as RawPacket);
        return { type: msg.type, handled: !!handlers.onRawPacket };
      case 'message_acked':
        handlers.onMessageAcked?.((msg.data as { message_id: number }).message_id);
        return { type: msg.type, handled: !!handlers.onMessageAcked };
      case 'pong':
        return { type: msg.type, handled: true };
      default:
        return { type: msg.type, handled: false };
    }
  } catch {
    return { type: 'error', handled: false };
  }
}

describe('parseWebSocketMessage', () => {
  it('routes health message to onHealth handler', () => {
    const onHealth = vi.fn();
    const data = JSON.stringify({
      type: 'health',
      data: { radio_connected: true, serial_port: '/dev/ttyUSB0' },
    });

    const result = parseWebSocketMessage(data, { onHealth });

    expect(result.type).toBe('health');
    expect(result.handled).toBe(true);
    expect(onHealth).toHaveBeenCalledWith({
      radio_connected: true,
      serial_port: '/dev/ttyUSB0',
    });
  });

  it('routes message_acked to onMessageAcked with message ID', () => {
    const onMessageAcked = vi.fn();
    const data = JSON.stringify({
      type: 'message_acked',
      data: { message_id: 42 },
    });

    const result = parseWebSocketMessage(data, { onMessageAcked });

    expect(result.type).toBe('message_acked');
    expect(result.handled).toBe(true);
    expect(onMessageAcked).toHaveBeenCalledWith(42);
  });

  it('routes new message to onMessage handler', () => {
    const onMessage = vi.fn();
    const messageData = {
      id: 123,
      type: 'CHAN',
      channel_idx: 0,
      text: 'Hello',
      received_at: 1700000000,
      outgoing: false,
      acked: false,
    };
    const data = JSON.stringify({ type: 'message', data: messageData });

    const result = parseWebSocketMessage(data, { onMessage });

    expect(result.type).toBe('message');
    expect(result.handled).toBe(true);
    expect(onMessage).toHaveBeenCalledWith(messageData);
  });

  it('handles pong messages silently', () => {
    const data = JSON.stringify({ type: 'pong' });

    const result = parseWebSocketMessage(data, {});

    expect(result.type).toBe('pong');
    expect(result.handled).toBe(true);
  });

  it('returns unhandled for unknown message types', () => {
    const data = JSON.stringify({ type: 'unknown_type', data: {} });

    const result = parseWebSocketMessage(data, {});

    expect(result.type).toBe('unknown_type');
    expect(result.handled).toBe(false);
  });

  it('handles invalid JSON gracefully', () => {
    const data = 'not valid json {';

    const result = parseWebSocketMessage(data, {});

    expect(result.type).toBe('error');
    expect(result.handled).toBe(false);
  });

  it('does not call handler when not provided', () => {
    const data = JSON.stringify({
      type: 'health',
      data: { radio_connected: true },
    });

    const result = parseWebSocketMessage(data, {});

    expect(result.type).toBe('health');
    expect(result.handled).toBe(false);
  });

  it('routes raw_packet to onRawPacket handler', () => {
    const onRawPacket = vi.fn();
    const packetData = {
      id: 1,
      timestamp: 1700000000,
      data: 'deadbeef',
      payload_type: 'GROUP_TEXT',
      decrypted: true,
      decrypted_info: { channel_name: '#test', sender: 'Alice' },
    };
    const data = JSON.stringify({ type: 'raw_packet', data: packetData });

    const result = parseWebSocketMessage(data, { onRawPacket });

    expect(result.type).toBe('raw_packet');
    expect(result.handled).toBe(true);
    expect(onRawPacket).toHaveBeenCalledWith(packetData);
  });
});
