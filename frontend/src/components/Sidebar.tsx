import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  Bell,
  BellOff,
  Bot,
  Cable,
  ChartNetwork,
  CheckCheck,
  ChevronDown,
  ChevronRight,
  LockOpen,
  Logs,
  Map,
  Search as SearchIcon,
  SquarePen,
  X,
} from 'lucide-react';
import {
  CONTACT_TYPE_ROOM,
  CONTACT_TYPE_REPEATER,
  type Contact,
  type Channel,
  type Conversation,
} from '../types';
import {
  buildSidebarSectionSortOrders,
  FAVORITES_SORT_CYCLE,
  getStateKey,
  loadLegacyLocalStorageSortOrder,
  loadLocalStorageSidebarSectionSortOrders,
  saveLocalStorageSidebarSectionSortOrders,
  type ConversationTimes,
  type SidebarSectionSortOrders,
  type SidebarSortableSection,
  type SortOrder,
} from '../utils/conversationState';
import { isPublicChannelKey } from '../utils/publicChannel';
import { getContactDisplayName } from '../utils/pubkey';
import { handleKeyboardActivate } from '../utils/a11y';
import { ContactAvatar } from './ContactAvatar';
import { Input } from './ui/input';
import { Button } from './ui/button';
import { cn } from '@/lib/utils';

type FavoriteItem = { type: 'channel'; channel: Channel } | { type: 'contact'; contact: Contact };

// Grouping order for the Favorites "by type" sorts. Mirrors the standalone sidebar
// section order: Channels, Contacts (clients/sensors/unknown), Rooms, Repeaters.
function favoriteTypeRank(item: FavoriteItem): number {
  if (item.type === 'channel') return 0;
  switch (item.contact.type) {
    case CONTACT_TYPE_ROOM:
      return 2;
    case CONTACT_TYPE_REPEATER:
      return 3;
    default:
      return 1;
  }
}

// The next order when the section's sort toggle is clicked. Favorites cycles
// through all four orders; every other (single-type) section flips recent<->alpha.
function nextSortOrder(section: SidebarSortableSection, current: SortOrder): SortOrder {
  if (section === 'favorites') {
    const idx = FAVORITES_SORT_CYCLE.indexOf(current);
    return FAVORITES_SORT_CYCLE[(idx + 1) % FAVORITES_SORT_CYCLE.length];
  }
  return current === 'alpha' ? 'recent' : 'alpha';
}

// Compact glyph/text shown on the toggle for the current order.
function sortOrderLabel(order: SortOrder): string {
  switch (order) {
    case 'alpha':
      return 'A-Z';
    case 'type-recent':
      return 'Type ⏱';
    case 'type-alpha':
      return 'Type A-Z';
    case 'recent':
    default:
      return '⏱';
  }
}

// Human phrase for aria/title, describing what the order sorts by.
function sortOrderDescription(order: SortOrder): string {
  switch (order) {
    case 'alpha':
      return 'alphabetically';
    case 'type-recent':
      return 'by type, then recent';
    case 'type-alpha':
      return 'by type, then alphabetically';
    case 'recent':
    default:
      return 'by recent';
  }
}

type ConversationRow = {
  key: string;
  type: 'channel' | 'contact';
  id: string;
  name: string;
  unreadCount: number;
  isMention: boolean;
  notificationsEnabled: boolean;
  muted?: boolean;
  contact?: Contact;
};

type CollapseState = {
  tools: boolean;
  favorites: boolean;
  channels: boolean;
  contacts: boolean;
  rooms: boolean;
  repeaters: boolean;
};

const SIDEBAR_COLLAPSE_STATE_KEY = 'remoteterm-sidebar-collapse-state';

const DEFAULT_COLLAPSE_STATE: CollapseState = {
  tools: false,
  favorites: false,
  channels: false,
  contacts: false,
  rooms: false,
  repeaters: false,
};

function loadCollapsedState(): CollapseState {
  try {
    const raw = localStorage.getItem(SIDEBAR_COLLAPSE_STATE_KEY);
    if (!raw) return DEFAULT_COLLAPSE_STATE;
    const parsed = JSON.parse(raw) as Partial<CollapseState>;
    return {
      tools: parsed.tools ?? DEFAULT_COLLAPSE_STATE.tools,
      favorites: parsed.favorites ?? DEFAULT_COLLAPSE_STATE.favorites,
      channels: parsed.channels ?? DEFAULT_COLLAPSE_STATE.channels,
      contacts: parsed.contacts ?? DEFAULT_COLLAPSE_STATE.contacts,
      rooms: parsed.rooms ?? DEFAULT_COLLAPSE_STATE.rooms,
      repeaters: parsed.repeaters ?? DEFAULT_COLLAPSE_STATE.repeaters,
    };
  } catch {
    return DEFAULT_COLLAPSE_STATE;
  }
}

