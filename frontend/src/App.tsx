import {
  useState,
  useEffect,
  useCallback,
  useMemo,
  useRef,
  startTransition,
  lazy,
  Suspense,
} from 'react';
import { api } from './api';
import { takePrefetch } from './prefetch';
import { useWebSocket } from './useWebSocket';
import {
  useRepeaterMode,
  useUnreadCounts,
  useConversationMessages,
  getMessageContentKey,
} from './hooks';
import * as messageCache from './messageCache';
import { StatusBar } from './components/StatusBar';
import { Sidebar } from './components/Sidebar';
import { MessageList } from './components/MessageList';
import { MessageInput, type MessageInputHandle } from './components/MessageInput';
import { NewMessageModal } from './components/NewMessageModal';
import {
  SETTINGS_SECTION_LABELS,
  SETTINGS_SECTION_ORDER,
  type SettingsSection,
} from './components/settingsConstants';
import { RawPacketList } from './components/RawPacketList';

// Lazy-load heavy components to reduce initial bundle
const MapView = lazy(() => import('./components/MapView').then((m) => ({ default: m.MapView })));
const VisualizerView = lazy(() =>
  import('./components/VisualizerView').then((m) => ({ default: m.VisualizerView }))
);
const SettingsModal = lazy(() =>
  import('./components/SettingsModal').then((m) => ({ default: m.SettingsModal }))
);
const CrackerPanel = lazy(() =>
  import('./components/CrackerPanel').then((m) => ({ default: m.CrackerPanel }))
);
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
import {
  parseHashConversation,
  updateUrlHash,
  getMapFocusHash,
  resolveChannelFromHashToken,
  resolveContactFromHashToken,
} from './utils/urlHash';
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
const PUBLIC_CHANNEL_KEY = '8B3387E9C5CDEA6AC9E5EDBAA115CD72';

