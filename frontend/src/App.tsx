import { useState, useEffect, useCallback, useMemo, useRef } from 'react';
import { api } from './api';
import { useWebSocket } from './useWebSocket';
import { StatusBar } from './components/StatusBar';
import { Sidebar } from './components/Sidebar';
import { MessageList } from './components/MessageList';
import { MessageInput, type MessageInputHandle } from './components/MessageInput';
import { NewMessageModal } from './components/NewMessageModal';
import { ConfigModal } from './components/ConfigModal';
import { RawPacketList } from './components/RawPacketList';
import { CrackerPanel } from './components/CrackerPanel';
import { Sheet, SheetContent, SheetHeader, SheetTitle } from './components/ui/sheet';
import { Toaster, toast } from './components/ui/sonner';
import {
  getLastMessageTimes,
  getLastReadTimes,
  setLastMessageTime,
  setLastReadTime,
  getStateKey,
  type ConversationTimes,
} from './utils/conversationState';
import { pubkeysMatch, getContactDisplayName } from './utils/pubkey';
import { cn } from '@/lib/utils';
import type {
  AppSettings,
  AppSettingsUpdate,
  Contact,
  Channel,
  Conversation,
  HealthStatus,
  Message,
  RawPacket,
  RadioConfig,
  RadioConfigUpdate,
} from './types';

const MAX_RAW_PACKETS = 500; // Limit stored packets to prevent memory issues

// Generate a key for deduplicating messages by content
function getMessageContentKey(msg: Message): string {
  return `${msg.type}-${msg.conversation_key}-${msg.text}-${msg.sender_timestamp}`;
}

// Parse URL hash to get conversation (e.g., #channel/Public or #contact/JohnDoe or #raw)
function parseHashConversation(): { type: 'channel' | 'contact' | 'raw'; name: string } | null {
  const hash = window.location.hash.slice(1); // Remove leading #
  if (!hash) return null;

  if (hash === 'raw') {
    return { type: 'raw', name: 'raw' };
  }

  const slashIndex = hash.indexOf('/');
  if (slashIndex === -1) return null;

  const type = hash.slice(0, slashIndex);
  const name = decodeURIComponent(hash.slice(slashIndex + 1));

  if ((type === 'channel' || type === 'contact') && name) {
    return { type, name };
  }
  return null;
}

// Generate URL hash from conversation
function getConversationHash(conv: Conversation | null): string {
  if (!conv) return '';
  if (conv.type === 'raw') return '#raw';
  // Strip leading # from channel names for cleaner URLs
  const name = conv.type === 'channel' && conv.name.startsWith('#')
    ? conv.name.slice(1)
    : conv.name;
  return `#${conv.type}/${encodeURIComponent(name)}`;
}

