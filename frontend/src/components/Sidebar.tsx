import { useState } from 'react';
import type { Contact, Channel, Conversation } from '../types';
import { getStateKey, type ConversationTimes } from '../utils/conversationState';
import { getPubkeyPrefix, getContactDisplayName } from '../utils/pubkey';
import { ContactAvatar } from './ContactAvatar';
import { CONTACT_TYPE_REPEATER } from '../utils/contactAvatar';
import { Input } from './ui/input';
import { Button } from './ui/button';
import { cn } from '@/lib/utils';

type SortOrder = 'alpha' | 'recent';

interface SidebarProps {
  contacts: Contact[];
  channels: Channel[];
  activeConversation: Conversation | null;
  onSelectConversation: (conversation: Conversation) => void;
  onNewMessage: () => void;
  lastMessageTimes: ConversationTimes;
  unreadCounts: Record<string, number>;
}

// Load sort preference from localStorage
function loadSortOrder(): SortOrder {
  try {
    const stored = localStorage.getItem('remoteterm-sortOrder');
    return stored === 'recent' ? 'recent' : 'alpha';
  } catch {
    return 'alpha';
  }
}

// Save sort preference to localStorage
function saveSortOrder(order: SortOrder): void {
  try {
    localStorage.setItem('remoteterm-sortOrder', order);
  } catch {
    // localStorage might be full or disabled
  }
}