interface SidebarProps {
  contacts: Contact[];
  channels: Channel[];
  activeConversation: Conversation | null;
  onSelectConversation: (conversation: Conversation) => void;
  onNewMessage: (event?: React.MouseEvent<HTMLButtonElement>) => void;
  lastMessageTimes: ConversationTimes;
  unreadCounts: Record<string, number>;
  /** Tracks which conversations have unread messages that mention the user */
  mentions: Record<string, boolean>;
  showCracker: boolean;
  crackerRunning: boolean;
  onToggleCracker: () => void;
  onMarkAllRead: () => void;
  isConversationNotificationsEnabled?: (type: 'channel' | 'contact', id: string) => boolean;
  blockedKeys?: string[];
  blockedNames?: string[];
}

function loadInitialSectionSortOrders(): SidebarSectionSortOrders {
  const storedOrders = loadLocalStorageSidebarSectionSortOrders();
  if (storedOrders) return storedOrders;

  const legacyOrder = loadLegacyLocalStorageSortOrder();
  const orders = buildSidebarSectionSortOrders(legacyOrder ?? undefined);
  saveLocalStorageSidebarSectionSortOrders(orders);
  return orders;
}

export function Sidebar({
  contacts,
  channels,
  activeConversation,
  onSelectConversation,
  onNewMessage,
  lastMessageTimes,
  unreadCounts,
  mentions,
  showCracker,
  crackerRunning,
  onToggleCracker,
  onMarkAllRead,
  isConversationNotificationsEnabled,
  blockedKeys = [],
  blockedNames = [],
}: SidebarProps) {
  const isContactBlocked = useCallback(
    (c: Contact) =>
      blockedKeys.includes(c.public_key.toLowerCase()) ||
      (c.name != null && blockedNames.includes(c.name)),
    [blockedKeys, blockedNames]
  );

  const [searchQuery, setSearchQuery] = useState('');
  const initialSectionSortOrders = useMemo(loadInitialSectionSortOrders, []);
  const [sectionSortOrders, setSectionSortOrders] = useState(initialSectionSortOrders);
  const initialCollapsedState = useMemo(loadCollapsedState, []);
  const [toolsCollapsed, setToolsCollapsed] = useState(initialCollapsedState.tools);
  const [favoritesCollapsed, setFavoritesCollapsed] = useState(initialCollapsedState.favorites);
  const [channelsCollapsed, setChannelsCollapsed] = useState(initialCollapsedState.channels);
  const [contactsCollapsed, setContactsCollapsed] = useState(initialCollapsedState.contacts);
  const [roomsCollapsed, setRoomsCollapsed] = useState(initialCollapsedState.rooms);
  const [repeatersCollapsed, setRepeatersCollapsed] = useState(initialCollapsedState.repeaters);
  const collapseSnapshotRef = useRef<CollapseState | null>(null);

  const handleSortToggle = (section: SidebarSortableSection) => {
    setSectionSortOrders((prev) => {
      const nextOrder = nextSortOrder(section, prev[section]);
      const updated = { ...prev, [section]: nextOrder };
      saveLocalStorageSidebarSectionSortOrders(updated);
      return updated;
    });
  };

  const handleSelectConversation = (conversation: Conversation) => {
    setSearchQuery('');
    onSelectConversation(conversation);
  };

  const isActive = (
    type: 'contact' | 'channel' | 'raw' | 'map' | 'visualizer' | 'search' | 'trace' | 'bots',
    id: string
  ) => activeConversation?.type === type && activeConversation?.id === id;

  // Get unread count for a conversation
  const getUnreadCount = (type: 'channel' | 'contact', id: string): number => {
    const key = getStateKey(type, id);
    return unreadCounts[key] || 0;
  };

  // Check if a conversation has a mention
  const hasMention = (type: 'channel' | 'contact', id: string): boolean => {
    const key = getStateKey(type, id);
    return mentions[key] || false;
  };

  const getLastMessageTime = useCallback(
    (type: 'channel' | 'contact', id: string) => {
      const key = getStateKey(type, id);
      return lastMessageTimes[key] || 0;
    },
    [lastMessageTimes]
  );

  const getContactHeardTime = useCallback((contact: Contact): number => {
    // Prefer last_seen (server receive wall clock — the value the UI shows as
    // "Last heard") so the recency sort matches the displayed date. Fall back to
    // last_advert only for repeaters known purely from radio sync, which have no
    // independent last_seen. Using Math.max here let a radio-reported (sender
    // clock, skew-prone) last_advert pin a repeater to the top even when its
    // displayed last_seen was older. See ContactStatusInfo "Last heard".
    return contact.last_seen || contact.last_advert || 0;
  }, []);

  const getContactRecentTime = useCallback(
    (contact: Contact): number => {
      if (contact.type === CONTACT_TYPE_REPEATER) {
        return getContactHeardTime(contact);
      }
      return getLastMessageTime('contact', contact.public_key) || getContactHeardTime(contact);
    },
    [getContactHeardTime, getLastMessageTime]
  );

  // Deduplicate channels by key only.
  // Channel names are not unique; distinct keys must remain visible.
  const uniqueChannels = useMemo(
    () =>
      channels.reduce<Channel[]>((acc, channel) => {
        if (!acc.some((c) => c.key === channel.key)) {
          acc.push(channel);
        }
        return acc;
      }, []),
    [channels]
  );

  // Deduplicate contacts by public key, preferring ones with names
  // Also filter out any contacts with empty public keys
  const uniqueContacts = useMemo(
    () =>
      contacts
        .filter((c) => c.public_key && c.public_key.length > 0)
        .sort((a, b) => {
          // Sort contacts with names first
          if (a.name && !b.name) return -1;
          if (!a.name && b.name) return 1;
          return (a.name || '').localeCompare(b.name || '');
        })
        .reduce<Contact[]>((acc, contact) => {
          if (!acc.some((c) => c.public_key === contact.public_key)) {
            acc.push(contact);
          }
          return acc;
        }, []),
    [contacts]
  );

  // Sort channels based on sort order, with Public always first
  const sortedChannels = useMemo(
    () =>
      [...uniqueChannels].sort((a, b) => {
        // Public channel always sorts to the top
        if (isPublicChannelKey(a.key)) return -1;
        if (isPublicChannelKey(b.key)) return 1;

        // Muted channels always sort to the bottom
        if (a.muted && !b.muted) return 1;
        if (!a.muted && b.muted) return -1;

        if (sectionSortOrders.channels === 'recent') {
          const timeA = getLastMessageTime('channel', a.key);
          const timeB = getLastMessageTime('channel', b.key);
          if (timeA && timeB) return timeB - timeA;
          if (timeA && !timeB) return -1;
          if (!timeA && timeB) return 1;
        }
        return a.name.localeCompare(b.name);
      }),
    [uniqueChannels, sectionSortOrders.channels, getLastMessageTime]
  );

  const sortContactsByOrder = useCallback(
    (items: Contact[], order: SortOrder) =>
      [...items].sort((a, b) => {
        // Unread DM contacts always float to the top
        const unreadA = unreadCounts[getStateKey('contact', a.public_key)] || 0;
        const unreadB = unreadCounts[getStateKey('contact', b.public_key)] || 0;
        if (unreadA > 0 && unreadB === 0) return -1;
        if (unreadA === 0 && unreadB > 0) return 1;

        if (order === 'recent') {
          const timeA = getContactRecentTime(a);
          const timeB = getContactRecentTime(b);
          if (timeA && timeB) return timeB - timeA;
          if (timeA && !timeB) return -1;
          if (!timeA && timeB) return 1;
        }
        return (a.name || a.public_key).localeCompare(b.name || b.public_key);
      }),
    [getContactRecentTime, unreadCounts]
  );

  const sortRepeatersByOrder = useCallback(
    (items: Contact[], order: SortOrder) =>
      [...items].sort((a, b) => {
        if (order === 'recent') {
          const timeA = getContactHeardTime(a);
          const timeB = getContactHeardTime(b);
          if (timeA && timeB) return timeB - timeA;
          if (timeA && !timeB) return -1;
          if (!timeA && timeB) return 1;
        }
        return (a.name || a.public_key).localeCompare(b.name || b.public_key);
      }),
    [getContactHeardTime]
  );

  const getFavoriteItemName = useCallback(
    (item: FavoriteItem) =>
      item.type === 'channel'
        ? item.channel.name
        : getContactDisplayName(
            item.contact.name,
            item.contact.public_key,
            item.contact.last_advert
          ),
    []
  );

  const sortFavoriteItemsByOrder = useCallback(
    (items: FavoriteItem[], order: SortOrder) => {
      const typeGrouped = order === 'type-recent' || order === 'type-alpha';
      const byRecent = order === 'recent' || order === 'type-recent';
      return [...items].sort((a, b) => {
        if (typeGrouped) {
          const rankDiff = favoriteTypeRank(a) - favoriteTypeRank(b);
          if (rankDiff !== 0) return rankDiff;
        }

        if (byRecent) {
          const timeA =
            a.type === 'channel'
              ? getLastMessageTime('channel', a.channel.key)
              : getContactRecentTime(a.contact);
          const timeB =
            b.type === 'channel'
              ? getLastMessageTime('channel', b.channel.key)
              : getContactRecentTime(b.contact);
          if (timeA && timeB) return timeB - timeA;
          if (timeA && !timeB) return -1;
          if (!timeA && timeB) return 1;
        }

        return getFavoriteItemName(a).localeCompare(getFavoriteItemName(b));
      });
    },
    [getContactRecentTime, getFavoriteItemName, getLastMessageTime]
  );

  // Split non-repeater contacts and repeater contacts into separate sorted lists
  const sortedNonRepeaterContacts = useMemo(
    () =>
      sortContactsByOrder(
        uniqueContacts.filter(
          (c) => c.type !== CONTACT_TYPE_REPEATER && c.type !== CONTACT_TYPE_ROOM
        ),
        sectionSortOrders.contacts
      ),
    [uniqueContacts, sectionSortOrders.contacts, sortContactsByOrder]
  );

  const sortedRooms = useMemo(
    () =>
      sortContactsByOrder(
        uniqueContacts.filter((c) => c.type === CONTACT_TYPE_ROOM),
        sectionSortOrders.rooms
      ),
    [uniqueContacts, sectionSortOrders.rooms, sortContactsByOrder]
  );

  const sortedRepeaters = useMemo(
    () =>
      sortRepeatersByOrder(
        uniqueContacts.filter((c) => c.type === CONTACT_TYPE_REPEATER),
        sectionSortOrders.repeaters
      ),
    [uniqueContacts, sectionSortOrders.repeaters, sortRepeatersByOrder]
  );

  // Filter by search query
  const query = searchQuery.toLowerCase().trim();
  const isSearching = query.length > 0;

  const filteredChannels = useMemo(
    () =>
      query
        ? sortedChannels.filter(
            (c) => c.name.toLowerCase().includes(query) || c.key.toLowerCase().startsWith(query)
          )
        : sortedChannels,
    [sortedChannels, query]
  );

  const filteredNonRepeaterContacts = useMemo(() => {
    const visible = sortedNonRepeaterContacts.filter((c) => !isContactBlocked(c));
    return query
      ? visible.filter(
          (c) =>
            c.name?.toLowerCase().includes(query) || c.public_key.toLowerCase().startsWith(query)
        )
      : visible;
  }, [sortedNonRepeaterContacts, query, isContactBlocked]);

  const filteredRooms = useMemo(() => {
    const visible = sortedRooms.filter((c) => !isContactBlocked(c));
    return query
      ? visible.filter(
          (c) =>
            c.name?.toLowerCase().includes(query) || c.public_key.toLowerCase().startsWith(query)
        )
      : visible;
  }, [sortedRooms, query, isContactBlocked]);

  const filteredRepeaters = useMemo(() => {
    const visible = sortedRepeaters.filter((c) => !isContactBlocked(c));
    return query
      ? visible.filter(
          (c) =>
            c.name?.toLowerCase().includes(query) || c.public_key.toLowerCase().startsWith(query)
        )
      : visible;
  }, [sortedRepeaters, query, isContactBlocked]);

  // Expand sections while searching; restore prior collapse state when search ends.
  useEffect(() => {
    if (isSearching) {
      if (!collapseSnapshotRef.current) {
        collapseSnapshotRef.current = {
          tools: toolsCollapsed,
          favorites: favoritesCollapsed,
          channels: channelsCollapsed,
          contacts: contactsCollapsed,
          rooms: roomsCollapsed,
          repeaters: repeatersCollapsed,
        };
      }

      if (
        toolsCollapsed ||
        favoritesCollapsed ||
        channelsCollapsed ||
        contactsCollapsed ||
        roomsCollapsed ||
        repeatersCollapsed
      ) {
        setToolsCollapsed(false);
        setFavoritesCollapsed(false);
        setChannelsCollapsed(false);
        setContactsCollapsed(false);
        setRoomsCollapsed(false);
        setRepeatersCollapsed(false);
      }
      return;
    }

    if (collapseSnapshotRef.current) {
      const prev = collapseSnapshotRef.current;
      collapseSnapshotRef.current = null;
      setToolsCollapsed(prev.tools);
      setFavoritesCollapsed(prev.favorites);
      setChannelsCollapsed(prev.channels);
      setContactsCollapsed(prev.contacts);
      setRoomsCollapsed(prev.rooms);
      setRepeatersCollapsed(prev.repeaters);
    }
  }, [
    isSearching,
    toolsCollapsed,
    favoritesCollapsed,
    channelsCollapsed,
    contactsCollapsed,
    roomsCollapsed,
    repeatersCollapsed,
  ]);

  useEffect(() => {
    if (isSearching) return;

    const state: CollapseState = {
      tools: toolsCollapsed,
      favorites: favoritesCollapsed,
      channels: channelsCollapsed,
      contacts: contactsCollapsed,
      rooms: roomsCollapsed,
      repeaters: repeatersCollapsed,
    };

    try {
      localStorage.setItem(SIDEBAR_COLLAPSE_STATE_KEY, JSON.stringify(state));
    } catch {
      // Ignore localStorage write failures (e.g., disabled storage)
    }
  }, [
    isSearching,
    toolsCollapsed,
    favoritesCollapsed,
    channelsCollapsed,
    contactsCollapsed,
    roomsCollapsed,
    repeatersCollapsed,
  ]);

  // Separate favorites from regular items, and build combined favorites list
  const {
    favoriteItems,
    nonFavoriteChannels,
    nonFavoriteContacts,
    nonFavoriteRooms,
    nonFavoriteRepeaters,
  } = useMemo(() => {
    const favChannels = filteredChannels.filter((c) => c.favorite);
    const favContacts = [
      ...filteredNonRepeaterContacts,
      ...filteredRooms,
      ...filteredRepeaters,
    ].filter((c) => c.favorite);
    const nonFavChannels = filteredChannels.filter((c) => !c.favorite);
    const nonFavContacts = filteredNonRepeaterContacts.filter((c) => !c.favorite);
    const nonFavRooms = filteredRooms.filter((c) => !c.favorite);
    const nonFavRepeaters = filteredRepeaters.filter((c) => !c.favorite);

    const items: FavoriteItem[] = [
      ...favChannels.map((channel) => ({ type: 'channel' as const, channel })),
      ...favContacts.map((contact) => ({ type: 'contact' as const, contact })),
    ];

    return {
      favoriteItems: sortFavoriteItemsByOrder(items, sectionSortOrders.favorites),
      nonFavoriteChannels: nonFavChannels,
      nonFavoriteContacts: nonFavContacts,
      nonFavoriteRooms: nonFavRooms,
      nonFavoriteRepeaters: nonFavRepeaters,
    };
  }, [
    filteredChannels,
    filteredNonRepeaterContacts,
    filteredRooms,
    filteredRepeaters,
    sectionSortOrders.favorites,
    sortFavoriteItemsByOrder,
  ]);

  const buildChannelRow = (channel: Channel, keyPrefix: string): ConversationRow => ({
    key: `${keyPrefix}-${channel.key}`,
    type: 'channel',
    id: channel.key,
    name: channel.name,
    unreadCount: channel.muted ? 0 : getUnreadCount('channel', channel.key),
    isMention: channel.muted ? false : hasMention('channel', channel.key),
    notificationsEnabled: isConversationNotificationsEnabled?.('channel', channel.key) ?? false,
    muted: channel.muted,
  });

  const buildContactRow = (contact: Contact, keyPrefix: string): ConversationRow => ({
    key: `${keyPrefix}-${contact.public_key}`,
    type: 'contact',
    id: contact.public_key,
    name: getContactDisplayName(contact.name, contact.public_key, contact.last_advert),
    unreadCount: getUnreadCount('contact', contact.public_key),
    isMention: hasMention('contact', contact.public_key),
    notificationsEnabled:
      isConversationNotificationsEnabled?.('contact', contact.public_key) ?? false,
    contact,
  });

  const renderConversationRow = (row: ConversationRow) => {
    const highlightUnread =
      row.isMention ||
      (row.type === 'contact' &&
        row.contact?.type !== CONTACT_TYPE_REPEATER &&
        row.unreadCount > 0);

    return (
      <div
        key={row.key}
        className={cn(
          'px-3 py-2 cursor-pointer flex items-center gap-2 border-l-2 border-transparent hover:bg-accent transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring',
          isActive(row.type, row.id) && 'bg-accent border-l-primary',
          row.unreadCount > 0 && '[&_.name]:font-semibold [&_.name]:text-foreground'
        )}
        role="button"
        tabIndex={0}
        aria-current={isActive(row.type, row.id) ? 'page' : undefined}
        onKeyDown={handleKeyboardActivate}
        onClick={() =>
          handleSelectConversation({
            type: row.type,
            id: row.id,
            name: row.name,
          })
        }
      >
        {row.type === 'contact' && row.contact && (
          <ContactAvatar
            name={row.contact.name}
            publicKey={row.contact.public_key}
            size={24}
            contactType={row.contact.type}
          />
        )}
        <span className="name flex-1 truncate text-[0.8125rem]">{row.name}</span>
        <span className="ml-auto flex items-center gap-1">
          {row.muted ? (
            <span aria-label="Channel muted" title="Channel muted">
              <BellOff className="h-3.5 w-3.5 text-muted-foreground" />
            </span>
          ) : (
            <>
              {row.notificationsEnabled && (
                <span aria-label="Notifications enabled" title="Notifications enabled">
                  <Bell className="h-3.5 w-3.5 text-muted-foreground" />
                </span>
              )}
              {row.unreadCount > 0 && (
                <span
                  className={cn(
                    'text-[0.625rem] font-semibold px-1.5 py-0.5 rounded-full min-w-[18px] text-center',
                    highlightUnread
                      ? 'bg-badge-mention text-badge-mention-foreground'
                      : 'bg-badge-unread/90 text-badge-unread-foreground'
                  )}
                  aria-label={`${row.unreadCount} unread message${row.unreadCount !== 1 ? 's' : ''}`}
                >
                  {row.unreadCount}
                </span>
              )}
            </>
          )}
        </span>
      </div>
    );
  };

  const renderSidebarActionRow = ({
    key,
    active = false,
    icon,
    label,
    onClick,
  }: {
    key: string;
    active?: boolean;
    icon: React.ReactNode;
    label: React.ReactNode;
    onClick: () => void;
  }) => (
    <div
      key={key}
      data-active={active ? 'true' : undefined}
      className={cn(
        'sidebar-action-row px-3 py-2 cursor-pointer flex items-center gap-2 border-l-2 border-transparent hover:bg-accent transition-colors text-[0.8125rem] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring',
        active && 'bg-accent border-l-primary'
      )}
      role="button"
      tabIndex={0}
      aria-current={active ? 'page' : undefined}
      onKeyDown={handleKeyboardActivate}
      onClick={onClick}
    >
      <span className="sidebar-tool-icon" aria-hidden="true">
        {icon}
      </span>
      <span className="sidebar-tool-label flex-1 truncate">{label}</span>
    </div>
  );

  const getSectionUnreadCount = (rows: ConversationRow[]): number =>
    rows.reduce((total, row) => total + row.unreadCount, 0);

  const sectionHasMention = (rows: ConversationRow[]): boolean => rows.some((row) => row.isMention);

  const favoriteRows = favoriteItems.map((item) =>
    item.type === 'channel'
      ? buildChannelRow(item.channel, 'fav-chan')
      : buildContactRow(item.contact, 'fav-contact')
  );
  const channelRows = nonFavoriteChannels.map((channel) => buildChannelRow(channel, 'chan'));
  const contactRows = nonFavoriteContacts.map((contact) => buildContactRow(contact, 'contact'));
  const roomRows = nonFavoriteRooms.map((contact) => buildContactRow(contact, 'room'));
  const repeaterRows = nonFavoriteRepeaters.map((contact) => buildContactRow(contact, 'repeater'));

  const favoritesUnreadCount = getSectionUnreadCount(favoriteRows);
  const channelsUnreadCount = getSectionUnreadCount(channelRows);
  const contactsUnreadCount = getSectionUnreadCount(contactRows);
  const roomsUnreadCount = getSectionUnreadCount(roomRows);
  const repeatersUnreadCount = getSectionUnreadCount(repeaterRows);
  const favoritesHasMention = sectionHasMention(favoriteRows);
  const channelsHasMention = sectionHasMention(channelRows);
  const toolRows = !query
    ? [
        renderSidebarActionRow({
          key: 'tool-raw',
          active: isActive('raw', 'raw'),
          icon: <Logs className="h-4 w-4" />,
          label: 'Packet Feed',
          onClick: () =>
            handleSelectConversation({
              type: 'raw',
              id: 'raw',
              name: 'Raw Packet Feed',
            }),
        }),
        renderSidebarActionRow({
          key: 'tool-map',
          active: isActive('map', 'map'),
          icon: <Map className="h-4 w-4" />,
          label: 'Node Map',
          onClick: () =>
            handleSelectConversation({
              type: 'map',
              id: 'map',
              name: 'Node Map',
            }),
        }),
        renderSidebarActionRow({
          key: 'tool-visualizer',
          active: isActive('visualizer', 'visualizer'),
          icon: <ChartNetwork className="h-4 w-4" />,
          label: 'Mesh Visualizer',
          onClick: () =>
            handleSelectConversation({
              type: 'visualizer',
              id: 'visualizer',
              name: 'Mesh Visualizer',
            }),
        }),
        renderSidebarActionRow({
          key: 'tool-trace',
          active: isActive('trace', 'trace'),
          icon: <Cable className="h-4 w-4" />,
          label: 'Trace',
          onClick: () =>
            handleSelectConversation({
              type: 'trace',
              id: 'trace',
              name: 'Trace',
            }),
        }),
        renderSidebarActionRow({
          key: 'tool-search',
          active: isActive('search', 'search'),
          icon: <SearchIcon className="h-4 w-4" />,
          label: 'Message Search',
          onClick: () =>
            handleSelectConversation({
              type: 'search',
              id: 'search',
              name: 'Message Search',
            }),
        }),
        renderSidebarActionRow({
          key: 'tool-bots',
          active: isActive('bots', 'bots'),
          icon: <Bot className="h-4 w-4" />,
          label: 'Bots',
          onClick: () =>
            handleSelectConversation({
              type: 'bots',
              id: 'bots',
              name: 'Bots',
            }),
        }),
        renderSidebarActionRow({
          key: 'tool-cracker',
          active: showCracker,
          icon: <LockOpen className="h-4 w-4" />,
          label: (
            <>
              {showCracker ? 'Hide' : 'Show'} Channel Finder
              <span
                className={cn(
                  'ml-1 text-[0.6875rem]',
                  crackerRunning ? 'text-primary' : 'text-muted-foreground'
                )}
              >
                ({crackerRunning ? 'running' : 'idle'})
              </span>
            </>
          ),
          onClick: onToggleCracker,
        }),
      ]
    : [];

  const renderSectionHeader = (
    title: string,
    collapsed: boolean,
    onToggle: () => void,
    sortSection: SidebarSortableSection | null = null,
    unreadCount = 0,
    highlightUnread = false
  ) => {
    const effectiveCollapsed = isSearching ? false : collapsed;
    const sectionSortOrder = sortSection ? sectionSortOrders[sortSection] : null;

    return (
      <div className="flex justify-between items-center px-3 py-2 pt-3.5">
        <button
          className={cn(
            'flex items-center gap-1.5 text-[0.625rem] uppercase tracking-wider text-muted-foreground hover:text-foreground transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring rounded',
            isSearching && 'cursor-default'
          )}
          aria-expanded={!effectiveCollapsed}
          onClick={() => {
            if (!isSearching) onToggle();
          }}
          title={effectiveCollapsed ? `Expand ${title}` : `Collapse ${title}`}
        >
          {effectiveCollapsed ? (
            <ChevronRight className="h-3.5 w-3.5" aria-hidden="true" />
          ) : (
            <ChevronDown className="h-3.5 w-3.5" aria-hidden="true" />
          )}
          <span>{title}</span>
        </button>
        {(sortSection || unreadCount > 0) && (
          <div className="ml-auto flex items-center gap-1.5">
            {sortSection && sectionSortOrder && (
              <button
                className="bg-transparent text-muted-foreground/60 px-1 py-0.5 text-[0.625rem] rounded hover:text-foreground transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring whitespace-nowrap"
                onClick={() => handleSortToggle(sortSection)}
                aria-label={`Sort ${title} ${sortOrderDescription(
                  nextSortOrder(sortSection, sectionSortOrder)
                )}`}
                title={`Currently sorted ${sortOrderDescription(
                  sectionSortOrder
                )}. Click to sort ${sortOrderDescription(
                  nextSortOrder(sortSection, sectionSortOrder)
                )}.`}
              >
                {sortOrderLabel(sectionSortOrder)}
              </button>
            )}
            {unreadCount > 0 && (
              <span
                className={cn(
                  'text-[0.625rem] font-medium px-1.5 py-0.5 rounded-full',
                  highlightUnread
                    ? 'bg-badge-mention text-badge-mention-foreground'
                    : 'bg-secondary text-muted-foreground'
                )}
                aria-label={`${unreadCount} unread`}
              >
                {unreadCount}
              </span>
            )}
          </div>
        )}
      </div>
    );
  };

  return (
    <nav
      className="sidebar w-60 h-full min-h-0 overflow-hidden bg-card border-r border-border flex flex-col"
      aria-label="Conversations"
    >
      {/* Header */}
      <div className="px-3 py-2 border-b border-border">
        <Button
          variant="outline"
          size="sm"
          onClick={onNewMessage}
          title="Add channel or contact"
          aria-label="Add channel or contact"
          className="h-8 w-full justify-start gap-2 border-primary/20 bg-primary/5 px-3 text-[0.8125rem] text-primary hover:bg-primary/10 hover:text-primary"
        >
          <SquarePen className="h-4 w-4" />
          <span>Add Channel/Contact</span>
        </Button>
      </div>

      {/* List */}
      <div className="flex-1 min-h-0 overflow-y-auto [contain:layout_paint]">
        <div className="px-3 py-2 border-b border-border/60">
          <div className="relative min-w-0">
            <Input
              type="text"
              placeholder="Search channels/contacts..."
              aria-label="Search conversations"
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              className={cn('h-7 text-[0.8125rem] bg-background/50', searchQuery ? 'pr-8' : 'pr-3')}
            />
            {searchQuery && (
              <button
                className="absolute right-2 top-1/2 -translate-y-1/2 text-muted-foreground hover:text-foreground text-lg leading-none transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring rounded"
                onClick={() => setSearchQuery('')}
                title="Clear search"
                aria-label="Clear search"
              >
                <X className="h-4 w-4" />
              </button>
            )}
          </div>
        </div>

        {/* Tools */}
        {toolRows.length > 0 && (
          <>
            {renderSectionHeader('Tools', toolsCollapsed, () => setToolsCollapsed((prev) => !prev))}
            {(isSearching || !toolsCollapsed) && toolRows}
          </>
        )}

        {/* Mark All Read */}
        {!query && Object.values(unreadCounts).some((c) => c > 0) && (
          <div
            className="px-3 py-2 cursor-pointer flex items-center gap-2 border-l-2 border-transparent hover:bg-accent transition-colors text-[0.8125rem] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
            role="button"
            tabIndex={0}
            onKeyDown={handleKeyboardActivate}
            onClick={onMarkAllRead}
          >
            <CheckCheck className="h-4 w-4 text-muted-foreground" aria-hidden="true" />
            <span className="flex-1 truncate text-muted-foreground">Mark all as read</span>
          </div>
        )}

        {/* Favorites */}
        {favoriteItems.length > 0 && (
          <>
            {renderSectionHeader(
              'Favorites',
              favoritesCollapsed,
              () => setFavoritesCollapsed((prev) => !prev),
              'favorites',
              favoritesUnreadCount,
              favoritesHasMention
            )}
            {(isSearching || !favoritesCollapsed) &&
              favoriteRows.map((row) => renderConversationRow(row))}
          </>
        )}

        {/* Channels */}
        {nonFavoriteChannels.length > 0 && (
          <>
            {renderSectionHeader(
              'Channels',
              channelsCollapsed,
              () => setChannelsCollapsed((prev) => !prev),
              'channels',
              channelsUnreadCount,
              channelsHasMention
            )}
            {(isSearching || !channelsCollapsed) &&
              channelRows.map((row) => renderConversationRow(row))}
          </>
        )}

        {/* Contacts */}
        {nonFavoriteContacts.length > 0 && (
          <>
            {renderSectionHeader(
              'Contacts',
              contactsCollapsed,
              () => setContactsCollapsed((prev) => !prev),
              'contacts',
              contactsUnreadCount,
              contactsUnreadCount > 0
            )}
            {(isSearching || !contactsCollapsed) &&
              contactRows.map((row) => renderConversationRow(row))}
          </>
        )}

        {/* Repeaters */}
        {nonFavoriteRepeaters.length > 0 && (
          <>
            {renderSectionHeader(
              'Repeaters',
              repeatersCollapsed,
              () => setRepeatersCollapsed((prev) => !prev),
              'repeaters',
              repeatersUnreadCount
            )}
            {(isSearching || !repeatersCollapsed) &&
              repeaterRows.map((row) => renderConversationRow(row))}
          </>
        )}

        {/* Room Servers */}
        {nonFavoriteRooms.length > 0 && (
          <>
            {renderSectionHeader(
              'Room Servers',
              roomsCollapsed,
              () => setRoomsCollapsed((prev) => !prev),
              'rooms',
              roomsUnreadCount,
              roomsUnreadCount > 0
            )}
            {(isSearching || !roomsCollapsed) && roomRows.map((row) => renderConversationRow(row))}
          </>
        )}

        {/* Empty state */}
        {nonFavoriteContacts.length === 0 &&
          nonFavoriteRooms.length === 0 &&
          nonFavoriteChannels.length === 0 &&
          nonFavoriteRepeaters.length === 0 &&
          favoriteItems.length === 0 && (
            <div className="p-5 text-center text-muted-foreground">
              {query ? 'No matches found' : 'No conversations yet'}
            </div>
          )}
      </div>
    </nav>
  );
}
