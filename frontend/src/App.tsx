import { useState, useEffect, useCallback, useMemo, useRef } from 'react';
import { api } from './api';
import { useWebSocket } from './useWebSocket';
import {
  useRepeaterMode,
  useUnreadCounts,
  useConversationMessages,
  getMessageContentKey,
} from './hooks';
import { StatusBar } from './components/StatusBar';
import { Sidebar } from './components/Sidebar';
import { MessageList } from './components/MessageList';
import { MessageInput, type MessageInputHandle } from './components/MessageInput';
import { NewMessageModal } from './components/NewMessageModal';
import { SettingsModal } from './components/SettingsModal';
import { RawPacketList } from './components/RawPacketList';
import { MapView } from './components/MapView';
import { VisualizerView } from './components/VisualizerView';
import { CrackerPanel } from './components/CrackerPanel';
import { Sheet, SheetContent, SheetHeader, SheetTitle } from './components/ui/sheet';
import { Toaster, toast } from './components/ui/sonner';
import {
  getStateKey,
  initLastMessageTimes,
  loadLocalStorageLastMessageTimes,
  loadLocalStorageSortOrder,
  clearLocalStorageConversationState,
} from './utils/conversationState';
import { formatTime } from './utils/messageParser';
import { getContactDisplayName } from './utils/pubkey';
import { parseHashConversation, updateUrlHash, getMapFocusHash } from './utils/urlHash';
import { isValidLocation, calculateDistance, formatDistance } from './utils/pathUtils';
import {
  isFavorite,
  loadLocalStorageFavorites,
  clearLocalStorageFavorites,
} from './utils/favorites';
import { cn } from '@/lib/utils';
import type {
  AppSettings,
  AppSettingsUpdate,
  Contact,
  Channel,
  Conversation,
  Favorite,
  HealthStatus,
  Message,
  MessagePath,
  RawPacket,
  RadioConfig,
  RadioConfigUpdate,
} from './types';

const MAX_RAW_PACKETS = 500;

