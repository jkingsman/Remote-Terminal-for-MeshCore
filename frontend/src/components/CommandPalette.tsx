import { useCallback, useEffect, useMemo, useState } from 'react';
import {
  Bot,
  Hash,
  Map,
  MessageSquare,
  Network,
  Radio,
  Route,
  Search,
  Star,
  User,
  Waypoints,
} from 'lucide-react';

import {
  Command,
  CommandEmpty,
  CommandGroup,
  CommandInput,
  CommandItem,
  CommandList,
} from './ui/command';
import { Dialog, DialogContent, DialogDescription, DialogTitle } from './ui/dialog';
import { getContactDisplayName } from '../utils/pubkey';
import {
  SETTINGS_SECTION_LABELS,
  SETTINGS_SECTION_ORDER,
  SETTINGS_SECTION_ICONS,
  type SettingsSection,
} from './settings/settingsConstants';
import type { Channel, Contact, Conversation } from '../types';
import { CONTACT_TYPE_REPEATER, CONTACT_TYPE_ROOM } from '../types';

const MAX_PER_GROUP = 8;

interface CommandPaletteProps {
  contacts: Contact[];
  channels: Channel[];
  onSelectConversation: (conv: Conversation) => void;
  onOpenSettings: (section: SettingsSection) => void;
  onRepeaterAutoLogin: (publicKey: string, displayName: string) => void;
}

interface Searchable {
  searchText: string;
  keyText?: string;
}

interface SearchableContact extends Searchable {
  contact: Contact;
  displayName: string;
}

interface SearchableChannel extends Searchable {
  channel: Channel;
}

interface ToolItem extends Searchable {
  id: string;
  name: string;
  icon: React.ComponentType<{ className?: string }>;
  type: 'raw' | 'map' | 'visualizer' | 'search' | 'trace' | 'bots';
}

interface SettingItem extends Searchable {
  section: SettingsSection;
  label: string;
  icon: React.ComponentType<{ className?: string }>;
}

const TOOL_ITEMS: ToolItem[] = [
  { id: 'raw', name: 'Raw Packet Feed', icon: Radio, type: 'raw', searchText: 'raw packet feed' },
  { id: 'map', name: 'Map View', icon: Map, type: 'map', searchText: 'map view' },
  {
    id: 'visualizer',
    name: 'Network Visualizer',
    icon: Network,
    type: 'visualizer',
    searchText: 'network visualizer',
  },
  {
    id: 'search',
    name: 'Message Search',
    icon: Search,
    type: 'search',
    searchText: 'message search',
  },
  { id: 'trace', name: 'Route Trace', icon: Route, type: 'trace', searchText: 'route trace' },
  { id: 'bots', name: 'Bots', icon: Bot, type: 'bots', searchText: 'bots' },
];

const SETTING_ITEMS: SettingItem[] = SETTINGS_SECTION_ORDER.map((section) => ({
  section,
  label: SETTINGS_SECTION_LABELS[section],
  icon: SETTINGS_SECTION_ICONS[section],
  searchText: `settings ${SETTINGS_SECTION_LABELS[section]}`.toLowerCase(),
}));

function fuzzyMatch(text: string, query: string): boolean {
  let qi = 0;
  for (let ti = 0; ti < text.length && qi < query.length; ti++) {
    if (text[ti] === query[qi]) qi++;
  }
  return qi === query.length;
}

function filterList<T extends Searchable>(items: T[], query: string): T[] {
  if (!query) return items.slice(0, MAX_PER_GROUP);
  const results: T[] = [];
  for (const item of items) {
    const nameMatch = fuzzyMatch(item.searchText, query);
    const keyMatch = item.keyText ? item.keyText.startsWith(query) : false;
    if (nameMatch || keyMatch) {
      results.push(item);
      if (results.length >= MAX_PER_GROUP) break;
    }
  }
  return results;
}