export function Sidebar({
  contacts,
  channels,
  activeConversation,
  onSelectConversation,
  onNewMessage,
  lastMessageTimes,
  unreadCounts,
}: SidebarProps) {
  const [sortOrder, setSortOrder] = useState<SortOrder>(loadSortOrder);
  const [searchQuery, setSearchQuery] = useState('');

  const handleSortToggle = () => {
    const newOrder = sortOrder === 'alpha' ? 'recent' : 'alpha';
    setSortOrder(newOrder);
    saveSortOrder(newOrder);
  };

  const handleSelectConversation = (conversation: Conversation) => {
    setSearchQuery('');
    onSelectConversation(conversation);
  };

  const isActive = (type: 'contact' | 'channel' | 'raw', id: string) =>
    activeConversation?.type === type && activeConversation?.id === id;

  // Get unread count for a conversation
  const getUnreadCount = (type: 'channel' | 'contact', id: string): number => {
    const key = getStateKey(type, id);
    return unreadCounts[key] || 0;
  };

  const getLastMessageTime = (type: 'channel' | 'contact', id: string) => {
    const key = getStateKey(type, id);
    return lastMessageTimes[key] || 0;
  };

  // Deduplicate channels by name, keeping the first (lowest index)
  const uniqueChannels = channels.reduce<Channel[]>((acc, channel) => {
    if (!acc.some((c) => c.name === channel.name)) {
      acc.push(channel);
    }
    return acc;
  }, []);

  // Deduplicate contacts by 12-char prefix, preferring ones with names
  // Also filter out any contacts with empty public keys
  const uniqueContacts = contacts
    .filter((c) => c.public_key && c.public_key.length > 0)
    .sort((a, b) => {
      // Sort contacts with names first
      if (a.name && !b.name) return -1;
      if (!a.name && b.name) return 1;
      return (a.name || '').localeCompare(b.name || '');
    })
    .reduce<Contact[]>((acc, contact) => {
      const prefix = getPubkeyPrefix(contact.public_key);
      if (!acc.some((c) => getPubkeyPrefix(c.public_key) === prefix)) {
        acc.push(contact);
      }
      return acc;
    }, []);

  // Sort channels based on sort order, with Public always first
  const sortedChannels = [...uniqueChannels].sort((a, b) => {
    // Public channel always sorts to the top
    if (a.name === 'Public') return -1;
    if (b.name === 'Public') return 1;

    if (sortOrder === 'recent') {
      const timeA = getLastMessageTime('channel', a.key);
      const timeB = getLastMessageTime('channel', b.key);
      // If both have messages, sort by most recent first
      if (timeA && timeB) return timeB - timeA;
      // Items with messages come before items without
      if (timeA && !timeB) return -1;
      if (!timeA && timeB) return 1;
      // Fall back to alpha for items without messages
    }
    return a.name.localeCompare(b.name);
  });

  // Sort contacts: non-repeaters first (by recent or alpha), then repeaters (always alpha)
  const sortedContacts = [...uniqueContacts].sort((a, b) => {
    const aIsRepeater = a.type === CONTACT_TYPE_REPEATER;
    const bIsRepeater = b.type === CONTACT_TYPE_REPEATER;

    // Repeaters always go to the bottom
    if (aIsRepeater && !bIsRepeater) return 1;
    if (!aIsRepeater && bIsRepeater) return -1;

    // Both repeaters: always sort alphabetically
    if (aIsRepeater && bIsRepeater) {
      return (a.name || a.public_key).localeCompare(b.name || b.public_key);
    }

    // Both non-repeaters: use selected sort order
    if (sortOrder === 'recent') {
      const timeA = getLastMessageTime('contact', a.public_key);
      const timeB = getLastMessageTime('contact', b.public_key);
      // If both have messages, sort by most recent first
      if (timeA && timeB) return timeB - timeA;
      // Items with messages come before items without
      if (timeA && !timeB) return -1;
      if (!timeA && timeB) return 1;
      // Fall back to alpha for items without messages
    }
    return (a.name || a.public_key).localeCompare(b.name || b.public_key);
  });

  // Filter by search query
  const query = searchQuery.toLowerCase().trim();
  const filteredChannels = query
    ? sortedChannels.filter((c) => c.name.toLowerCase().includes(query))
    : sortedChannels;
  const filteredContacts = query
    ? sortedContacts.filter((c) =>
        (c.name?.toLowerCase().includes(query)) ||
        c.public_key.toLowerCase().includes(query)
      )
    : sortedContacts;

  return (
    <div className="sidebar w-60 h-full min-h-0 bg-card border-r border-border flex flex-col">
      {/* Header */}
      <div className="flex justify-between items-center px-3 py-3 border-b border-border">
        <h2 className="text-xs uppercase text-muted-foreground font-medium">Conversations</h2>
        <Button
          variant="ghost"
          size="sm"
          onClick={onNewMessage}
          title="New Message"
          className="h-6 w-6 p-0 text-muted-foreground hover:text-foreground"
        >
          +
        </Button>
      </div>

      {/* Search */}
      <div className="relative px-3 py-2 border-b border-border">
        <Input
          type="text"
          placeholder="Search..."
          value={searchQuery}
          onChange={(e) => setSearchQuery(e.target.value)}
          className="h-8 text-sm pr-8"
        />
        {searchQuery && (
          <button
            className="absolute right-4 top-1/2 -translate-y-1/2 text-muted-foreground hover:text-foreground text-lg leading-none"
            onClick={() => setSearchQuery('')}
            title="Clear search"
          >
            ×
          </button>
        )}
      </div>

      {/* List */}
      <div className="flex-1 overflow-y-auto">
        {/* Raw Packet Feed */}
        {!query && (
          <div
            className={cn(
              "px-3 py-2.5 cursor-pointer flex items-center gap-2 border-l-2 border-transparent hover:bg-accent",
              isActive('raw', 'raw') && "bg-accent border-l-primary"
            )}
            onClick={() =>
              handleSelectConversation({
                type: 'raw',
                id: 'raw',
                name: 'Raw Packet Feed',
              })
            }
          >
            <span className="text-muted-foreground text-xs">📡</span>
            <span className="flex-1 truncate">Packet Feed</span>
          </div>
        )}

        {/* Channels */}
        {filteredChannels.length > 0 && (
          <>
            <div className="flex justify-between items-center px-3 py-2 pt-3">
              <span className="text-[11px] uppercase text-muted-foreground">Channels</span>
              <button
                className="bg-transparent border border-border text-muted-foreground px-1.5 py-0.5 text-[10px] rounded hover:bg-accent hover:text-foreground"
                onClick={handleSortToggle}
                title={sortOrder === 'alpha' ? 'Sort by recent' : 'Sort alphabetically'}
              >
                {sortOrder === 'alpha' ? 'A-Z' : '⏱'}
              </button>
            </div>
            {filteredChannels.map((channel) => {
              const unreadCount = getUnreadCount('channel', channel.key);
              return (
                <div
                  key={`chan-${channel.key}`}
                  className={cn(
                    "px-3 py-2.5 cursor-pointer flex items-center gap-2 border-l-2 border-transparent hover:bg-accent",
                    isActive('channel', channel.key) && "bg-accent border-l-primary",
                    unreadCount > 0 && "[&_.name]:font-bold [&_.name]:text-foreground"
                  )}
                  onClick={() =>
                    handleSelectConversation({
                      type: 'channel',
                      id: channel.key,
                      name: channel.name,
                    })
                  }
                >
                  <span className="text-muted-foreground text-xs">#</span>
                  <span className="name flex-1 truncate">{channel.name}</span>
                  {unreadCount > 0 && (
                    <span className="bg-primary text-primary-foreground text-[10px] font-semibold px-1.5 py-0.5 rounded-full min-w-[18px] text-center">
                      {unreadCount}
                    </span>
                  )}
                </div>
              );
            })}
          </>
        )}

        {/* Contacts */}
        {filteredContacts.length > 0 && (
          <>
            <div className="flex justify-between items-center px-3 py-2 pt-3">
              <span className="text-[11px] uppercase text-muted-foreground">Contacts</span>
              {filteredChannels.length === 0 && (
                <button
                  className="bg-transparent border border-border text-muted-foreground px-1.5 py-0.5 text-[10px] rounded hover:bg-accent hover:text-foreground"
                  onClick={handleSortToggle}
                  title={sortOrder === 'alpha' ? 'Sort by recent' : 'Sort alphabetically'}
                >
                  {sortOrder === 'alpha' ? 'A-Z' : '⏱'}
                </button>
              )}
            </div>
            {filteredContacts.map((contact) => {
              const unreadCount = getUnreadCount('contact', contact.public_key);
              return (
                <div
                  key={contact.public_key}
                  className={cn(
                    "px-3 py-2.5 cursor-pointer flex items-center gap-2 border-l-2 border-transparent hover:bg-accent",
                    isActive('contact', contact.public_key) && "bg-accent border-l-primary",
                    unreadCount > 0 && "[&_.name]:font-bold [&_.name]:text-foreground"
                  )}
                  onClick={() =>
                    handleSelectConversation({
                      type: 'contact',
                      id: contact.public_key,
                      name: getContactDisplayName(contact.name, contact.public_key),
                    })
                  }
                >
                  <ContactAvatar name={contact.name} publicKey={contact.public_key} size={24} contactType={contact.type} />
                  <span className="name flex-1 truncate">
                    {getContactDisplayName(contact.name, contact.public_key)}
                  </span>
                  {unreadCount > 0 && (
                    <span className="bg-primary text-primary-foreground text-[10px] font-semibold px-1.5 py-0.5 rounded-full min-w-[18px] text-center">
                      {unreadCount}
                    </span>
                  )}
                </div>
              );
            })}
          </>
        )}

        {/* Empty state */}
        {filteredContacts.length === 0 && filteredChannels.length === 0 && (
          <div className="p-5 text-center text-muted-foreground">
            {query ? 'No matches found' : 'No conversations yet'}
          </div>
        )}
      </div>
    </div>
  );
}
