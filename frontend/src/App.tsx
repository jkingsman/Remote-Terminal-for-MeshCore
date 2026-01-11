import { useState, useEffect, useCallback, useMemo, useRef } from 'react';
import { api } from './api';
import { useWebSocket } from './useWebSocket';
import { useRepeaterMode, useUnreadCounts, useConversationMessages } from './hooks';
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
import { getStateKey } from './utils/conversationState';
import { pubkeysMatch, getContactDisplayName } from './utils/pubkey';
import { parseHashConversation, updateUrlHash } from './utils/urlHash';
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

const MAX_RAW_PACKETS = 500;

export function App() {
  const messageInputRef = useRef<MessageInputHandle>(null);
  const activeConversationRef = useRef<Conversation | null>(null);
  const [health, setHealth] = useState<HealthStatus | null>(null);
  const [config, setConfig] = useState<RadioConfig | null>(null);
  const [appSettings, setAppSettings] = useState<AppSettings | null>(null);
  const [contacts, setContacts] = useState<Contact[]>([]);
  const [channels, setChannels] = useState<Channel[]>([]);
  const [rawPackets, setRawPackets] = useState<RawPacket[]>([]);
  const [activeConversation, setActiveConversation] = useState<Conversation | null>(null);
  const [showNewMessage, setShowNewMessage] = useState(false);
  const [showConfig, setShowConfig] = useState(false);
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const [undecryptedCount, setUndecryptedCount] = useState(0);
  const [showCracker, setShowCracker] = useState(false);
  const [crackerRunning, setCrackerRunning] = useState(false);

  // Track previous health status to detect changes
  const prevHealthRef = useRef<HealthStatus | null>(null);

  // Custom hooks for extracted functionality
  const {
    messages,
    messagesLoading,
    loadingOlder,
    hasOlderMessages,
    setMessages,
    fetchMessages,
    fetchOlderMessages,
    addMessageIfNew,
    updateMessageAck,
  } = useConversationMessages(activeConversation);

  const {
    unreadCounts,
    lastMessageTimes,
    incrementUnread,
    markAllRead,
    trackNewMessage,
  } = useUnreadCounts(channels, contacts, activeConversation);

  const {
    repeaterLoggedIn,
    activeContactIsRepeater,
    handleTelemetryRequest,
    handleRepeaterCommand,
  } = useRepeaterMode(activeConversation, contacts, setMessages);

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

      // Check if message belongs to the active conversation
      const isForActiveConversation = (() => {
        if (!activeConv) return false;
        if (msg.type === 'CHAN' && activeConv.type === 'channel') {
          return msg.conversation_key === activeConv.id;
        }
        if (msg.type === 'PRIV' && activeConv.type === 'contact') {
          return msg.conversation_key && pubkeysMatch(activeConv.id, msg.conversation_key);
        }
        return false;
      })();

      // Only add to message list if it's for the active conversation
      if (isForActiveConversation) {
        addMessageIfNew(msg);
      }

      // Track for unread counts and sorting
      trackNewMessage(msg);

      // Count unread for non-active, incoming messages
      if (!msg.outgoing && !isForActiveConversation) {
        let stateKey: string | null = null;
        if (msg.type === 'CHAN' && msg.conversation_key) {
          stateKey = getStateKey('channel', msg.conversation_key);
        } else if (msg.type === 'PRIV' && msg.conversation_key) {
          stateKey = getStateKey('contact', msg.conversation_key);
        }
        if (stateKey) {
          incrementUnread(stateKey);
        }
      }
    },
    onContact: (contact: Contact) => {
      setContacts((prev) => {
        const idx = prev.findIndex((c) => c.public_key === contact.public_key);
        if (idx >= 0) {
          const updated = [...prev];
          const existing = prev[idx];
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
        if (prev.some((p) => p.id === packet.id)) {
          return prev;
        }
        const updated = [...prev, packet];
        if (updated.length > MAX_RAW_PACKETS) {
          return updated.slice(-MAX_RAW_PACKETS);
        }
        return updated;
      });
    },
    onMessageAcked: (messageId: number, ackCount: number) => {
      updateMessageAck(messageId, ackCount);
    },
  }), [addMessageIfNew, trackNewMessage, incrementUnread, updateMessageAck]);

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

  // Initial fetch for config and settings
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

    const conv = resolveHashToConversation();
    if (conv) {
      setActiveConversation(conv);
      hasSetDefaultConversation.current = true;
      return;
    }

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

  // Keep ref in sync and update URL hash
  useEffect(() => {
    activeConversationRef.current = activeConversation;
    if (activeConversation) {
      updateUrlHash(activeConversation);
    }
  }, [activeConversation]);

  // Send message handler
  const handleSendMessage = useCallback(
    async (text: string) => {
      if (!activeConversation) return;

      if (activeConversation.type === 'channel') {
        await api.sendChannelMessage(activeConversation.id, text);
      } else {
        await api.sendDirectMessage(activeConversation.id, text);
      }
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
    setHealth((prev) =>
      prev ? { ...prev, radio_connected: false } : prev
    );
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
      const data = await api.getChannels();
      setChannels(data);

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
      onMarkAllRead={markAllRead}
    />
  );

  return (
    <div className="flex flex-col h-dvh">
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
                  onSend={
                    activeContactIsRepeater
                      ? (repeaterLoggedIn ? handleRepeaterCommand : handleTelemetryRequest)
                      : handleSendMessage
                  }
                  disabled={!health?.radio_connected}
                  isRepeaterMode={activeContactIsRepeater && !repeaterLoggedIn}
                  placeholder={
                    !health?.radio_connected
                      ? 'Radio not connected'
                      : activeContactIsRepeater
                        ? (repeaterLoggedIn
                            ? 'Send CLI command (requires admin login)...'
                            : `Enter password for ${activeConversation.name} (or . for none)...`)
                        : `Message ${activeConversation.name}...`
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