export function CommandPalette({
  contacts,
  channels,
  onSelectConversation,
  onOpenSettings,
  onRepeaterAutoLogin,
}: CommandPaletteProps) {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState('');

  useEffect(() => {
    function onKeyDown(e: KeyboardEvent) {
      if (e.key === 'k' && (e.metaKey || e.ctrlKey)) {
        e.preventDefault();
        setOpen((prev) => !prev);
      }
    }
    document.addEventListener('keydown', onKeyDown);
    return () => document.removeEventListener('keydown', onKeyDown);
  }, []);

  const select = useCallback((action: () => void) => {
    setOpen(false);
    action();
  }, []);

  const {
    favContacts,
    favRepeaters,
    regularContacts,
    repeaters,
    rooms,
    favChannels,
    regularChannels,
  } = useMemo(() => {
    const fc: SearchableContact[] = [];
    const fr: SearchableContact[] = [];
    const rc: SearchableContact[] = [];
    const rp: SearchableContact[] = [];
    const rm: SearchableContact[] = [];
    for (const c of contacts) {
      const displayName = getContactDisplayName(c.name, c.public_key, c.last_advert);
      const entry: SearchableContact = {
        contact: c,
        displayName,
        searchText: displayName.toLowerCase(),
        keyText: c.public_key.toLowerCase(),
      };
      if (c.type === CONTACT_TYPE_REPEATER) {
        (c.favorite ? fr : rp).push(entry);
      } else if (c.type === CONTACT_TYPE_ROOM) {
        rm.push(entry);
      } else {
        (c.favorite ? fc : rc).push(entry);
      }
    }
    const fch: SearchableChannel[] = [];
    const rch: SearchableChannel[] = [];
    for (const ch of channels) {
      const entry: SearchableChannel = {
        channel: ch,
        searchText: ch.name.toLowerCase(),
        keyText: ch.key.toLowerCase(),
      };
      (ch.favorite ? fch : rch).push(entry);
    }
    return {
      favContacts: fc,
      favRepeaters: fr,
      regularContacts: rc,
      repeaters: rp,
      rooms: rm,
      favChannels: fch,
      regularChannels: rch,
    };
  }, [contacts, channels]);

  const lq = query.toLowerCase();
  const fTools = filterList(TOOL_ITEMS, lq);
  const fSettings = filterList(SETTING_ITEMS, lq);
  const fFavContacts = filterList(favContacts, lq);
  const fFavRepeaters = filterList(favRepeaters, lq);
  const fFavChannels = filterList(favChannels, lq);
  const fContacts = filterList(regularContacts, lq);
  const fRepeaters = filterList(repeaters, lq);
  const fRooms = filterList(rooms, lq);
  const fChannels = filterList(regularChannels, lq);

  const totalResults =
    fTools.length +
    fSettings.length +
    fFavContacts.length +
    fFavRepeaters.length +
    fFavChannels.length +
    fContacts.length +
    fRepeaters.length +
    fRooms.length +
    fChannels.length;

  return (
    <Dialog
      open={open}
      onOpenChange={(nextOpen) => {
        setOpen(nextOpen);
        if (!nextOpen) setQuery('');
      }}
    >
      <DialogContent className="overflow-hidden p-0 shadow-lg" hideCloseButton>
        <DialogTitle className="sr-only">Command palette</DialogTitle>
        <DialogDescription className="sr-only">
          Search for conversations, settings, and tools
        </DialogDescription>
        <Command shouldFilter={false}>
          <CommandInput placeholder="Jump to..." value={query} onValueChange={setQuery} />
          <CommandList>
            {totalResults === 0 && <CommandEmpty>No results found.</CommandEmpty>}

            {fTools.length > 0 && (
              <CommandGroup heading="Tools">
                {fTools.map((tool) => (
                  <CommandItem
                    key={tool.id}
                    onSelect={() =>
                      select(() =>
                        onSelectConversation({ type: tool.type, id: tool.id, name: tool.name })
                      )
                    }
                  >
                    <tool.icon className="text-muted-foreground" />
                    <span>{tool.name}</span>
                  </CommandItem>
                ))}
              </CommandGroup>
            )}

            {fSettings.length > 0 && (
              <CommandGroup heading="Settings">
                {fSettings.map((item) => (
                  <CommandItem
                    key={item.section}
                    onSelect={() => select(() => onOpenSettings(item.section))}
                  >
                    <item.icon className="text-muted-foreground" />
                    <span>{item.label}</span>
                  </CommandItem>
                ))}
              </CommandGroup>
            )}

            {fFavContacts.length > 0 && (
              <ContactGroup
                heading="Favorite Contacts"
                items={fFavContacts}
                icon={User}
                onSelect={select}
                onSelectConversation={onSelectConversation}
                showStar
              />
            )}

            {fFavRepeaters.length > 0 && (
              <RepeaterGroup
                heading="Favorite Repeaters"
                items={fFavRepeaters}
                onSelect={select}
                onSelectConversation={onSelectConversation}
                onRepeaterAutoLogin={onRepeaterAutoLogin}
                showStar
              />
            )}

            {fFavChannels.length > 0 && (
              <CommandGroup heading="Favorite Channels">
                {fFavChannels.map(({ channel: ch }) => (
                  <CommandItem
                    key={ch.key}
                    onSelect={() =>
                      select(() =>
                        onSelectConversation({ type: 'channel', id: ch.key, name: ch.name })
                      )
                    }
                  >
                    <Hash className="text-muted-foreground" />
                    <span>{ch.name}</span>
                    <Star className="ml-auto h-3 w-3 text-favorite" />
                  </CommandItem>
                ))}
              </CommandGroup>
            )}

            {fContacts.length > 0 && (
              <ContactGroup
                heading="Contacts"
                items={fContacts}
                icon={User}
                onSelect={select}
                onSelectConversation={onSelectConversation}
              />
            )}

            {fRepeaters.length > 0 && (
              <RepeaterGroup
                heading="Repeaters"
                items={fRepeaters}
                onSelect={select}
                onSelectConversation={onSelectConversation}
                onRepeaterAutoLogin={onRepeaterAutoLogin}
              />
            )}

            {fRooms.length > 0 && (
              <ContactGroup
                heading="Rooms"
                items={fRooms}
                icon={MessageSquare}
                onSelect={select}
                onSelectConversation={onSelectConversation}
              />
            )}

            {fChannels.length > 0 && (
              <CommandGroup heading="Channels">
                {fChannels.map(({ channel: ch }) => (
                  <CommandItem
                    key={ch.key}
                    onSelect={() =>
                      select(() =>
                        onSelectConversation({ type: 'channel', id: ch.key, name: ch.name })
                      )
                    }
                  >
                    <Hash className="text-muted-foreground" />
                    <span>{ch.name}</span>
                  </CommandItem>
                ))}
              </CommandGroup>
            )}
          </CommandList>
        </Command>
      </DialogContent>
    </Dialog>
  );
}

