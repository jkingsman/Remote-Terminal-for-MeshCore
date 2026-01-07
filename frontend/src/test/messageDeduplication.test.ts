/**
 * Tests for message deduplication in MessageList.
 *
 * Messages arriving via different packet paths should be deduplicated
 * based on (type, conversation_key, text, sender_timestamp).
 */

import { describe, it, expect } from 'vitest';
import type { Message } from '../types';

/**
 * Deduplication logic extracted from MessageList for testing.
 * Same message via different paths = same (type, conversation_key, text, timestamp)
 */
function deduplicateMessages(messages: Message[]): Message[] {
  return messages.reduce<Message[]>((acc, msg) => {
    const key = `${msg.type}-${msg.conversation_key}-${msg.text}-${msg.sender_timestamp}`;
    const existing = acc.find(m =>
      `${m.type}-${m.conversation_key}-${m.text}-${m.sender_timestamp}` === key
    );
    if (!existing) {
      acc.push(msg);
    }
    return acc;
  }, []);
}

function createMessage(overrides: Partial<Message>): Message {
  return {
    id: 1,
    type: 'CHAN',
    conversation_key: 'AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA0', // 32-char hex channel key
    text: 'Test message',
    sender_timestamp: 1700000000,
    received_at: 1700000001,
    path_len: null,
    txt_type: 0,
    signature: null,
    outgoing: false,
    acked: false,
    ...overrides,
  };
}

describe('Message Deduplication', () => {
  it('keeps unique messages', () => {
    const messages = [
      createMessage({ id: 1, text: 'Message 1', sender_timestamp: 1000 }),
      createMessage({ id: 2, text: 'Message 2', sender_timestamp: 2000 }),
      createMessage({ id: 3, text: 'Message 3', sender_timestamp: 3000 }),
    ];

    const result = deduplicateMessages(messages);

    expect(result).toHaveLength(3);
  });

  it('deduplicates same channel message via different paths', () => {
    const messages = [
      createMessage({ id: 1, conversation_key: 'AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA0', text: 'Hello', sender_timestamp: 1000 }),
      createMessage({ id: 2, conversation_key: 'AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA0', text: 'Hello', sender_timestamp: 1000 }), // duplicate
      createMessage({ id: 3, conversation_key: 'AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA0', text: 'Hello', sender_timestamp: 1000 }), // duplicate
    ];

    const result = deduplicateMessages(messages);

    expect(result).toHaveLength(1);
    expect(result[0].id).toBe(1); // keeps first occurrence
  });

  it('keeps messages with same text but different timestamps', () => {
    const messages = [
      createMessage({ id: 1, text: 'Hello', sender_timestamp: 1000 }),
      createMessage({ id: 2, text: 'Hello', sender_timestamp: 2000 }), // different timestamp
    ];

    const result = deduplicateMessages(messages);

    expect(result).toHaveLength(2);
  });

  it('keeps messages with same text but different channels', () => {
    const messages = [
      createMessage({ id: 1, conversation_key: 'AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA0', text: 'Hello', sender_timestamp: 1000 }),
      createMessage({ id: 2, conversation_key: 'BBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB', text: 'Hello', sender_timestamp: 1000 }), // different channel
    ];

    const result = deduplicateMessages(messages);

    expect(result).toHaveLength(2);
  });

  it('deduplicates same DM via different paths', () => {
    const messages = [
      createMessage({ id: 1, type: 'PRIV', conversation_key: 'abc123def456789012345678901234567890123456789012345678901234', text: 'Hi', sender_timestamp: 1000 }),
      createMessage({ id: 2, type: 'PRIV', conversation_key: 'abc123def456789012345678901234567890123456789012345678901234', text: 'Hi', sender_timestamp: 1000 }), // duplicate
    ];

    const result = deduplicateMessages(messages);

    expect(result).toHaveLength(1);
  });

  it('keeps DMs from different senders with same text', () => {
    const messages = [
      createMessage({ id: 1, type: 'PRIV', conversation_key: 'abc123def456789012345678901234567890123456789012345678901234', text: 'Hi', sender_timestamp: 1000 }),
      createMessage({ id: 2, type: 'PRIV', conversation_key: 'def456789012345678901234567890123456789012345678901234567890', text: 'Hi', sender_timestamp: 1000 }),
    ];

    const result = deduplicateMessages(messages);

    expect(result).toHaveLength(2);
  });

  it('keeps channel message and DM with same text', () => {
    const messages = [
      createMessage({ id: 1, type: 'CHAN', conversation_key: 'AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA0', text: 'Hello', sender_timestamp: 1000 }),
      createMessage({ id: 2, type: 'PRIV', conversation_key: 'abc123def456789012345678901234567890123456789012345678901234', text: 'Hello', sender_timestamp: 1000 }),
    ];

    const result = deduplicateMessages(messages);

    expect(result).toHaveLength(2);
  });

  it('handles empty array', () => {
    const result = deduplicateMessages([]);

    expect(result).toHaveLength(0);
  });

  it('handles single message', () => {
    const messages = [createMessage({ id: 1 })];

    const result = deduplicateMessages(messages);

    expect(result).toHaveLength(1);
  });
});