export function App() {
  const messageInputRef = useRef<MessageInputHandle>(null);
  const activeConversationRef = useRef<Conversation | null>(null);
  const rebootPollTokenRef = useRef(0);
  const pendingDeleteFallbackRef = useRef(false);
  // Track seen message content to prevent duplicate unread increments
  // Uses content-based key (type-conversation_key-text-sender_timestamp) for deduplication
  const seenMessageContentRef = useRef<Set<string>>(new Set());
  const [health, setHealth] = useState<HealthStatus | null>(null);
  const [config, setConfig] = useState<RadioConfig | null>(null);
  const [appSettings, setAppSettings] = useState<AppSettings | null>(null);
  const [contacts, setContacts] = useState<Contact[]>([]);
  const [contactsLoaded, setContactsLoaded] = useState(false);
  const [channels, setChannels] = useState<Channel[]>([]);
  const [rawPackets, setRawPackets] = useState<RawPacket[]>([]);
  const [activeConversation, setActiveConversation] = useState<Conversation | null>(null);
  const [showNewMessage, setShowNewMessage] = useState(false);
  const [showSettings, setShowSettings] = useState(false);
  const [settingsSection, setSettingsSection] = useState<SettingsSection>('radio');
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const [undecryptedCount, setUndecryptedCount] = useState(0);
  const [showCracker, setShowCracker] = useState(false);
  const [crackerRunning, setCrackerRunning] = useState(false);

  // Defer CrackerPanel mount until first opened (lazy-loaded, but keep mounted after for state)
  const crackerMounted = useRef(false);
  if (showCracker) crackerMounted.current = true;

  // Favorites are now stored server-side in appSettings.
  // Stable empty array prevents a new reference every render when there are none.
  const emptyFavorites = useRef<Favorite[]>([]).current;
  const favorites: Favorite[] = appSettings?.favorites ?? emptyFavorites;

  // Track previous health status to detect changes
  const prevHealthRef = useRef<HealthStatus | null>(null);
  useEffect(() => {
    return () => {
      rebootPollTokenRef.current += 1;
    };
  }, []);

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
  } = useUnreadCounts(channels, contacts, activeConversation);

  const {
    repeaterLoggedIn,
    activeContactIsRepeater,
    handleTelemetryRequest,
    handleRepeaterCommand,
  } = useRepeaterMode(activeConversation, contacts, setMessages, activeConversationRef);

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
              description: data.connection_info
                ? `Connected via ${data.connection_info}`
                : undefined,
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

        const contentKey = getMessageContentKey(msg);

        // Dedup: check if we've already seen this content BEFORE marking it seen.
        // This must happen before the active/non-active branch so that messages
        // first seen in the active conversation are tracked — otherwise a mesh
        // duplicate arriving after the user switches away would create a phantom
        // unread badge.
        const alreadySeen = !msg.outgoing && seenMessageContentRef.current.has(contentKey);
        if (!msg.outgoing) {
          seenMessageContentRef.current.add(contentKey);

          // Limit set size to prevent memory issues
          if (seenMessageContentRef.current.size > 1000) {
            const keys = Array.from(seenMessageContentRef.current);
            seenMessageContentRef.current = new Set(keys.slice(-500));
          }
        }

        // For non-active conversations: update cache and count unreads
        if (!isForActiveConversation) {
          // Update message cache (instant restore on switch)
          messageCache.addMessage(msg.conversation_key, msg, contentKey);

          // Count unread for incoming messages (skip duplicates from multiple mesh paths)
          if (!msg.outgoing && !alreadySeen) {
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
        }
      },
      onContact: (contact: Contact) => {
        setContacts((prev) => {
          const idx = prev.findIndex((c) => c.public_key === contact.public_key);
          if (idx >= 0) {
            const existing = prev[idx];
            // Skip update if all incoming fields are identical — avoids a new
            // array reference (and Sidebar re-render) on every advertisement.
            const merged = { ...existing, ...contact };
            const unchanged = (Object.keys(merged) as (keyof Contact)[]).every(
              (k) => existing[k] === merged[k]
            );
            if (unchanged) return prev;
            const updated = [...prev];
            updated[idx] = merged;
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
        messageCache.updateAck(messageId, ackCount, paths);
      },
    }),
    [addMessageIfNew, trackNewMessage, incrementUnread, updateMessageAck, checkMention]
  );

  // Connect to WebSocket
  useWebSocket(wsHandlers);

  // Fetch radio config (not sent via WebSocket)
  const fetchConfig = useCallback(async () => {
    try {
      const data = await (takePrefetch('config') ?? api.getRadioConfig());
      setConfig(data);
    } catch (err) {
      console.error('Failed to fetch config:', err);
    }
  }, []);

  // Fetch app settings
  const fetchAppSettings = useCallback(async () => {
    try {
      const data = await (takePrefetch('settings') ?? api.getSettings());
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
      const data = await (takePrefetch('undecryptedCount') ?? api.getUndecryptedPacketCount());
      setUndecryptedCount(data.count);
    } catch (err) {
      console.error('Failed to fetch undecrypted count:', err);
    }
  }, []);

  // Fetch all contacts, paginating if >1000
  const fetchAllContacts = useCallback(async (): Promise<Contact[]> => {
    const pageSize = 1000;
    const first = await (takePrefetch('contacts') ?? api.getContacts(pageSize, 0));
    if (first.length < pageSize) return first;
    let all = [...first];
    let offset = pageSize;
    while (true) {
      const page = await api.getContacts(pageSize, offset);
      all = all.concat(page);
      if (page.length < pageSize) break;
      offset += pageSize;
    }
    return all;
  }, []);

  // Initial fetch for config, settings, and data
  useEffect(() => {
    fetchConfig();
    fetchAppSettings();
    fetchUndecryptedCount();

    // Fetch contacts and channels via REST (parallel, faster than WS serial push)
    (takePrefetch('channels') ?? api.getChannels()).then(setChannels).catch(console.error);
    fetchAllContacts()
      .then((data) => {
        setContacts(data);
        setContactsLoaded(true);
      })
      .catch((err) => {
        console.error(err);
        setContactsLoaded(true);
      });
  }, [fetchConfig, fetchAppSettings, fetchUndecryptedCount, fetchAllContacts]);

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

  // Phase 1: Set initial conversation from URL hash or default to Public channel
  // Only needs channels (fast path) - doesn't wait for contacts
  const hasSetDefaultConversation = useRef(false);
  useEffect(() => {
    if (hasSetDefaultConversation.current || activeConversation) return;
    if (channels.length === 0) return;

    const hashConv = parseHashConversation();

    // Handle non-data views immediately
    if (hashConv?.type === 'raw') {
      setActiveConversation({ type: 'raw', id: 'raw', name: 'Raw Packet Feed' });
      hasSetDefaultConversation.current = true;
      return;
    }
    if (hashConv?.type === 'map') {
      setActiveConversation({
        type: 'map',
        id: 'map',
        name: 'Node Map',
        mapFocusKey: hashConv.mapFocusKey,
      });
      hasSetDefaultConversation.current = true;
      return;
    }
    if (hashConv?.type === 'visualizer') {
      setActiveConversation({ type: 'visualizer', id: 'visualizer', name: 'Mesh Visualizer' });
      hasSetDefaultConversation.current = true;
      return;
    }

    // Handle channel hash (ID-first with legacy-name fallback)
    if (hashConv?.type === 'channel') {
      const channel = resolveChannelFromHashToken(hashConv.name, channels);
      if (channel) {
        setActiveConversation({ type: 'channel', id: channel.key, name: channel.name });
        hasSetDefaultConversation.current = true;
        return;
      }
    }

    // Contact hash — wait for phase 2
    if (hashConv?.type === 'contact') return;

    // No hash or unresolvable — default to Public
    const publicChannel = channels.find((c) => c.name === 'Public');
    if (publicChannel) {
      setActiveConversation({
        type: 'channel',
        id: publicChannel.key,
        name: publicChannel.name,
      });
      hasSetDefaultConversation.current = true;
    }
  }, [channels, activeConversation]);

  // Phase 2: Resolve contact hash (only if phase 1 didn't set a conversation)
  useEffect(() => {
    if (hasSetDefaultConversation.current || activeConversation) return;

    const hashConv = parseHashConversation();
    if (hashConv?.type === 'contact') {
      // Wait until the initial contacts load finishes so we don't fall back early.
      if (!contactsLoaded) return;

      const contact = resolveContactFromHashToken(hashConv.name, contacts);
      if (contact) {
        setActiveConversation({
          type: 'contact',
          id: contact.public_key,
          name: getContactDisplayName(contact.name, contact.public_key),
        });
        hasSetDefaultConversation.current = true;
        return;
      }

      // Contact hash didn't match — fall back to Public if channels loaded.
      if (channels.length > 0) {
        const publicChannel = channels.find((c) => c.name === 'Public');
        if (publicChannel) {
          setActiveConversation({
            type: 'channel',
            id: publicChannel.key,
            name: publicChannel.name,
          });
          hasSetDefaultConversation.current = true;
        }
      }
    }
  }, [contacts, channels, activeConversation, contactsLoaded]);

  // Keep ref in sync and update URL hash
  useEffect(() => {
    activeConversationRef.current = activeConversation;
    if (activeConversation) {
      updateUrlHash(activeConversation);
    }
  }, [activeConversation]);

  // If a delete action left us without an active conversation, recover to Public
  // once channels are available. This is scoped to delete flows only so it doesn't
  // interfere with hash-based startup resolution.
  useEffect(() => {
    if (!pendingDeleteFallbackRef.current) return;
    if (activeConversation) {
      pendingDeleteFallbackRef.current = false;
      return;
    }

    const publicChannel =
      channels.find((c) => c.key === PUBLIC_CHANNEL_KEY) ||
      channels.find((c) => c.name === 'Public');
    if (!publicChannel) return;

    hasSetDefaultConversation.current = true;
    pendingDeleteFallbackRef.current = false;
    setActiveConversation({
      type: 'channel',
      id: publicChannel.key,
      name: publicChannel.name,
    });
  }, [activeConversation, channels]);

  // Send message handler
  const handleSendMessage = useCallback(
    async (text: string) => {
      if (!activeConversation) return;

      // Capture conversation identity before the await — the user could switch
      // conversations while the send is in flight.
      const conversationId = activeConversation.id;

      let sent: Message;
      if (activeConversation.type === 'channel') {
        sent = await api.sendChannelMessage(activeConversation.id, text);
      } else {
        sent = await api.sendDirectMessage(activeConversation.id, text);
      }

      // Add the returned message directly instead of re-fetching all messages.
      // A full fetchMessages() replaces the array, which resets messagesAdded to 0
      // and skips the scroll-to-bottom — especially when racing with the initial
      // conversation load or a WebSocket delivery that already incremented the count.
      // The send endpoints do NOT broadcast via WebSocket, so for DMs this is the
      // only path that shows the sent message. For channels, the radio echo may
      // eventually arrive via WS, but addMessageIfNew deduplicates by content key.
      if (activeConversationRef.current?.id === conversationId) {
        addMessageIfNew(sent);
      }
    },
    [activeConversation, addMessageIfNew]
  );

  const handleResendMessage = useCallback(async (messageId: number) => {
    try {
      await api.resendMessage(messageId);
      toast.success('Message sent again');
    } catch (err) {
      console.error('Failed to resend message:', err);
      toast.error('Failed to resend message', {
        description: err instanceof Error ? err.message : 'Check radio connection',
      });
    }
  }, []);

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
    const pollToken = ++rebootPollTokenRef.current;
    const pollUntilReconnected = async () => {
      for (let i = 0; i < 30; i++) {
        await new Promise((r) => setTimeout(r, 1000));
        if (rebootPollTokenRef.current !== pollToken) return;
        try {
          const data = await api.getHealth();
          if (rebootPollTokenRef.current !== pollToken) return;
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
      await api.sendAdvertisement();
      toast.success('Advertisement sent');
    } catch (err) {
      console.error('Failed to send advertisement:', err);
      toast.error('Failed to send advertisement', {
        description: err instanceof Error ? err.message : 'Check radio connection',
      });
    }
  }, []);

  const handleHealthRefresh = useCallback(async () => {
    try {
      const data = await api.getHealth();
      setHealth(data);
    } catch (err) {
      console.error('Failed to refresh health:', err);
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
      pendingDeleteFallbackRef.current = true;
      await api.deleteChannel(key);
      messageCache.remove(key);
      const refreshedChannels = await api.getChannels();
      setChannels(refreshedChannels);
      const publicChannel =
        refreshedChannels.find((c) => c.key === PUBLIC_CHANNEL_KEY) ||
        refreshedChannels.find((c) => c.name === 'Public');
      hasSetDefaultConversation.current = true;
      setActiveConversation({
        type: 'channel',
        id: publicChannel?.key || PUBLIC_CHANNEL_KEY,
        name: publicChannel?.name || 'Public',
      });
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
      pendingDeleteFallbackRef.current = true;
      await api.deleteContact(publicKey);
      messageCache.remove(publicKey);
      setContacts((prev) => prev.filter((c) => c.public_key !== publicKey));
      const refreshedChannels = await api.getChannels();
      setChannels(refreshedChannels);
      const publicChannel =
        refreshedChannels.find((c) => c.key === PUBLIC_CHANNEL_KEY) ||
        refreshedChannels.find((c) => c.name === 'Public');
      hasSetDefaultConversation.current = true;
      setActiveConversation({
        type: 'channel',
        id: publicChannel?.key || PUBLIC_CHANNEL_KEY,
        name: publicChannel?.name || 'Public',
      });
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
      const data = await fetchAllContacts();
      setContacts(data);

      setActiveConversation({
        type: 'contact',
        id: created.public_key,
        name: getContactDisplayName(created.name, created.public_key),
      });
    },
    [fetchAllContacts]
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

  // Handle direct trace request
  const handleTrace = useCallback(async () => {
    if (!activeConversation || activeConversation.type !== 'contact') return;
    toast('Trace started...');
    try {
      const result = await api.requestTrace(activeConversation.id);
      const parts: string[] = [];
      if (result.remote_snr !== null) parts.push(`Remote SNR: ${result.remote_snr.toFixed(1)} dB`);
      if (result.local_snr !== null) parts.push(`Local SNR: ${result.local_snr.toFixed(1)} dB`);
      const detail = parts.join(', ');
      toast.success(detail ? `Trace complete! ${detail}` : 'Trace complete!');
    } catch (err) {
      toast.error('Trace failed', {
        description: err instanceof Error ? err.message : 'Unknown error',
      });
    }
  }, [activeConversation]);

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

  const handleCloseSettingsView = useCallback(() => {
    startTransition(() => setShowSettings(false));
    setSidebarOpen(false);
  }, []);

  const handleToggleSettingsView = useCallback(() => {
    startTransition(() => {
      setShowSettings((prev) => !prev);
    });
    setSidebarOpen(false);
  }, []);

  const handleNewMessage = useCallback(() => {
    setShowNewMessage(true);
    setSidebarOpen(false);
  }, []);

  const handleToggleCracker = useCallback(() => {
    setShowCracker((prev) => !prev);
  }, []);

  // Sidebar content (shared between desktop and mobile)
  const sidebarContent = (
    <Sidebar
      contacts={contacts}
      channels={channels}
      activeConversation={activeConversation}
      onSelectConversation={handleSelectConversation}
      onNewMessage={handleNewMessage}
      lastMessageTimes={lastMessageTimes}
      unreadCounts={unreadCounts}
      mentions={mentions}
      showCracker={showCracker}
      crackerRunning={crackerRunning}
      onToggleCracker={handleToggleCracker}
      onMarkAllRead={markAllRead}
      favorites={favorites}
      sortOrder={appSettings?.sidebar_sort_order ?? 'recent'}
      onSortOrderChange={handleSortOrderChange}
    />
  );

  const settingsSidebarContent = (
    <div className="sidebar w-60 h-full min-h-0 bg-card border-r border-border flex flex-col">
      <div className="flex justify-between items-center px-3 py-3 border-b border-border">
        <h2 className="text-xs uppercase text-muted-foreground font-medium">Settings</h2>
        <button
          type="button"
          onClick={handleCloseSettingsView}
          className="h-6 w-6 rounded text-muted-foreground hover:text-foreground hover:bg-accent"
          title="Back to conversations"
          aria-label="Back to conversations"
        >
          ←
        </button>
      </div>
      <div className="flex-1 overflow-y-auto py-1">
        {SETTINGS_SECTION_ORDER.map((section) => (
          <button
            key={section}
            type="button"
            className={cn(
              'w-full px-3 py-2.5 text-left border-l-2 border-transparent hover:bg-accent',
              settingsSection === section && 'bg-accent border-l-primary'
            )}
            onClick={() => setSettingsSection(section)}
          >
            {SETTINGS_SECTION_LABELS[section]}
          </button>
        ))}
      </div>
    </div>
  );

  const activeSidebarContent = showSettings ? settingsSidebarContent : sidebarContent;

  return (
    <div className="flex flex-col h-full">
      <StatusBar
        health={health}
        config={config}
        settingsMode={showSettings}
        onSettingsClick={handleToggleSettingsView}
        onMenuClick={showSettings ? undefined : () => setSidebarOpen(true)}
      />

      <div className="flex flex-1 overflow-hidden">
        {/* Desktop sidebar - hidden on mobile */}
        <div className="hidden md:block">{activeSidebarContent}</div>

        {/* Mobile sidebar - Sheet that slides in */}
        <Sheet open={sidebarOpen} onOpenChange={setSidebarOpen}>
          <SheetContent side="left" className="w-[280px] p-0 flex flex-col" hideCloseButton>
            <SheetHeader className="sr-only">
              <SheetTitle>Navigation</SheetTitle>
            </SheetHeader>
            <div className="flex-1 overflow-hidden">{activeSidebarContent}</div>
          </SheetContent>
        </Sheet>

        <main className="flex-1 flex flex-col bg-background min-w-0">
          <div className={cn('flex-1 flex flex-col min-h-0', showSettings && 'hidden')}>
            {activeConversation ? (
              activeConversation.type === 'map' ? (
                <>
                  <div className="flex justify-between items-center px-4 py-3 border-b border-border font-medium text-lg">
                    Node Map
                  </div>
                  <div className="flex-1 overflow-hidden">
                    <Suspense
                      fallback={
                        <div className="flex-1 flex items-center justify-center text-muted-foreground">
                          Loading map...
                        </div>
                      }
                    >
                      <MapView contacts={contacts} focusedKey={activeConversation.mapFocusKey} />
                    </Suspense>
                  </div>
                </>
              ) : activeConversation.type === 'visualizer' ? (
                <Suspense
                  fallback={
                    <div className="flex-1 flex items-center justify-center text-muted-foreground">
                      Loading visualizer...
                    </div>
                  }
                >
                  <VisualizerView
                    packets={rawPackets}
                    contacts={contacts}
                    config={config}
                    onClearPackets={() => setRawPackets([])}
                  />
                </Suspense>
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
                                ? calculateDistance(
                                    config.lat,
                                    config.lon,
                                    contact.lat,
                                    contact.lon
                                  )
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
                      {/* Direct trace button (contacts only) */}
                      {activeConversation.type === 'contact' && (
                        <button
                          className="p-1.5 rounded hover:bg-accent text-xl leading-none"
                          onClick={handleTrace}
                          title="Direct Trace"
                        >
                          &#x1F6CE;
                        </button>
                      )}
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
                        activeConversation.type === 'channel' &&
                        activeConversation.name === 'Public'
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
                    key={activeConversation.id}
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
                    onResendMessage={handleResendMessage}
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

          {showSettings && (
            <div className="flex-1 flex flex-col min-h-0">
              <div className="flex justify-between items-center px-4 py-3 border-b border-border font-medium text-lg">
                <span>Radio & Settings</span>
                <span className="text-sm text-muted-foreground hidden md:inline">
                  {SETTINGS_SECTION_LABELS[settingsSection]}
                </span>
              </div>
              <div className="flex-1 min-h-0 overflow-hidden">
                <Suspense
                  fallback={
                    <div className="flex-1 flex items-center justify-center p-8 text-muted-foreground">
                      Loading settings...
                    </div>
                  }
                >
                  <SettingsModal
                    open={showSettings}
                    pageMode
                    externalSidebarNav
                    desktopSection={settingsSection}
                    config={config}
                    health={health}
                    appSettings={appSettings}
                    onClose={handleCloseSettingsView}
                    onSave={handleSaveConfig}
                    onSaveAppSettings={handleSaveAppSettings}
                    onSetPrivateKey={handleSetPrivateKey}
                    onReboot={handleReboot}
                    onAdvertise={handleAdvertise}
                    onHealthRefresh={handleHealthRefresh}
                    onRefreshAppSettings={fetchAppSettings}
                  />
                </Suspense>
              </div>
            </div>
          )}
        </main>
      </div>

      {/* Global Cracker Panel - deferred until first opened, then kept mounted for state */}
      <div
        className={cn(
          'border-t border-border bg-background transition-all duration-200 overflow-hidden',
          showCracker ? 'h-[275px]' : 'h-0'
        )}
      >
        {crackerMounted.current && (
          <Suspense
            fallback={
              <div className="flex items-center justify-center h-full text-muted-foreground">
                Loading cracker...
              </div>
            }
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
          </Suspense>
        )}
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

      <Toaster position="top-right" />
    </div>
  );
}