function ContactGroup({
  heading,
  items,
  icon: Icon,
  showStar,
  onSelect,
  onSelectConversation,
}: {
  heading: string;
  items: SearchableContact[];
  icon: React.ComponentType<{ className?: string }>;
  showStar?: boolean;
  onSelect: (action: () => void) => void;
  onSelectConversation: (conv: Conversation) => void;
}) {
  return (
    <CommandGroup heading={heading}>
      {items.map(({ contact: c, displayName }) => (
        <CommandItem
          key={c.public_key}
          onSelect={() =>
            onSelect(() =>
              onSelectConversation({ type: 'contact', id: c.public_key, name: displayName })
            )
          }
        >
          <Icon className="text-muted-foreground" />
          <span>{displayName}</span>
          {showStar && <Star className="ml-auto h-3 w-3 text-favorite" />}
        </CommandItem>
      ))}
    </CommandGroup>
  );
}

function RepeaterGroup({
  heading,
  items,
  showStar,
  onSelect,
  onSelectConversation,
  onRepeaterAutoLogin,
}: {
  heading: string;
  items: SearchableContact[];
  showStar?: boolean;
  onSelect: (action: () => void) => void;
  onSelectConversation: (conv: Conversation) => void;
  onRepeaterAutoLogin: (publicKey: string, displayName: string) => void;
}) {
  return (
    <CommandGroup heading={heading}>
      {items.flatMap(({ contact: c, displayName }) => [
        <CommandItem
          key={c.public_key}
          onSelect={() =>
            onSelect(() =>
              onSelectConversation({ type: 'contact', id: c.public_key, name: displayName })
            )
          }
        >
          <Waypoints className="text-muted-foreground" />
          <span>{displayName}</span>
          {showStar && <Star className="ml-auto h-3 w-3 text-favorite" />}
        </CommandItem>,
        <CommandItem
          key={`${c.public_key}-acl`}
          onSelect={() => onSelect(() => onRepeaterAutoLogin(c.public_key, displayName))}
        >
          <Waypoints className="text-muted-foreground" />
          <span>
            {displayName} <span className="text-muted-foreground">(ACL login + load all)</span>
          </span>
        </CommandItem>,
      ])}
    </CommandGroup>
  );
}