export function App() {
  const messageInputRef = useRef<MessageInputHandle>(null);
  const activeConversationRef = useRef<Conversation | null>(null);
  // Track seen message content to prevent duplicate unread increments
  // Uses content-based key (type-conversation_key-text-sender_timestamp) for deduplication
  const seenMessageContentRef = useRef<Set<string>>(new Set());
  const [health, setHealth] = useState<HealthStatus | null>(null);
  const [config, setConfig] = useState<RadioConfig | null>(null);
  const [appSettings, setAppSettings] = useState<AppSettings | null>(null);
  const [contacts, setContacts] = useState<Contact[]>([]);
  const [channels, setChannels] = useState<Channel[]>([]);
  const [rawPackets, setRawPackets] = useState<RawPacket[]>([]);
  const [activeConversation, setActiveConversation] = useState<Conversation | null>(null);
  const [showNewMessage, setShowNewMessage] = useState(false);
  const [showSettings, setShowSettings] = useState(false);
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const [undecryptedCount, setUndecryptedCount] = useState(0);
  const [showCracker, setShowCracker] = useState(false);
  const [crackerRunning, setCrackerRunning] = useState(false);

  // Favorites are now stored server-side in appSettings
  const favorites: Favorite[] = appSettings?.favorites ?? [];

  // Track previous health status to detect changes
  const prevHealthRef = useRef<HealthStatus | null>(null);

  // Keep user's name in ref for mention detection in WebSocket callback
  const myNameRef = useRef<string | null>(null);
  useEffect(() => {
    myNameRef.current = config?.name ?? null;
  }, [config?.name]);

  // Check if a message mentions the user
  const checkMention = useCallback((text: string): boolean => {
    const name = myNameRef.current;
    if (!name) return false;
    const escaped = name.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
    const mentionPattern = new RegExp(`@\\[${escaped}\\]`, 'i');
    return mentionPattern.test(text);
  }, []);

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
    mentions,
    lastMessageTimes,
    incrementUnread,
    markAllRead,
    trackNewMessage,
  } = useUnreadCounts(channels, contacts, activeConversation, config?.name);

  const {
    repeaterLoggedIn,
    activeContactIsRepeater,
    handleTelemetryRequest,
    handleRepeaterCommand,
  } = useRepeaterMode(activeConversation, contacts, setMessages);

  // WebSocket handlers - memoized to prevent reconnection loops
  const wsHandlers = useMemo(
    () => ({
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
            // Refresh config after reconnection (may have changed after reboot)
            api.getRadioConfig().then(setConfig).catch(console.error);
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
      onSuccess: (success: { message: string; details?: string }) => {
        toast.success(success.message, {
          description: success.details,
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
            return msg.conversation_key === activeConv.id;
          }
          return false;
        })();

        // Only add to message list if it's for the active conversation
        if (isForActiveConversation) {
          addMessageIfNew(msg);
        }

        // Track for unread counts and sorting
        trackNewMessage(msg);

        // Count unread for non-active, incoming messages (with deduplication)
        if (!msg.outgoing && !isForActiveConversation) {
          // Skip if we've already seen this message content (prevents duplicate increments
          // when the same message arrives via multiple mesh paths)
          const contentKey = getMessageContentKey(msg);
          if (seenMessageContentRef.current.has(contentKey)) {
            return;
          }
          seenMessageContentRef.current.add(contentKey);

          // Limit set size to prevent memory issues
          if (seenMessageContentRef.current.size > 1000) {
            const keys = Array.from(seenMessageContentRef.current);
            seenMessageContentRef.current = new Set(keys.slice(-500));
          }

          let stateKey: string | null = null;
          if (msg.type === 'CHAN' && msg.conversation_key) {
            stateKey = getStateKey('channel', msg.conversation_key);
          } else if (msg.type === 'PRIV' && msg.conversation_key) {
            stateKey = getStateKey('contact', msg.conversation_key);
          }
          if (stateKey) {
            const hasMention = checkMention(msg.text);
            incrementUnread(stateKey, hasMention);
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
      onMessageAcked: (messageId: number, ackCount: number, paths?: MessagePath[]) => {
        updateMessageAck(messageId, ackCount, paths);
      },
    }),
    [addMessageIfNew, trackNewMessage, incrementUnread, updateMessageAck, checkMention]
  );

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
      // Initialize in-memory cache with server data
      initLastMessageTimes(data.last_message_times ?? {});
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

  // One-time migration of localStorage preferences to server
  const hasMigratedRef = useRef(false);
  useEffect(() => {
    // Only run once we have appSettings loaded
    if (!appSettings || hasMigratedRef.current) return;

    // Skip if already migrated on server
    if (appSettings.preferences_migrated) {
      // Just clear any leftover localStorage
      clearLocalStorageFavorites();
      clearLocalStorageConversationState();
      hasMigratedRef.current = true;
      return;
    }

    // Check if we have any localStorage data to migrate
    const localFavorites = loadLocalStorageFavorites();
    const localSortOrder = loadLocalStorageSortOrder();
    const localLastMessageTimes = loadLocalStorageLastMessageTimes();

    const hasLocalData =
      localFavorites.length > 0 ||
      localSortOrder !== 'recent' ||
      Object.keys(localLastMessageTimes).length > 0;

    if (!hasLocalData) {
      // No local data to migrate, just mark as done
      hasMigratedRef.current = true;
      return;
    }

    // Mark as migrating immediately to prevent duplicate calls
    hasMigratedRef.current = true;

    // Migrate localStorage to server
    const migratePreferences = async () => {
      try {
        const result = await api.migratePreferences({
          favorites: localFavorites,
          sort_order: localSortOrder,
          last_message_times: localLastMessageTimes,
        });

        if (result.migrated) {
          toast.success('Preferences migrated', {
            description: `Migrated ${localFavorites.length} favorites to server`,
          });
        }

        // Update local state with migrated settings
        setAppSettings(result.settings);
        // Reinitialize cache with migrated data
        initLastMessageTimes(result.settings.last_message_times ?? {});

        // Clear localStorage after successful migration
        clearLocalStorageFavorites();
        clearLocalStorageConversationState();
      } catch (err) {
        console.error('Failed to migrate preferences:', err);
        // Don't block the app on migration failure
      }
    };

    migratePreferences();
  }, [appSettings]);

  // Resolve URL hash to a conversation
  const resolveHashToConversation = useCallback((): Conversation | null => {
    const hashConv = parseHashConversation();
    if (!hashConv) return null;

    if (hashConv.type === 'raw') {
      return { type: 'raw', id: 'raw', name: 'Raw Packet Feed' };
    }
    if (hashConv.type === 'map') {
      return {
        type: 'map',
        id: 'map',
        name: 'Node Map',
        mapFocusKey: hashConv.mapFocusKey,
      };
    }
    if (hashConv.type === 'visualizer') {
      return { type: 'visualizer', id: 'visualizer', name: 'Mesh Visualizer' };
    }
    if (hashConv.type === 'channel') {
      const channel = channels.find(
        (c) => c.name === hashConv.name || c.name === `#${hashConv.name}`
      );
      if (channel) {
        return { type: 'channel', id: channel.key, name: channel.name };
      }
    }
    if (hashConv.type === 'contact') {
      const contact = contacts.find(
        (c) => getContactDisplayName(c.name, c.public_key) === hashConv.name
      );
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

    const publicChannel = channels.find((c) => c.name === 'Public');
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
  const handleSaveConfig = useCallback(
    async (update: RadioConfigUpdate) => {
      await api.updateRadioConfig(update);
      await fetchConfig();
    },
    [fetchConfig]
  );

  // App settings save handler
  const handleSaveAppSettings = useCallback(
    async (update: AppSettingsUpdate) => {
      await api.updateSettings(update);
      await fetchAppSettings();
    },
    [fetchAppSettings]
  );

  // Set private key handler
  const handleSetPrivateKey = useCallback(
    async (key: string) => {
      await api.setPrivateKey(key);
      await fetchConfig();
    },
    [fetchConfig]
  );

  // Reboot radio handler
  const handleReboot = useCallback(async () => {
    await api.rebootRadio();
    setHealth((prev) => (prev ? { ...prev, radio_connected: false } : prev));
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
      toast.success('Advertisement sent');
    } catch (err) {
      console.error('Failed to send advertisement:', err);
      toast.error('Failed to send advertisement', {
        description: err instanceof Error ? err.message : 'Check radio connection',
      });
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

  // Toggle favorite status for a conversation (via API) with optimistic update
  const handleToggleFavorite = useCallback(async (type: 'channel' | 'contact', id: string) => {
    // Read current favorites inside the callback to avoid a dependency on the
    // derived `favorites` array (which creates a new reference every render).
    setAppSettings((prev) => {
      if (!prev) return prev;
      const currentFavorites = prev.favorites ?? [];
      const wasFavorited = isFavorite(currentFavorites, type, id);
      const optimisticFavorites = wasFavorited
        ? currentFavorites.filter((f) => !(f.type === type && f.id === id))
        : [...currentFavorites, { type, id }];
      return { ...prev, favorites: optimisticFavorites };
    });

    try {
      const updatedSettings = await api.toggleFavorite(type, id);
      setAppSettings(updatedSettings);
    } catch (err) {
      console.error('Failed to toggle favorite:', err);
      // Revert: re-fetch would be safest, but restoring from server state on next sync
      // is acceptable. For now, just refetch settings.
      try {
        const settings = await api.getSettings();
        setAppSettings(settings);
      } catch {
        // If refetch also fails, leave optimistic state
      }
      toast.error('Failed to update favorite');
    }
  }, []);

  // Delete channel handler
  const handleDeleteChannel = useCallback(async (key: string) => {
    if (!confirm('Delete this channel? Message history will be preserved.')) return;
    try {
      await api.deleteChannel(key);
      setChannels((prev) => prev.filter((c) => c.key !== key));
      setActiveConversation(null);
      toast.success('Channel deleted');
    } catch (err) {
      console.error('Failed to delete channel:', err);
      toast.error('Failed to delete channel', {
        description: err instanceof Error ? err.message : undefined,
      });
    }
  }, []);

  // Delete contact handler
  const handleDeleteContact = useCallback(async (publicKey: string) => {
    if (!confirm('Delete this contact? Message history will be preserved.')) return;
    try {
      await api.deleteContact(publicKey);
      setContacts((prev) => prev.filter((c) => c.public_key !== publicKey));
      setActiveConversation(null);
      toast.success('Contact deleted');
    } catch (err) {
      console.error('Failed to delete contact:', err);
      toast.error('Failed to delete contact', {
        description: err instanceof Error ? err.message : undefined,
      });
    }
  }, []);

  // Create contact handler
  const handleCreateContact = useCallback(
    async (name: string, publicKey: string, tryHistorical: boolean) => {
      const created = await api.createContact(publicKey, name || undefined, tryHistorical);
      const data = await api.getContacts();
      setContacts(data);

      setActiveConversation({
        type: 'contact',
        id: created.public_key,
        name: getContactDisplayName(created.name, created.public_key),
      });
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

  // Handle sort order change via API with optimistic update
  const handleSortOrderChange = useCallback(
    async (order: 'recent' | 'alpha') => {
      // Capture previous value for rollback on error
      const previousOrder = appSettings?.sidebar_sort_order ?? 'recent';

      // Optimistic update for responsive UI
      setAppSettings((prev) => (prev ? { ...prev, sidebar_sort_order: order } : prev));

      try {
        const updatedSettings = await api.updateSettings({ sidebar_sort_order: order });
        setAppSettings(updatedSettings);
      } catch (err) {
        console.error('Failed to update sort order:', err);
        // Revert to previous value on error (not inverting the new value)
        setAppSettings((prev) => (prev ? { ...prev, sidebar_sort_order: previousOrder } : prev));
        toast.error('Failed to save sort preference');
      }
    },
    [appSettings?.sidebar_sort_order]
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
      mentions={mentions}
      showCracker={showCracker}
      crackerRunning={crackerRunning}
      onToggleCracker={() => setShowCracker((prev) => !prev)}
      onMarkAllRead={markAllRead}
      favorites={favorites}
      sortOrder={appSettings?.sidebar_sort_order ?? 'recent'}
      onSortOrderChange={handleSortOrderChange}
    />
  );

  return (
    <div className="flex flex-col h-dvh">
      <StatusBar
        health={health}
        config={config}
        onSettingsClick={() => setShowSettings(true)}
        onMenuClick={() => setSidebarOpen(true)}
      />

      <div className="flex flex-1 overflow-hidden">
        {/* Desktop sidebar - hidden on mobile */}
        <div className="hidden md:block">{sidebarContent}</div>

        {/* Mobile sidebar - Sheet that slides in */}
        <Sheet open={sidebarOpen} onOpenChange={setSidebarOpen}>
          <SheetContent side="left" className="w-[280px] p-0 flex flex-col" hideCloseButton>
            <SheetHeader className="sr-only">
              <SheetTitle>Navigation</SheetTitle>
            </SheetHeader>
            <div className="flex-1 overflow-hidden">{sidebarContent}</div>
          </SheetContent>
        </Sheet>

        <div className="flex-1 flex flex-col bg-background min-w-0">
          {activeConversation ? (
            activeConversation.type === 'map' ? (
              <>
                <div className="flex justify-between items-center px-4 py-3 border-b border-border font-medium text-lg">
                  Node Map
                </div>
                <div className="flex-1 overflow-hidden">
                  <MapView contacts={contacts} focusedKey={activeConversation.mapFocusKey} />
                </div>
              </>
            ) : activeConversation.type === 'visualizer' ? (
              <VisualizerView
                packets={rawPackets}
                contacts={contacts}
                config={config}
                onClearPackets={() => setRawPackets([])}
              />
            ) : activeConversation.type === 'raw' ? (
              <>
                <div className="flex justify-between items-center px-4 py-3 border-b border-border font-medium text-lg">
                  Raw Packet Feed
                </div>
                <div className="flex-1 overflow-hidden">
                  <RawPacketList packets={rawPackets} />
                </div>
              </>
            ) : (
              <>
                <div className="flex justify-between items-center px-4 py-3 border-b border-border font-medium text-lg gap-2">
                  <span className="flex flex-wrap items-baseline gap-x-2 min-w-0 flex-1">
                    <span className="flex-shrink-0">
                      {activeConversation.type === 'channel' &&
                      !activeConversation.name.startsWith('#') &&
                      activeConversation.name !== 'Public'
                        ? '#'
                        : ''}
                      {activeConversation.name}
                    </span>
                    <span
                      className="font-normal text-sm text-muted-foreground font-mono truncate cursor-pointer hover:text-primary"
                      onClick={(e) => {
                        e.stopPropagation();
                        navigator.clipboard.writeText(activeConversation.id);
                        toast.success(
                          activeConversation.type === 'channel'
                            ? 'Room key copied!'
                            : 'Contact key copied!'
                        );
                      }}
                      title="Click to copy"
                    >
                      {activeConversation.type === 'channel'
                        ? activeConversation.id.toLowerCase()
                        : activeConversation.id}
                    </span>
                    {activeConversation.type === 'contact' &&
                      (() => {
                        const contact = contacts.find(
                          (c) => c.public_key === activeConversation.id
                        );
                        if (!contact) return null;
                        const parts: React.ReactNode[] = [];
                        if (contact.last_seen) {
                          parts.push(`Last heard: ${formatTime(contact.last_seen)}`);
                        }
                        if (contact.last_path_len === -1) {
                          parts.push('flood');
                        } else if (contact.last_path_len === 0) {
                          parts.push('direct');
                        } else if (contact.last_path_len > 0) {
                          parts.push(
                            `${contact.last_path_len} hop${contact.last_path_len > 1 ? 's' : ''}`
                          );
                        }
                        // Add coordinate link if contact has valid location
                        if (isValidLocation(contact.lat, contact.lon)) {
                          // Calculate distance from us if we have valid location
                          const distFromUs =
                            config && isValidLocation(config.lat, config.lon)
                              ? calculateDistance(config.lat, config.lon, contact.lat, contact.lon)
                              : null;
                          parts.push(
                            <span key="coords">
                              <span
                                className="font-mono cursor-pointer hover:text-primary hover:underline"
                                onClick={(e) => {
                                  e.stopPropagation();
                                  const url =
                                    window.location.origin +
                                    window.location.pathname +
                                    getMapFocusHash(contact.public_key);
                                  window.open(url, '_blank');
                                }}
                                title="View on map"
                              >
                                {contact.lat!.toFixed(3)}, {contact.lon!.toFixed(3)}
                              </span>
                              {distFromUs !== null && ` (${formatDistance(distFromUs)})`}
                            </span>
                          );
                        }
                        return parts.length > 0 ? (
                          <span className="font-normal text-sm text-muted-foreground flex-shrink-0">
                            (
                            {parts.map((part, i) => (
                              <span key={i}>
                                {i > 0 && ', '}
                                {part}
                              </span>
                            ))}
                            )
                          </span>
                        ) : null;
                      })()}
                  </span>
                  <div className="flex items-center gap-1 flex-shrink-0">
                    {/* Favorite button */}
                    {(activeConversation.type === 'channel' ||
                      activeConversation.type === 'contact') && (
                      <button
                        className="p-1.5 rounded hover:bg-accent text-xl leading-none"
                        onClick={() =>
                          handleToggleFavorite(
                            activeConversation.type as 'channel' | 'contact',
                            activeConversation.id
                          )
                        }
                        title={
                          isFavorite(
                            favorites,
                            activeConversation.type as 'channel' | 'contact',
                            activeConversation.id
                          )
                            ? 'Remove from favorites'
                            : 'Add to favorites'
                        }
                      >
                        {isFavorite(
                          favorites,
                          activeConversation.type as 'channel' | 'contact',
                          activeConversation.id
                        ) ? (
                          <span className="text-yellow-500">&#9733;</span>
                        ) : (
                          <span className="text-muted-foreground">&#9734;</span>
                        )}
                      </button>
                    )}
                    {/* Delete button */}
                    {!(
                      activeConversation.type === 'channel' && activeConversation.name === 'Public'
                    ) && (
                      <button
                        className="p-1.5 rounded hover:bg-destructive/20 text-destructive text-xl leading-none"
                        onClick={() => {
                          if (activeConversation.type === 'channel') {
                            handleDeleteChannel(activeConversation.id);
                          } else {
                            handleDeleteContact(activeConversation.id);
                          }
                        }}
                        title="Delete"
                      >
                        &#128465;
                      </button>
                    )}
                  </div>
                </div>
                <MessageList
                  messages={messages}
                  contacts={contacts}
                  loading={messagesLoading}
                  loadingOlder={loadingOlder}
                  hasOlderMessages={hasOlderMessages}
                  onSenderClick={
                    activeConversation.type === 'channel' ? handleSenderClick : undefined
                  }
                  onLoadOlder={fetchOlderMessages}
                  radioName={config?.name}
                  config={config}
                />
                <MessageInput
                  ref={messageInputRef}
                  onSend={
                    activeContactIsRepeater
                      ? repeaterLoggedIn
                        ? handleRepeaterCommand
                        : handleTelemetryRequest
                      : handleSendMessage
                  }
                  disabled={!health?.radio_connected}
                  isRepeaterMode={activeContactIsRepeater && !repeaterLoggedIn}
                  conversationType={activeConversation.type}
                  senderName={config?.name}
                  placeholder={
                    !health?.radio_connected
                      ? 'Radio not connected'
                      : activeContactIsRepeater
                        ? repeaterLoggedIn
                          ? 'Send CLI command (requires admin login)...'
                          : `Enter password for ${activeConversation.name} (or . for none)...`
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
          'border-t border-border bg-background transition-all duration-200 overflow-hidden',
          showCracker ? 'h-[275px]' : 'h-0'
        )}
      >
        <CrackerPanel
          packets={rawPackets}
          channels={channels}
          visible={showCracker}
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

      <SettingsModal
        open={showSettings}
        config={config}
        health={health}
        appSettings={appSettings}
        onClose={() => setShowSettings(false)}
        onSave={handleSaveConfig}
        onSaveAppSettings={handleSaveAppSettings}
        onSetPrivateKey={handleSetPrivateKey}
        onReboot={handleReboot}
        onAdvertise={handleAdvertise}
        onHealthRefresh={async () => {
          const data = await api.getHealth();
          setHealth(data);
        }}
        onRefreshAppSettings={fetchAppSettings}
      />

      <Toaster position="top-right" />
    </div>
  );
}
