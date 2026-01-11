/**
 * Tests for useConversationMessages hook utilities.
 *
 * These tests verify the message deduplication key generation.
 */

import { describe, it, expect } from 'vitest';
import { getMessageContentKey } from '../hooks/useConversationMessages';
import type { Message } from '../types';

function createMessage(overrides: Partial<Message> = {}): Message {
  return {
    id: 1,
    type: 'CHAN',
    conversation_key: 'channel123',
    text: 'Hello world',
    sender_timestamp: 1700000000,
    received_at: 1700000001,
    path_len: null,
    txt_type: 0,
    signature: null,
    outgoing: false,
    acked: 0,
    ...overrides,
  };
}

describe('getMessageContentKey', () => {
  it('generates key from type, conversation_key, text, and sender_timestamp', () => {
    const msg = createMessage({
      type: 'CHAN',
      conversation_key: 'abc123',
      text: 'Hello',
      sender_timestamp: 1700000000,
    });

    const key = getMessageContentKey(msg);

    expect(key).toBe('CHAN-abc123-Hello-1700000000');
  });

  it('generates different keys for different message types', () => {
    const chanMsg = createMessage({ type: 'CHAN' });
    const privMsg = createMessage({ type: 'PRIV' });

    expect(getMessageContentKey(chanMsg)).not.toBe(getMessageContentKey(privMsg));
  });

  it('generates different keys for different conversation keys', () => {
    const msg1 = createMessage({ conversation_key: 'channel1' });
    const msg2 = createMessage({ conversation_key: 'channel2' });

    expect(getMessageContentKey(msg1)).not.toBe(getMessageContentKey(msg2));
  });

  it('generates different keys for different text', () => {
    const msg1 = createMessage({ text: 'Hello' });
    const msg2 = createMessage({ text: 'World' });

    expect(getMessageContentKey(msg1)).not.toBe(getMessageContentKey(msg2));
  });

  it('generates different keys for different timestamps', () => {
    const msg1 = createMessage({ sender_timestamp: 1700000000 });
    const msg2 = createMessage({ sender_timestamp: 1700000001 });

    expect(getMessageContentKey(msg1)).not.toBe(getMessageContentKey(msg2));
  });

  it('generates same key for messages with same content', () => {
    const msg1 = createMessage({
      id: 1,
      type: 'CHAN',
      conversation_key: 'abc',
      text: 'Test',
      sender_timestamp: 1700000000,
    });
    const msg2 = createMessage({
      id: 2, // Different ID
      type: 'CHAN',
      conversation_key: 'abc',
      text: 'Test',
      sender_timestamp: 1700000000,
    });

    expect(getMessageContentKey(msg1)).toBe(getMessageContentKey(msg2));
  });

  it('handles null sender_timestamp', () => {
    const msg = createMessage({ sender_timestamp: null });

    const key = getMessageContentKey(msg);

    expect(key).toBe('CHAN-channel123-Hello world-null');
  });

  it('handles empty text', () => {
    const msg = createMessage({ text: '' });

    const key = getMessageContentKey(msg);

    expect(key).toContain('--'); // Empty text between dashes
  });

  it('handles text with special characters', () => {
    const msg = createMessage({ text: 'Hello: World! @user #channel' });

    const key = getMessageContentKey(msg);

    expect(key).toContain('Hello: World! @user #channel');
  });
});