export function App() {
  const messageInputRef = useRef<MessageInputHandle>(null);
  const activeConversationRef = useRef<Conversation | null>(null);
  const seenMessageContent = useRef<Set<string>>(new Set());
  const [health, setHealth] = useState<HealthStatus | null>(null);
  const [config, setConfig] = useState<RadioConfig | null>(null);
  const [appSettings, setAppSettings] = useState<AppSettings | null>(null);
  const [contacts, setContacts] = useState<Contact[]>([]);
  const [channels, setChannels] = useState<Channel[]>([]);
  const [messages, setMessages] = useState<Message[]>([]);
  const [hasOlderMessages, setHasOlderMessages] = useState(false);
  const [loadingOlder, setLoadingOlder] = useState(false);
  const [rawPackets, setRawPackets] = useState<RawPacket[]>([]);
  const [activeConversation, setActiveConversation] = useState<Conversation | null>(null);
  const [messagesLoading, setMessagesLoading] = useState(false);
  const [showNewMessage, setShowNewMessage] = useState(false);
  const [showConfig, setShowConfig] = useState(false);
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const [undecryptedCount, setUndecryptedCount] = useState(0);
  const [showCracker, setShowCracker] = useState(false);
  const [crackerRunning, setCrackerRunning] = useState(false);
  // Track last message times (persisted in localStorage, used for sorting)
  const [lastMessageTimes, setLastMessageTimes] = useState<ConversationTimes>(getLastMessageTimes);
  // Track unread counts (calculated on load and incremented during session)
  const [unreadCounts, setUnreadCounts] = useState<Record<string, number>>({});

  // Track previous health status to detect changes
  const prevHealthRef = useRef<HealthStatus | null>(null);

  // WebSocket handlers - memoized to prevent reconnection loops
  const wsHandlers = useMemo(() => ({
    onHealth: (data: HealthStatus) => {
      const prev = prevHealthRef.current;
      prevHealthRef.current = data;
      setHealth(data);

      // Show toast on connection status change
      if (prev !== null && prev.radio_connected !== data.radio_connected) {
        if (data.radio_connected) {
          toast.success('Radio connected', {
            description: data.serial_port ? `Connected to ${data.serial_port}` : undefined,
          });
        } else {
          toast.error('Radio disconnected', {
            description: 'Check radio connection and power',
          });
        }
      }
    },
    onError: (error: { message: string; details?: string }) => {
      toast.error(error.message, {
        description: error.details,
      });
    },
    onContacts: (data: Contact[]) => setContacts(data),
    onChannels: (data: Channel[]) => setChannels(data),
    onMessage: (msg: Message) => {
      const activeConv = activeConversationRef.current;

      // Skip duplicate messages (same content + timestamp)
      const contentKey = getMessageContentKey(msg);
      if (seenMessageContent.current.has(contentKey)) {
        console.debug('Duplicate message content ignored:', contentKey.slice(0, 50));
        return;
      }
      seenMessageContent.current.add(contentKey);
      // Limit set size to prevent memory issues (keep last 1000)
      if (seenMessageContent.current.size > 1000) {
        const entries = Array.from(seenMessageContent.current);
        seenMessageContent.current = new Set(entries.slice(-500));
      }

      // Determine conversation key for this message
      let conversationKey: string | null = null;
      if (msg.type === 'CHAN' && msg.conversation_key) {
        conversationKey = getStateKey('channel', msg.conversation_key);
      } else if (msg.type === 'PRIV' && msg.conversation_key) {
        conversationKey = getStateKey('contact', msg.conversation_key);
      }

      // Check if message belongs to the active conversation
      const isForActiveConversation = (() => {
        if (!activeConv) return false;
        if (msg.type === 'CHAN' && activeConv.type === 'channel') {
          return msg.conversation_key === activeConv.id;
        }
        if (msg.type === 'PRIV' && activeConv.type === 'contact') {
          // Match by public key or prefix (either could be full key or prefix)
          return msg.conversation_key && pubkeysMatch(activeConv.id, msg.conversation_key);
        }
        return false;
      })();

      // Only add to message list if it's for the active conversation
      if (isForActiveConversation) {
        setMessages((prev) => {
          if (prev.some((m) => m.id === msg.id)) {
            return prev;
          }
          return [...prev, msg];
        });
      }

      // Track last message time for sorting and unread detection
      if (conversationKey) {
        const timestamp = msg.received_at || Math.floor(Date.now() / 1000);
        const updated = setLastMessageTime(conversationKey, timestamp);
        setLastMessageTimes(updated);

        // Count unread messages during this session (for non-active, incoming messages)
        if (!msg.outgoing && !isForActiveConversation) {
          setUnreadCounts((prev) => ({
            ...prev,
            [conversationKey]: (prev[conversationKey] || 0) + 1,
          }));
        }
      }
    },
    onContact: (contact: Contact) => {
      // Update or add contact, preserving existing non-null values
      setContacts((prev) => {
        const idx = prev.findIndex((c) => c.public_key === contact.public_key);
        if (idx >= 0) {
          const updated = [...prev];
          const existing = prev[idx];
          // Merge: prefer new non-null values, but keep existing values if new is null
          updated[idx] = {
            ...existing,
            ...contact,
            name: contact.name ?? existing.name,
            last_path: contact.last_path ?? existing.last_path,
            lat: contact.lat ?? existing.lat,
            lon: contact.lon ?? existing.lon,
          };
          return updated;
        }
        return [...prev, contact as Contact];
      });
    },
    onRawPacket: (packet: RawPacket) => {
      setRawPackets((prev) => {
        // Check if packet already exists
        if (prev.some((p) => p.id === packet.id)) {
          return prev;
        }
        // Limit to MAX_RAW_PACKETS, removing oldest
        const updated = [...prev, packet];
        if (updated.length > MAX_RAW_PACKETS) {
          return updated.slice(-MAX_RAW_PACKETS);
        }
        return updated;
      });
    },
    onMessageAcked: (messageId: number) => {
      // Update message acked status
      setMessages((prev) => {
        const idx = prev.findIndex((m) => m.id === messageId);
        if (idx >= 0) {
          const updated = [...prev];
          updated[idx] = { ...prev[idx], acked: true };
          return updated;
        }
        return prev;
      });
    },
  }), []);

  // Connect to WebSocket
  useWebSocket(wsHandlers);

  // Fetch radio config (not sent via WebSocket)
  const fetchConfig = useCallback(async () => {
    try {
      const data = await api.getRadioConfig();
      setConfig(data);
    } catch (err) {
      console.error('Failed to fetch config:', err);
    }
  }, []);

  // Fetch app settings
  const fetchAppSettings = useCallback(async () => {
    try {
      const data = await api.getSettings();
      setAppSettings(data);
    } catch (err) {
      console.error('Failed to fetch app settings:', err);
    }
  }, []);

  // Fetch undecrypted packet count
  const fetchUndecryptedCount = useCallback(async () => {
    try {
      const data = await api.getUndecryptedPacketCount();
      setUndecryptedCount(data.count);
    } catch (err) {
      console.error('Failed to fetch undecrypted count:', err);
    }
  }, []);

  const MESSAGE_PAGE_SIZE = 200;

  // Fetch messages for active conversation
  const fetchMessages = useCallback(async (showLoading = false) => {
    if (!activeConversation) {
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
    if (!activeConversation || loadingOlder || !hasOlderMessages) return;

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
      }
      // If we got less than a full page, no more messages
      setHasOlderMessages(data.length >= MESSAGE_PAGE_SIZE);
    } catch (err) {
      console.error('Failed to fetch older messages:', err);
    } finally {
      setLoadingOlder(false);
    }
  }, [activeConversation, loadingOlder, hasOlderMessages, messages.length]);

  // Initial fetch for config and settings (WebSocket handles health/contacts/channels)
  useEffect(() => {
    fetchConfig();
    fetchAppSettings();
    fetchUndecryptedCount();
  }, [fetchConfig, fetchAppSettings, fetchUndecryptedCount]);

  // Resolve URL hash to a conversation
  const resolveHashToConversation = useCallback((): Conversation | null => {
    const hashConv = parseHashConversation();
    if (!hashConv) return null;

    if (hashConv.type === 'raw') {
      return { type: 'raw', id: 'raw', name: 'Raw Packet Feed' };
    }
    if (hashConv.type === 'channel') {
      // Match with or without leading # (URL strips it for cleaner URLs)
      const channel = channels.find(c => c.name === hashConv.name || c.name === `#${hashConv.name}`);
      if (channel) {
        return { type: 'channel', id: channel.key, name: channel.name };
      }
    }
    if (hashConv.type === 'contact') {
      const contact = contacts.find(c => getContactDisplayName(c.name, c.public_key) === hashConv.name);
      if (contact) {
        return {
          type: 'contact',
          id: contact.public_key,
          name: getContactDisplayName(contact.name, contact.public_key),
        };
      }
    }
    return null;
  }, [channels, contacts]);

  // Set initial conversation from URL hash or default to Public channel
  const hasSetDefaultConversation = useRef(false);
  useEffect(() => {
    if (hasSetDefaultConversation.current || activeConversation) return;
    if (channels.length === 0 && contacts.length === 0) return;

    // Try to restore from URL hash first
    const conv = resolveHashToConversation();
    if (conv) {
      setActiveConversation(conv);
      hasSetDefaultConversation.current = true;
      return;
    }

    // Fall back to Public channel
    const publicChannel = channels.find(c => c.name === 'Public');
    if (publicChannel) {
      setActiveConversation({
        type: 'channel',
        id: publicChannel.key,
        name: publicChannel.name,
      });
      hasSetDefaultConversation.current = true;
    }
  }, [channels, contacts, activeConversation, resolveHashToConversation]);

  // Fetch messages and count unreads for all conversations on load (single bulk request)
  const fetchedChannels = useRef<Set<string>>(new Set());
  const fetchedContacts = useRef<Set<string>>(new Set());

  useEffect(() => {
    // Find channels and contacts we haven't fetched yet
    const newChannels = channels.filter(c => !fetchedChannels.current.has(c.key));
    const newContacts = contacts.filter(c => c.public_key && !fetchedContacts.current.has(c.public_key));

    if (newChannels.length === 0 && newContacts.length === 0) return;

    // Mark as fetched before starting (to avoid duplicate fetches if effect re-runs)
    newChannels.forEach(c => fetchedChannels.current.add(c.key));
    newContacts.forEach(c => fetchedContacts.current.add(c.public_key));

    const fetchAndCountUnreads = async () => {
      // Build list of conversations to fetch
      const conversations: Array<{ type: 'PRIV' | 'CHAN'; conversation_key: string }> = [
        ...newChannels.map(c => ({ type: 'CHAN' as const, conversation_key: c.key })),
        ...newContacts.map(c => ({ type: 'PRIV' as const, conversation_key: c.public_key })),
      ];

      if (conversations.length === 0) return;

      try {
        // Single bulk request for all conversations
        const bulkMessages = await api.getMessagesBulk(conversations, 100);

        // Read lastReadTimes fresh from localStorage for accurate comparison
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

        // Update state with all the counts and times
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

  // Keep ref in sync with state and mark conversation as read when viewed
  useEffect(() => {
    activeConversationRef.current = activeConversation;

    // Mark conversation as read when user views it
    if (activeConversation && activeConversation.type !== 'raw') {
      const key = getStateKey(
        activeConversation.type as 'channel' | 'contact',
        activeConversation.id
      );
      // Update localStorage-based read time
      const now = Math.floor(Date.now() / 1000);
      setLastReadTime(key, now);

      // Clear unread count for this conversation
      setUnreadCounts((prev) => {
        if (prev[key]) {
          const next = { ...prev };
          delete next[key];
          return next;
        }
        return prev;
      });
    }

    // Update URL hash (replaceState doesn't add to history)
    if (activeConversation) {
      const newHash = getConversationHash(activeConversation);
      if (newHash !== window.location.hash) {
        window.history.replaceState(null, '', newHash);
      }
    }
  }, [activeConversation]);

  // Fetch messages when conversation changes
  useEffect(() => {
    fetchMessages(true);
  }, [fetchMessages]);

  // Send message handler
  const handleSendMessage = useCallback(
    async (text: string) => {
      if (!activeConversation) return;

      if (activeConversation.type === 'channel') {
        await api.sendChannelMessage(activeConversation.id, text);
      } else {
        await api.sendDirectMessage(activeConversation.id, text);
      }
      // Message will arrive via WebSocket, but fetch to be safe
      await fetchMessages();
    },
    [activeConversation, fetchMessages]
  );

  // Config save handler
  const handleSaveConfig = useCallback(async (update: RadioConfigUpdate) => {
    await api.updateRadioConfig(update);
    await fetchConfig();
  }, [fetchConfig]);

  // App settings save handler
  const handleSaveAppSettings = useCallback(async (update: AppSettingsUpdate) => {
    await api.updateSettings(update);
    await fetchAppSettings();
  }, [fetchAppSettings]);

  // Set private key handler
  const handleSetPrivateKey = useCallback(async (key: string) => {
    await api.setPrivateKey(key);
    await fetchConfig();
  }, [fetchConfig]);

  // Reboot radio handler
  const handleReboot = useCallback(async () => {
    await api.rebootRadio();
    // Immediately show disconnected state
    setHealth((prev) =>
      prev ? { ...prev, radio_connected: false } : prev
    );
    // Health updates will come via WebSocket when reconnected
    // But also poll as backup
    const pollUntilReconnected = async () => {
      for (let i = 0; i < 30; i++) {
        await new Promise((r) => setTimeout(r, 1000));
        try {
          const data = await api.getHealth();
          setHealth(data);
          if (data.radio_connected) {
            fetchConfig();
            return;
          }
        } catch {
          // Keep polling
        }
      }
    };
    pollUntilReconnected();
  }, [fetchConfig]);

  // Send flood advertisement handler
  const handleAdvertise = useCallback(async () => {
    try {
      await api.sendAdvertisement(true);
    } catch (err) {
      console.error('Failed to send advertisement:', err);
    }
  }, []);

  // Handle sender click to add mention
  const handleSenderClick = useCallback((sender: string) => {
    messageInputRef.current?.appendText(`@[${sender}] `);
  }, []);

  // Handle conversation selection (closes sidebar on mobile)
  const handleSelectConversation = useCallback((conv: Conversation) => {
    setActiveConversation(conv);
    setSidebarOpen(false);
  }, []);

  // Mark all conversations as read
  const handleMarkAllRead = useCallback(() => {
    const now = Math.floor(Date.now() / 1000);

    // Update localStorage for all channels
    for (const channel of channels) {
      const key = getStateKey('channel', channel.key);
      setLastReadTime(key, now);
    }

    // Update localStorage for all contacts
    for (const contact of contacts) {
      if (contact.public_key) {
        const key = getStateKey('contact', contact.public_key);
        setLastReadTime(key, now);
      }
    }

    // Clear all unread counts
    setUnreadCounts({});
  }, [channels, contacts]);

  // Delete channel handler
  const handleDeleteChannel = useCallback(async (key: string) => {
    if (!confirm('Delete this channel? Message history will be preserved.')) return;
    try {
      await api.deleteChannel(key);
      setChannels((prev) => prev.filter((c) => c.key !== key));
      setActiveConversation(null);
    } catch (err) {
      console.error('Failed to delete channel:', err);
    }
  }, []);

  // Delete contact handler
  const handleDeleteContact = useCallback(async (publicKey: string) => {
    if (!confirm('Delete this contact? Message history will be preserved.')) return;
    try {
      await api.deleteContact(publicKey);
      setContacts((prev) => prev.filter((c) => c.public_key !== publicKey));
      setActiveConversation(null);
    } catch (err) {
      console.error('Failed to delete contact:', err);
    }
  }, []);

  // Create contact handler
  const handleCreateContact = useCallback(
    async (name: string, publicKey: string, tryHistorical: boolean) => {
      const newContact: Contact = {
        public_key: publicKey,
        name,
        type: 0,
        flags: 0,
        last_path: null,
        last_path_len: -1,
        last_advert: null,
        lat: null,
        lon: null,
        last_seen: null,
        on_radio: false,
      };
      setContacts((prev) => [...prev, newContact]);

      // Open the new contact
      setActiveConversation({
        type: 'contact',
        id: publicKey,
        name: getContactDisplayName(name, publicKey),
      });

      if (tryHistorical) {
        console.log('Contact historical decryption not yet supported');
      }
    },
    []
  );

  // Create channel handler
  const handleCreateChannel = useCallback(
    async (name: string, key: string, tryHistorical: boolean) => {
      const created = await api.createChannel(name, key);
      // Channel will be broadcast via WebSocket, but fetch to be safe
      const data = await api.getChannels();
      setChannels(data);

      // Open the new channel (use created.key as the id)
      setActiveConversation({
        type: 'channel',
        id: created.key,
        name,
      });

      if (tryHistorical) {
        await api.decryptHistoricalPackets({
          key_type: 'channel',
          channel_key: created.key,
        });
        fetchUndecryptedCount();
      }
    },
    [fetchUndecryptedCount]
  );

  // Create hashtag channel handler
  const handleCreateHashtagChannel = useCallback(
    async (name: string, tryHistorical: boolean) => {
      const channelName = name.startsWith('#') ? name : `#${name}`;

      const created = await api.createChannel(channelName);
      const data = await api.getChannels();
      setChannels(data);

      // Open the new channel (use created.key as the id)
      setActiveConversation({
        type: 'channel',
        id: created.key,
        name: channelName,
      });

      if (tryHistorical) {
        await api.decryptHistoricalPackets({
          key_type: 'channel',
          channel_name: channelName,
        });
        fetchUndecryptedCount();
      }
    },
    [fetchUndecryptedCount]
  );

  // Sidebar content (shared between desktop and mobile)
  const sidebarContent = (
    <Sidebar
      contacts={contacts}
      channels={channels}
      activeConversation={activeConversation}
      onSelectConversation={handleSelectConversation}
      onNewMessage={() => {
        setShowNewMessage(true);
        setSidebarOpen(false);
      }}
      lastMessageTimes={lastMessageTimes}
      unreadCounts={unreadCounts}
      showCracker={showCracker}
      crackerRunning={crackerRunning}
      onToggleCracker={() => setShowCracker((prev) => !prev)}
      onMarkAllRead={handleMarkAllRead}
    />
  );

  return (
    <div className="flex flex-col h-screen">
      <StatusBar
        health={health}
        config={config}
        onConfigClick={() => setShowConfig(true)}
        onAdvertise={handleAdvertise}
        onMenuClick={() => setSidebarOpen(true)}
      />

      <div className="flex flex-1 overflow-hidden">
        {/* Desktop sidebar - hidden on mobile */}
        <div className="hidden md:block">
          {sidebarContent}
        </div>

        {/* Mobile sidebar - Sheet that slides in */}
        <Sheet open={sidebarOpen} onOpenChange={setSidebarOpen}>
          <SheetContent side="left" className="w-[280px] p-0 flex flex-col" hideCloseButton>
            <SheetHeader className="sr-only">
              <SheetTitle>Navigation</SheetTitle>
            </SheetHeader>
            <div className="flex-1 overflow-hidden">
              {sidebarContent}
            </div>
          </SheetContent>
        </Sheet>

        <div className="flex-1 flex flex-col bg-background">
          {activeConversation ? (
            activeConversation.type === 'raw' ? (
              <>
                <div className="flex justify-between items-center px-4 py-3 border-b border-border font-medium">Raw Packet Feed</div>
                <div className="flex-1 overflow-hidden">
                  <RawPacketList packets={rawPackets} />
                </div>
              </>
            ) : (
              <>
                <div className="flex justify-between items-center px-4 py-3 border-b border-border font-medium">
                  <span className="flex flex-col sm:flex-row sm:items-center sm:gap-2">
                    <span>
                      {activeConversation.type === 'channel' && !activeConversation.name.startsWith('#') ? '#' : ''}
                      {activeConversation.name}
                    </span>
                    <span className="font-normal text-xs text-muted-foreground font-mono">
                      {activeConversation.id}
                    </span>
                  </span>
                  {!(activeConversation.type === 'channel' && activeConversation.name === 'Public') && (
                    <button
                      className="py-1 px-3 bg-destructive/20 border border-destructive/30 text-destructive rounded text-xs cursor-pointer hover:bg-destructive/30"
                      onClick={() => {
                        if (activeConversation.type === 'channel') {
                          handleDeleteChannel(activeConversation.id);
                        } else {
                          handleDeleteContact(activeConversation.id);
                        }
                      }}
                    >
                      Delete
                    </button>
                  )}
                </div>
                <MessageList
                  messages={messages}
                  contacts={contacts}
                  loading={messagesLoading}
                  loadingOlder={loadingOlder}
                  hasOlderMessages={hasOlderMessages}
                  onSenderClick={activeConversation.type === 'channel' ? handleSenderClick : undefined}
                  onLoadOlder={fetchOlderMessages}
                  radioName={config?.name}
                />
                <MessageInput
                  ref={messageInputRef}
                  onSend={handleSendMessage}
                  disabled={!health?.radio_connected}
                  placeholder={
                    health?.radio_connected
                      ? `Message ${activeConversation.name}...`
                      : 'Radio not connected'
                  }
                />
              </>
            )
          ) : (
            <div className="flex-1 flex items-center justify-center text-muted-foreground">
              Select a conversation or start a new one
            </div>
          )}
        </div>
      </div>

      {/* Global Cracker Panel - always rendered to maintain state */}
      <div
        className={cn(
          "border-t border-border bg-background transition-all duration-200 overflow-hidden",
          showCracker ? "h-[275px]" : "h-0"
        )}
      >
        <CrackerPanel
          packets={rawPackets}
          channels={channels}
          onChannelCreate={async (name, key) => {
            const created = await api.createChannel(name, key);
            const data = await api.getChannels();
            setChannels(data);
            await api.decryptHistoricalPackets({
              key_type: 'channel',
              channel_key: created.key,
            });
            fetchUndecryptedCount();
          }}
          onRunningChange={setCrackerRunning}
        />
      </div>

      <NewMessageModal
        open={showNewMessage}
        contacts={contacts}
        undecryptedCount={undecryptedCount}
        onClose={() => setShowNewMessage(false)}
        onSelectConversation={(conv) => {
          setActiveConversation(conv);
          setShowNewMessage(false);
        }}
        onCreateContact={handleCreateContact}
        onCreateChannel={handleCreateChannel}
        onCreateHashtagChannel={handleCreateHashtagChannel}
      />

      <ConfigModal
        open={showConfig}
        config={config}
        appSettings={appSettings}
        onClose={() => setShowConfig(false)}
        onSave={handleSaveConfig}
        onSaveAppSettings={handleSaveAppSettings}
        onSetPrivateKey={handleSetPrivateKey}
        onReboot={handleReboot}
      />

      <Toaster position="top-right" />
    </div>
  );
}
