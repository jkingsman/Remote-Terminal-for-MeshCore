import { useState, useCallback, useEffect, useRef } from 'react';
import { api } from '../api';
import {
  getLastMessageTimes,
  getLastReadTimes,
  setLastMessageTime,
  setLastReadTime,
  getStateKey,
  type ConversationTimes,
} from '../utils/conversationState';
import type { Channel, Contact, Conversation, Message } from '../types';

export interface UseUnreadCountsResult {
  unreadCounts: Record<string, number>;
  lastMessageTimes: ConversationTimes;
  incrementUnread: (stateKey: string) => void;
  markAllRead: () => void;
  markConversationRead: (conv: Conversation) => void;
  trackNewMessage: (msg: Message) => void;
}

export function useUnreadCounts(
  channels: Channel[],
  contacts: Contact[],
  activeConversation: Conversation | null
): UseUnreadCountsResult {
  const [unreadCounts, setUnreadCounts] = useState<Record<string, number>>({});
  const [lastMessageTimes, setLastMessageTimes] = useState<ConversationTimes>(getLastMessageTimes);

  // Track which channels/contacts we've already fetched unreads for
  const fetchedChannels = useRef<Set<string>>(new Set());
  const fetchedContacts = useRef<Set<string>>(new Set());

  // Fetch messages and count unreads for new channels/contacts
  useEffect(() => {
    const newChannels = channels.filter(c => !fetchedChannels.current.has(c.key));
    const newContacts = contacts.filter(c => c.public_key && !fetchedContacts.current.has(c.public_key));

    if (newChannels.length === 0 && newContacts.length === 0) return;

    // Mark as fetched before starting (to avoid duplicate fetches if effect re-runs)
    newChannels.forEach(c => fetchedChannels.current.add(c.key));
    newContacts.forEach(c => fetchedContacts.current.add(c.public_key));

    const fetchAndCountUnreads = async () => {
      const conversations: Array<{ type: 'PRIV' | 'CHAN'; conversation_key: string }> = [
        ...newChannels.map(c => ({ type: 'CHAN' as const, conversation_key: c.key })),
        ...newContacts.map(c => ({ type: 'PRIV' as const, conversation_key: c.public_key })),
      ];

      if (conversations.length === 0) return;

      try {
        const bulkMessages = await api.getMessagesBulk(conversations, 100);
        const currentReadTimes = getLastReadTimes();
        const newUnreadCounts: Record<string, number> = {};
        const newLastMessageTimes: Record<string, number> = {};

        // Process channel messages
        for (const channel of newChannels) {
          const msgs = bulkMessages[`CHAN:${channel.key}`] || [];
          if (msgs.length > 0) {
            const key = getStateKey('channel', channel.key);
            const lastRead = currentReadTimes[key] || 0;

            const unreadCount = msgs.filter(m => !m.outgoing && m.received_at > lastRead).length;
            if (unreadCount > 0) {
              newUnreadCounts[key] = unreadCount;
            }

            const latestTime = Math.max(...msgs.map(m => m.received_at));
            newLastMessageTimes[key] = latestTime;
            setLastMessageTime(key, latestTime);
          }
        }

        // Process contact messages
        for (const contact of newContacts) {
          const msgs = bulkMessages[`PRIV:${contact.public_key}`] || [];
          if (msgs.length > 0) {
            const key = getStateKey('contact', contact.public_key);
            const lastRead = currentReadTimes[key] || 0;

            const unreadCount = msgs.filter(m => !m.outgoing && m.received_at > lastRead).length;
            if (unreadCount > 0) {
              newUnreadCounts[key] = unreadCount;
            }

            const latestTime = Math.max(...msgs.map(m => m.received_at));
            newLastMessageTimes[key] = latestTime;
            setLastMessageTime(key, latestTime);
          }
        }

        if (Object.keys(newUnreadCounts).length > 0) {
          setUnreadCounts(prev => ({ ...prev, ...newUnreadCounts }));
        }
        setLastMessageTimes(getLastMessageTimes());
      } catch (err) {
        console.error('Failed to fetch messages bulk:', err);
      }
    };

    fetchAndCountUnreads();
  }, [channels, contacts]);

  // Mark conversation as read when user views it
  useEffect(() => {
    if (activeConversation && activeConversation.type !== 'raw') {
      const key = getStateKey(
        activeConversation.type as 'channel' | 'contact',
        activeConversation.id
      );
      const now = Math.floor(Date.now() / 1000);
      setLastReadTime(key, now);

      setUnreadCounts((prev) => {
        if (prev[key]) {
          const next = { ...prev };
          delete next[key];
          return next;
        }
        return prev;
      });
    }
  }, [activeConversation]);

  // Increment unread count for a conversation
  const incrementUnread = useCallback((stateKey: string) => {
    setUnreadCounts((prev) => ({
      ...prev,
      [stateKey]: (prev[stateKey] || 0) + 1,
    }));
  }, []);

  // Mark all conversations as read
  const markAllRead = useCallback(() => {
    const now = Math.floor(Date.now() / 1000);

    for (const channel of channels) {
      const key = getStateKey('channel', channel.key);
      setLastReadTime(key, now);
    }

    for (const contact of contacts) {
      if (contact.public_key) {
        const key = getStateKey('contact', contact.public_key);
        setLastReadTime(key, now);
      }
    }

    setUnreadCounts({});
  }, [channels, contacts]);

  // Mark a specific conversation as read
  const markConversationRead = useCallback((conv: Conversation) => {
    if (conv.type === 'raw') return;

    const key = getStateKey(conv.type as 'channel' | 'contact', conv.id);
    const now = Math.floor(Date.now() / 1000);
    setLastReadTime(key, now);

    setUnreadCounts((prev) => {
      if (prev[key]) {
        const next = { ...prev };
        delete next[key];
        return next;
      }
      return prev;
    });
  }, []);

  // Track a new incoming message for unread counts
  const trackNewMessage = useCallback((msg: Message) => {
    let conversationKey: string | null = null;
    if (msg.type === 'CHAN' && msg.conversation_key) {
      conversationKey = getStateKey('channel', msg.conversation_key);
    } else if (msg.type === 'PRIV' && msg.conversation_key) {
      conversationKey = getStateKey('contact', msg.conversation_key);
    }

    if (conversationKey) {
      const timestamp = msg.received_at || Math.floor(Date.now() / 1000);
      const updated = setLastMessageTime(conversationKey, timestamp);
      setLastMessageTimes(updated);
    }
  }, []);

  return {
    unreadCounts,
    lastMessageTimes,
    incrementUnread,
    markAllRead,
    markConversationRead,
    trackNewMessage,
  };
}
