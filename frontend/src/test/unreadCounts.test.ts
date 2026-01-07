/**
 * Tests for unread count tracking logic.
 *
 * These tests verify the unread message counting behavior
 * without involving React component rendering.
 */

import { describe, it, expect } from 'vitest';
import type { Message, Conversation } from '../types';
import { getPubkeyPrefix, pubkeysMatch } from '../utils/pubkey';

/**
 * Determine if a message should increment unread count.
 * Extracted logic from App.tsx for testing.
 */
function shouldIncrementUnread(
  msg: Message,
  activeConversation: Conversation | null
): { key: string } | null {
  // Only count incoming messages
  if (msg.outgoing) {
    return null;
  }

  if (msg.type === 'CHAN' && msg.conversation_key) {
    const key = `channel-${msg.conversation_key}`;
    // Don't count if this channel is active
    if (activeConversation?.type === 'channel' && activeConversation?.id === msg.conversation_key) {
      return null;
    }
    return { key };
  }

  if (msg.type === 'PRIV' && msg.conversation_key) {
    // Use 12-char prefix for contact key
    const key = `contact-${getPubkeyPrefix(msg.conversation_key)}`;
    // Don't count if this contact is active (compare by prefix)
    if (activeConversation?.type === 'contact' && pubkeysMatch(activeConversation.id, msg.conversation_key)) {
      return null;
    }
    return { key };
  }

  return null;
}

/**
 * Get unread count for a conversation from the counts map.
 * Extracted logic from Sidebar.tsx for testing.
 */
function getUnreadCount(
  type: 'channel' | 'contact',
  id: string,
  unreadCounts: Record<string, number>
): number {
  if (type === 'channel') {
    return unreadCounts[`channel-${id}`] || 0;
  }
  // For contacts, use prefix
  const prefix = `contact-${getPubkeyPrefix(id)}`;
  return unreadCounts[prefix] || 0;
}

describe('shouldIncrementUnread', () => {
  const createMessage = (overrides: Partial<Message>): Message => ({
    id: 1,
    type: 'CHAN',
    conversation_key: 'AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA0', // 32-char hex channel key
    text: 'Test',
    sender_timestamp: null,
    received_at: Date.now(),
    path_len: null,
    txt_type: 0,
    signature: null,
    outgoing: false,
    acked: false,
    ...overrides,
  });

  it('returns key for incoming channel message when not viewing that channel', () => {
    const msg = createMessage({ type: 'CHAN', conversation_key: 'AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA3' });
    const activeConversation: Conversation = { type: 'channel', id: 'AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA5', name: 'other' };

    const result = shouldIncrementUnread(msg, activeConversation);

    expect(result).toEqual({ key: 'channel-AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA3' });
  });

  it('returns null for incoming channel message when viewing that channel', () => {
    const msg = createMessage({ type: 'CHAN', conversation_key: 'AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA3' });
    const activeConversation: Conversation = { type: 'channel', id: 'AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA3', name: '#test' };

    const result = shouldIncrementUnread(msg, activeConversation);

    expect(result).toBeNull();
  });

  it('returns null for outgoing messages', () => {
    const msg = createMessage({ type: 'CHAN', conversation_key: 'AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA3', outgoing: true });

    const result = shouldIncrementUnread(msg, null);

    expect(result).toBeNull();
  });

  it('returns key for incoming direct message when not viewing that contact', () => {
    const msg = createMessage({ type: 'PRIV', conversation_key: 'abc123456789012345678901234567890123456789012345678901234567' });
    const activeConversation: Conversation = { type: 'contact', id: 'xyz999999999012345678901234567890123456789012345678901234567', name: 'other' };

    const result = shouldIncrementUnread(msg, activeConversation);

    expect(result).toEqual({ key: 'contact-abc123456789' });
  });

  it('returns null for incoming direct message when viewing that contact', () => {
    const msg = createMessage({ type: 'PRIV', conversation_key: 'abc123456789012345678901234567890123456789012345678901234567' });
    const activeConversation: Conversation = {
      type: 'contact',
      id: 'abc123456789fullkey12345678901234567890123456789012345678',
      name: 'Alice',
    };

    const result = shouldIncrementUnread(msg, activeConversation);

    expect(result).toBeNull();
  });

  it('returns key when no conversation is active', () => {
    const msg = createMessage({ type: 'CHAN', conversation_key: 'AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA0' });

    const result = shouldIncrementUnread(msg, null);

    expect(result).toEqual({ key: 'channel-AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA0' });
  });

  it('returns key when viewing raw packet feed', () => {
    const msg = createMessage({ type: 'CHAN', conversation_key: 'AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA1' });
    const activeConversation: Conversation = { type: 'raw', id: 'raw', name: 'Packets' };

    const result = shouldIncrementUnread(msg, activeConversation);

    expect(result).toEqual({ key: 'channel-AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA1' });
  });
});

describe('getUnreadCount', () => {
  it('returns count for channel by exact key match', () => {
    const counts = { 'channel-AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA5': 3 };

    expect(getUnreadCount('channel', 'AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA5', counts)).toBe(3);
  });

  it('returns 0 for channel with no unread', () => {
    const counts = { 'channel-AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA5': 3 };

    expect(getUnreadCount('channel', 'BBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB9', counts)).toBe(0);
  });

  it('returns count for contact using 12-char prefix', () => {
    const counts = { 'contact-abc123456789': 5 };

    // Full public key lookup should match the prefix
    expect(getUnreadCount('contact', 'abc123456789fullpublickey123456789012345678901234', counts)).toBe(5);
  });

  it('handles contact key shorter than 12 chars', () => {
    const counts = { 'contact-short': 2 };

    expect(getUnreadCount('contact', 'short', counts)).toBe(2);
  });

  it('returns 0 for contact with no unread', () => {
    const counts = { 'contact-abc123456789': 5 };

    expect(getUnreadCount('contact', 'xyz999999999fullkey12345678901234567890123456789', counts)).toBe(0);
  });
});
