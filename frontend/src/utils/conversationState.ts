/**
 * localStorage utilities for tracking conversation read/message state.
 *
 * Stores two maps:
 * - lastMessageTime: when each conversation last received a message
 * - lastReadTime: when the user last viewed each conversation
 *
 * A conversation has unread messages if lastMessageTime > lastReadTime.
 */

import { getPubkeyPrefix } from './pubkey';

const LAST_MESSAGE_KEY = 'remoteterm-lastMessageTime';
const LAST_READ_KEY = 'remoteterm-lastReadTime';

export type ConversationTimes = Record<string, number>;

function loadTimes(key: string): ConversationTimes {
  try {
    const stored = localStorage.getItem(key);
    return stored ? JSON.parse(stored) : {};
  } catch {
    return {};
  }
}

function saveTimes(key: string, times: ConversationTimes): void {
  try {
    localStorage.setItem(key, JSON.stringify(times));
  } catch {
    // localStorage might be full or disabled
  }
}

export function getLastMessageTimes(): ConversationTimes {
  return loadTimes(LAST_MESSAGE_KEY);
}

export function getLastReadTimes(): ConversationTimes {
  return loadTimes(LAST_READ_KEY);
}

export function setLastMessageTime(stateKey: string, timestamp: number): ConversationTimes {
  const times = loadTimes(LAST_MESSAGE_KEY);
  // Only update if this is a newer message
  if (!times[stateKey] || timestamp > times[stateKey]) {
    times[stateKey] = timestamp;
    saveTimes(LAST_MESSAGE_KEY, times);
  }
  return times;
}

export function setLastReadTime(stateKey: string, timestamp: number): ConversationTimes {
  const times = loadTimes(LAST_READ_KEY);
  times[stateKey] = timestamp;
  saveTimes(LAST_READ_KEY, times);
  return times;
}

/**
 * Generate a state tracking key for unread counts and message times.
 *
 * This is NOT the same as Message.conversation_key (the database field).
 * This creates prefixed keys for localStorage/state tracking:
 * - Channels: "channel-{channelKey}"
 * - Contacts: "contact-{12-char-pubkey-prefix}"
 *
 * The 12-char prefix for contacts ensures consistent matching regardless
 * of whether we have a full 64-char pubkey or just a prefix.
 */
export function getStateKey(
  type: 'channel' | 'contact',
  id: string
): string {
  if (type === 'channel') {
    return `channel-${id}`;
  }
  // For contacts, use 12-char prefix for consistent matching
  return `contact-${getPubkeyPrefix(id)}`;
}
