import { useState, useCallback, useEffect, useRef } from 'react';
import { api } from '../api';
import type { Conversation, Message } from '../types';

const MESSAGE_PAGE_SIZE = 200;

// Generate a key for deduplicating messages by content
export function getMessageContentKey(msg: Message): string {
  return `${msg.type}-${msg.conversation_key}-${msg.text}-${msg.sender_timestamp}`;
}

export interface UseConversationMessagesResult {
  messages: Message[];
  messagesLoading: boolean;
  loadingOlder: boolean;
  hasOlderMessages: boolean;
  setMessages: React.Dispatch<React.SetStateAction<Message[]>>;
  fetchMessages: (showLoading?: boolean) => Promise<void>;
  fetchOlderMessages: () => Promise<void>;
  addMessageIfNew: (msg: Message) => boolean;
  updateMessageAck: (messageId: number, ackCount: number) => void;
}

export function useConversationMessages(
  activeConversation: Conversation | null
): UseConversationMessagesResult {
  const [messages, setMessages] = useState<Message[]>([]);
  const [messagesLoading, setMessagesLoading] = useState(false);
  const [loadingOlder, setLoadingOlder] = useState(false);
  const [hasOlderMessages, setHasOlderMessages] = useState(false);

  // Track seen message content for deduplication
  const seenMessageContent = useRef<Set<string>>(new Set());

  // Fetch messages for active conversation
  const fetchMessages = useCallback(async (showLoading = false) => {
    if (!activeConversation || activeConversation.type === 'raw') {
      setMessages([]);
      setHasOlderMessages(false);
      return;
    }

    if (showLoading) {
      setMessagesLoading(true);
    }
    try {
      const data = await api.getMessages({
        type: activeConversation.type === 'channel' ? 'CHAN' : 'PRIV',
        conversation_key: activeConversation.id,
        limit: MESSAGE_PAGE_SIZE,
      });
      setMessages(data);
      // Track seen content for new messages
      seenMessageContent.current.clear();
      for (const msg of data) {
        seenMessageContent.current.add(getMessageContentKey(msg));
      }
      // If we got a full page, there might be more
      setHasOlderMessages(data.length >= MESSAGE_PAGE_SIZE);
    } catch (err) {
      console.error('Failed to fetch messages:', err);
    } finally {
      if (showLoading) {
        setMessagesLoading(false);
      }
    }
  }, [activeConversation]);

  // Fetch older messages (pagination)
  const fetchOlderMessages = useCallback(async () => {
    if (!activeConversation || activeConversation.type === 'raw' || loadingOlder || !hasOlderMessages) return;

    setLoadingOlder(true);
    try {
      const data = await api.getMessages({
        type: activeConversation.type === 'channel' ? 'CHAN' : 'PRIV',
        conversation_key: activeConversation.id,
        limit: MESSAGE_PAGE_SIZE,
        offset: messages.length,
      });

      if (data.length > 0) {
        // Prepend older messages (they come sorted DESC, so older are at the end)
        setMessages(prev => [...prev, ...data]);
        // Track seen content
        for (const msg of data) {
          seenMessageContent.current.add(getMessageContentKey(msg));
        }
      }
      // If we got less than a full page, no more messages
      setHasOlderMessages(data.length >= MESSAGE_PAGE_SIZE);
    } catch (err) {
      console.error('Failed to fetch older messages:', err);
    } finally {
      setLoadingOlder(false);
    }
  }, [activeConversation, loadingOlder, hasOlderMessages, messages.length]);

  // Fetch messages when conversation changes
  useEffect(() => {
    fetchMessages(true);
  }, [fetchMessages]);

  // Add a message if it's new (deduplication)
  // Returns true if the message was added, false if it was a duplicate
  const addMessageIfNew = useCallback((msg: Message): boolean => {
    const contentKey = getMessageContentKey(msg);
    if (seenMessageContent.current.has(contentKey)) {
      console.debug('Duplicate message content ignored:', contentKey.slice(0, 50));
      return false;
    }
    seenMessageContent.current.add(contentKey);

    // Limit set size to prevent memory issues (keep last 500)
    if (seenMessageContent.current.size > 1000) {
      const entries = Array.from(seenMessageContent.current);
      seenMessageContent.current = new Set(entries.slice(-500));
    }

    setMessages((prev) => {
      if (prev.some((m) => m.id === msg.id)) {
        return prev;
      }
      return [...prev, msg];
    });

    return true;
  }, []);

  // Update a message's ack count
  const updateMessageAck = useCallback((messageId: number, ackCount: number) => {
    setMessages((prev) => {
      const idx = prev.findIndex((m) => m.id === messageId);
      if (idx >= 0) {
        const updated = [...prev];
        updated[idx] = { ...prev[idx], acked: ackCount };
        return updated;
      }
      return prev;
    });
  }, []);

  return {
    messages,
    messagesLoading,
    loadingOlder,
    hasOlderMessages,
    setMessages,
    fetchMessages,
    fetchOlderMessages,
    addMessageIfNew,
    updateMessageAck,
  };
}
