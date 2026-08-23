import { useEffect, useRef, useState } from 'react';
import {
  Bell,
  BellOff,
  ChevronsLeftRight,
  Globe2,
  Info,
  Route,
  Shrink,
  Star,
  Trash2,
} from 'lucide-react';
import { toast } from './ui/sonner';
import { DirectTraceIcon } from './DirectTraceIcon';
import { ContactPathDiscoveryModal } from './ContactPathDiscoveryModal';
import { ChannelFloodScopeOverrideModal } from './ChannelFloodScopeOverrideModal';
import { ChannelPathHashModeOverrideModal } from './ChannelPathHashModeOverrideModal';
import { handleKeyboardActivate } from '../utils/a11y';
import { isPublicChannelKey } from '../utils/publicChannel';
import { stripRegionScopePrefix, floodScopeOverrideLabel } from '../utils/regionScope';
import { isPrefixOnlyContact } from '../utils/pubkey';
import { cn } from '../lib/utils';
import { ContactAvatar } from './ContactAvatar';
import { ContactStatusInfo } from './ContactStatusInfo';
import type { Channel, Contact, Conversation, PathDiscoveryResponse, RadioConfig } from '../types';
import { CONTACT_TYPE_ROOM } from '../types';

interface ChatHeaderProps {
  conversation: Conversation;
  contacts: Contact[];
  channels: Channel[];
  config: RadioConfig | null;
  notificationsSupported: boolean;
  notificationsEnabled: boolean;
  notificationsPermission: NotificationPermission | 'unsupported';
  onTrace: () => void;
  onPathDiscovery: (publicKey: string) => Promise<PathDiscoveryResponse>;
  onToggleNotifications: () => void;
  pushSupported?: boolean;
  pushSubscribed?: boolean;
  pushEnabledForConversation?: boolean;
  onTogglePush?: () => void;
  onOpenPushSettings?: () => void;
  onToggleFavorite: (type: 'channel' | 'contact', id: string) => void;
  onToggleMute?: (key: string) => void;
  onSetMcmpEnabled?: (type: 'channel' | 'contact', id: string, enabled: boolean) => void;
  onSetChannelFloodScopeOverride?: (key: string, floodScopeOverride: string) => void;
  onSetChannelPathHashModeOverride?: (key: string, pathHashModeOverride: number | null) => void;
  onDeleteChannel: (key: string) => void;
  onDeleteContact: (publicKey: string) => void;
  onOpenContactInfo?: (publicKey: string) => void;
  onOpenChannelInfo?: (channelKey: string) => void;
}

export function ChatHeader({
  conversation,
  contacts,
  channels,
  config,
  notificationsSupported,
  notificationsEnabled,
  notificationsPermission,
  onTrace,
  onPathDiscovery,
  onToggleNotifications,
  pushSupported,
  pushSubscribed,
  pushEnabledForConversation,
  onTogglePush,
  onOpenPushSettings,
  onToggleFavorite,
  onToggleMute,
  onSetMcmpEnabled,
  onSetChannelFloodScopeOverride,
  onSetChannelPathHashModeOverride,
  onDeleteChannel,
  onDeleteContact,
  onOpenContactInfo,
  onOpenChannelInfo,
}: ChatHeaderProps) {
  const [showKey, setShowKey] = useState(false);
  const [pathDiscoveryOpen, setPathDiscoveryOpen] = useState(false);
  const [channelOverrideOpen, setChannelOverrideOpen] = useState(false);
  const [pathHashModeOverrideOpen, setPathHashModeOverrideOpen] = useState(false);
  const [notifDropdownOpen, setNotifDropdownOpen] = useState(false);
  const notifDropdownRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    setShowKey(false);
    setPathDiscoveryOpen(false);
    setChannelOverrideOpen(false);
    setPathHashModeOverrideOpen(false);
    setNotifDropdownOpen(false);
  }, [conversation.id]);

  // Close notification dropdown on outside click
  useEffect(() => {
    if (!notifDropdownOpen) return;
    const handler = (e: MouseEvent) => {
      if (notifDropdownRef.current && !notifDropdownRef.current.contains(e.target as Node)) {
        setNotifDropdownOpen(false);
      }
    };
    document.addEventListener('mousedown', handler);
    return () => document.removeEventListener('mousedown', handler);
  }, [notifDropdownOpen]);

  const activeChannel =
    conversation.type === 'channel'
      ? channels.find((channel) => channel.key === conversation.id)
      : undefined;
  const activeFloodScopeOverride =
    conversation.type === 'channel' ? (activeChannel?.flood_scope_override ?? null) : null;
  const activeFloodScopeLabel = activeFloodScopeOverride
    ? stripRegionScopePrefix(activeFloodScopeOverride)
    : null;
  // Badge text: maps the raw override ("*", "#Region", null) to a friendly label
  // so the unscoped marker renders as "unscoped" instead of a bare "*".
  const activeFloodScopeBadge = floodScopeOverrideLabel(activeFloodScopeOverride);
  const activePathHashModeOverride =
    conversation.type === 'channel' ? (activeChannel?.path_hash_mode_override ?? null) : null;
  const showPathHashModeOverride =
    conversation.type === 'channel' &&
    onSetChannelPathHashModeOverride &&
    config?.path_hash_mode_supported;
  const isPrivateChannel = conversation.type === 'channel' && !activeChannel?.is_hashtag;
  const activeContact =
    conversation.type === 'contact'
      ? contacts.find((contact) => contact.public_key === conversation.id)
      : null;
  const activeContactIsRoomServer = activeContact?.type === CONTACT_TYPE_ROOM;
  const activeContactIsPrefixOnly = activeContact
    ? isPrefixOnlyContact(activeContact.public_key)
    : false;

  const titleClickable =
    (conversation.type === 'contact' && onOpenContactInfo) ||
    (conversation.type === 'channel' && onOpenChannelInfo);
  const isFav =
    conversation.type === 'contact'
      ? (activeContact?.favorite ?? false)
      : conversation.type === 'channel'
        ? (activeChannel?.favorite ?? false)
        : false;
  // MCMP compression opt-in. Offered for regular DMs and channels; not for room
  // servers (their posts route through the room server and have a tighter budget)
  // or repeaters (handled by a separate console).
  const mcmpEnabled =
    conversation.type === 'contact'
      ? (activeContact?.mcmp_enabled ?? false)
      : conversation.type === 'channel'
        ? (activeChannel?.mcmp_enabled ?? false)
        : false;
  const showMcmpToggle =
    !!onSetMcmpEnabled &&
    ((conversation.type === 'contact' && !activeContactIsRoomServer) ||
      conversation.type === 'channel');
  const favoriteTitle =
    conversation.type === 'contact'
      ? isFav
        ? 'Remove from favorites. Favorite contacts stay loaded on the radio for ACK support.'
        : 'Add to favorites. Favorite contacts stay loaded on the radio for ACK support.'
      : isFav
        ? 'Remove from favorites'
        : 'Add to favorites';

  const handleEditFloodScopeOverride = () => {
    if (conversation.type !== 'channel' || !onSetChannelFloodScopeOverride) return;
    setChannelOverrideOpen(true);
  };

  const handleEditPathHashModeOverride = () => {
    if (conversation.type !== 'channel' || !onSetChannelPathHashModeOverride) return;
    setPathHashModeOverrideOpen(true);
  };

  const handleOpenConversationInfo = () => {
    if (conversation.type === 'contact' && onOpenContactInfo) {
      onOpenContactInfo(conversation.id);
      return;
    }
    if (conversation.type === 'channel' && onOpenChannelInfo) {
      onOpenChannelInfo(conversation.id);
    }
  };

  return (
    <header
      className={cn(
        'conversation-header grid items-start gap-x-2 gap-y-0.5 border-b border-border px-4 py-2.5',
        conversation.type === 'contact' && activeContact
          ? 'grid-cols-[minmax(0,1fr)_auto] min-[1100px]:grid-cols-[minmax(0,1fr)_auto_auto]'
          : 'grid-cols-[minmax(0,1fr)_auto]'
      )}
    >
      <span className="flex min-w-0 items-start gap-2">
        {conversation.type === 'contact' && onOpenContactInfo && (
          <button
            type="button"
            className="avatar-action-button flex-shrink-0 cursor-pointer rounded-full border-none bg-transparent p-0 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
            onClick={() => onOpenContactInfo(conversation.id)}
            title="View contact info"
            aria-label={`View info for ${conversation.name}`}
          >
            <ContactAvatar
              name={conversation.name}
              publicKey={conversation.id}
              size={28}
              contactType={contacts.find((c) => c.public_key === conversation.id)?.type}
              clickable
            />
          </button>
        )}
        <span className="flex min-w-0 flex-1 flex-col">
          <span className="flex min-w-0 flex-wrap items-baseline gap-x-2 gap-y-0.5">
            <span className="flex min-w-0 flex-1 items-baseline gap-2 whitespace-nowrap">
              <h2 className="min-w-0 flex-shrink font-semibold text-base">
                {titleClickable ? (
                  <button
                    type="button"
                    className="flex max-w-full min-w-0 items-center gap-1.5 overflow-hidden rounded-sm text-left transition-colors hover:text-primary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                    aria-label={`View info for ${conversation.name}`}
                    onClick={handleOpenConversationInfo}
                  >
                    <span className="truncate">
                      {conversation.type === 'channel' &&
                      !conversation.name.startsWith('#') &&
                      activeChannel?.is_hashtag
                        ? '#'
                        : ''}
                      {conversation.name}
                    </span>
                    <Info
                      className="h-3.5 w-3.5 flex-shrink-0 text-muted-foreground/80"
                      aria-hidden="true"
                    />
                  </button>
                ) : (
                  <span className="truncate">
                    {conversation.type === 'channel' &&
                    !conversation.name.startsWith('#') &&
                    activeChannel?.is_hashtag
                      ? '#'
                      : ''}
                    {conversation.name}
                  </span>
                )}
              </h2>
              {isPrivateChannel && !showKey ? (
                <button
                  className="min-w-0 flex-shrink text-[0.6875rem] font-mono text-muted-foreground transition-colors hover:text-primary"
                  onClick={(e) => {
                    e.stopPropagation();
                    setShowKey(true);
                  }}
                  title="Reveal channel key"
                >
                  Show Key
                </button>
              ) : (
                <span
                  className="min-w-0 flex-1 truncate font-mono text-[0.6875rem] text-muted-foreground transition-colors hover:text-primary"
                  role="button"
                  tabIndex={0}
                  onKeyDown={handleKeyboardActivate}
                  onClick={(e) => {
                    e.stopPropagation();
                    navigator.clipboard.writeText(conversation.id);
                    toast.success(
                      conversation.type === 'channel'
                        ? 'Channel key copied!'
                        : 'Contact key copied!'
                    );
                  }}
                  title="Click to copy"
                  aria-label={
                    conversation.type === 'channel' ? 'Copy channel key' : 'Copy contact key'
                  }
                >
                  {conversation.type === 'channel'
                    ? conversation.id.toLowerCase()
                    : conversation.id}
                </span>
              )}
            </span>
            {conversation.type === 'channel' && activeFloodScopeBadge && (
              <button
                className="mt-0.5 flex basis-full items-center gap-1 text-left sm:hidden"
                onClick={handleEditFloodScopeOverride}
                title="Set regional override"
                aria-label="Set regional override"
              >
                <Globe2
                  className="h-3.5 w-3.5 flex-shrink-0 text-[hsl(var(--region-override))]"
                  aria-hidden="true"
                />
                <span className="min-w-0 truncate text-[0.6875rem] font-medium text-[hsl(var(--region-override))]">
                  {activeFloodScopeBadge}
                </span>
              </button>
            )}
          </span>
        </span>
      </span>
      {conversation.type === 'contact' && activeContact && (
        <div className="col-span-2 row-start-2 min-w-0 text-[0.6875rem] text-muted-foreground min-[1100px]:col-span-1 min-[1100px]:col-start-2 min-[1100px]:row-start-1">
          <ContactStatusInfo
            contact={activeContact}
            ourLat={config?.lat ?? null}
            ourLon={config?.lon ?? null}
          />
        </div>
      )}
      <div className="flex items-center justify-end gap-0.5">
        {conversation.type === 'contact' && !activeContactIsRoomServer && (
          <button
            className="p-1 rounded hover:bg-accent text-lg leading-none transition-colors disabled:cursor-not-allowed disabled:opacity-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
            onClick={() => setPathDiscoveryOpen(true)}
            title={
              activeContactIsPrefixOnly
                ? 'Path Discovery unavailable until the full contact key is known'
                : 'Path Discovery. Send a routed probe and inspect the forward and return paths'
            }
            aria-label="Path Discovery"
            disabled={activeContactIsPrefixOnly}
          >
            <Route className="h-4 w-4 text-muted-foreground" aria-hidden="true" />
          </button>
        )}
        {conversation.type === 'contact' && !activeContactIsRoomServer && (
          <button
            className="p-1 rounded hover:bg-accent text-lg leading-none transition-colors disabled:cursor-not-allowed disabled:opacity-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
            onClick={onTrace}
            title={
              activeContactIsPrefixOnly
                ? 'Direct Trace unavailable until the full contact key is known'
                : 'Direct Trace. Send a direct trace probe to this contact and display out and back SNR'
            }
            aria-label="Direct Trace"
            disabled={activeContactIsPrefixOnly}
          >
            <DirectTraceIcon className="h-4 w-4 text-muted-foreground" />
          </button>
        )}
        {(notificationsSupported ||
          pushSupported ||
          (conversation.type === 'channel' && onToggleMute)) &&
          !activeContactIsRoomServer && (
            <div className="relative" ref={notifDropdownRef}>
              <button
                className="p-1 rounded hover:bg-accent text-lg leading-none transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                onClick={() => setNotifDropdownOpen((v) => !v)}
                title="Notification settings"
                aria-label="Notification settings"
                aria-expanded={notifDropdownOpen}
              >
                {activeChannel?.muted ? (
                  <BellOff className="h-4 w-4 text-muted-foreground" aria-hidden="true" />
                ) : (
                  <Bell
                    className={cn(
                      'h-4 w-4',
                      notificationsEnabled || pushEnabledForConversation
                        ? 'text-primary'
                        : 'text-muted-foreground'
                    )}
                    fill={
                      notificationsEnabled || pushEnabledForConversation ? 'currentColor' : 'none'
                    }
                    aria-hidden="true"
                  />
                )}
              </button>
              {notifDropdownOpen && (
                <div className="absolute right-[-4.5rem] sm:right-0 top-full z-50 mt-1 w-[calc(100vw-2rem)] sm:w-72 max-w-72 rounded-md border border-border bg-popover p-3 shadow-lg space-y-3">
                  {notificationsSupported && (
                    <label className="flex items-start gap-2.5 cursor-pointer group">
                      <input
                        type="checkbox"
                        className="mt-0.5 accent-primary h-4 w-4 shrink-0"
                        checked={notificationsEnabled}
                        disabled={notificationsPermission === 'denied'}
                        onChange={onToggleNotifications}
                      />
                      <div className="min-w-0">
                        <span className="text-sm font-medium text-foreground block leading-tight">
                          Desktop notifications (legacy)
                        </span>
                        <span className="text-xs text-muted-foreground leading-snug block mt-0.5">
                          {notificationsPermission === 'denied'
                            ? 'Blocked by browser — check site permissions'
                            : 'Alerts while this tab is open'}
                        </span>
                      </div>
                    </label>
                  )}
                  {pushSupported && onTogglePush && (
                    <>
                      <label className="flex items-start gap-2.5 cursor-pointer group">
                        <input
                          type="checkbox"
                          className="mt-0.5 accent-primary h-4 w-4 shrink-0"
                          checked={!!pushEnabledForConversation}
                          onChange={onTogglePush}
                        />
                        <div className="min-w-0">
                          <span className="text-sm font-medium text-foreground block leading-tight">
                            Web Push (beta testing)
                          </span>
                          <span className="text-xs text-muted-foreground leading-snug block mt-0.5">
                            {pushSubscribed
                              ? 'Alerts even when the browser is closed'
                              : 'Alerts even when the browser is closed. Requires HTTPS.'}
                          </span>
                        </div>
                      </label>
                      <span className="text-xs text-muted-foreground leading-snug block mt-0.5">
                        All notification types require a trusted HTTPS context. Depending on your
                        browser, a snakeoil certificate may not be sufficient.
                      </span>
                      {onOpenPushSettings && (
                        <p className="text-xs text-muted-foreground leading-snug mt-1.5">
                          Manage Web Push enabled devices in{' '}
                          <button
                            type="button"
                            onClick={() => {
                              setNotifDropdownOpen(false);
                              onOpenPushSettings();
                            }}
                            className="text-primary hover:underline transition-colors focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring"
                          >
                            Settings &rarr; Local
                          </button>
                          .
                        </p>
                      )}
                    </>
                  )}
                  {conversation.type === 'channel' && onToggleMute && (
                    <>
                      <hr className="border-border" />
                      <label className="flex items-start gap-2.5 cursor-pointer group">
                        <input
                          type="checkbox"
                          className="mt-0.5 accent-primary h-4 w-4 shrink-0"
                          checked={!!activeChannel?.muted}
                          onChange={() => onToggleMute(conversation.id)}
                        />
                        <div className="min-w-0">
                          <span className="text-sm font-medium text-foreground block leading-tight">
                            Mute channel
                          </span>
                          <span className="text-xs text-muted-foreground leading-snug block mt-0.5">
                            Hide unread counts and suppress all notifications
                          </span>
                        </div>
                      </label>
                    </>
                  )}
                </div>
              )}
            </div>
          )}
        {conversation.type === 'channel' && onSetChannelFloodScopeOverride && (
          <button
            className="flex shrink-0 items-center gap-1 rounded px-1 py-1 text-lg leading-none transition-colors hover:bg-accent focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
            onClick={handleEditFloodScopeOverride}
            title="Set regional override"
            aria-label="Set regional override"
          >
            <Globe2
              className={`h-4 w-4 ${activeFloodScopeLabel ? 'text-[hsl(var(--region-override))]' : 'text-muted-foreground'}`}
              aria-hidden="true"
            />
            {activeFloodScopeBadge && (
              <span className="hidden text-[0.6875rem] font-medium text-[hsl(var(--region-override))] sm:inline">
                {activeFloodScopeBadge}
              </span>
            )}
          </button>
        )}
        {showPathHashModeOverride && (
          <button
            className="flex shrink-0 items-center gap-1 rounded px-1 py-1 text-lg leading-none transition-colors hover:bg-accent focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
            onClick={handleEditPathHashModeOverride}
            title="Set path hop width override"
            aria-label="Set path hop width override"
          >
            <ChevronsLeftRight
              className={`h-4 w-4 ${activePathHashModeOverride != null ? 'text-status-connected' : 'text-muted-foreground'}`}
              aria-hidden="true"
            />
          </button>
        )}
        {showMcmpToggle && (
          <button
            className="p-1 rounded hover:bg-accent text-lg leading-none transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
            onClick={() =>
              onSetMcmpEnabled?.(
                conversation.type as 'channel' | 'contact',
                conversation.id,
                !mcmpEnabled
              )
            }
            title={
              mcmpEnabled
                ? 'MCMP compression is on — messages are compressed so more text fits in one packet. Turn off if the other side can’t read compressed messages.'
                : 'Compress messages (MCMP) to fit more text per packet. Both sides must support MCMP (meshcore-open / RemoteTerm).'
            }
            aria-label={mcmpEnabled ? 'Disable MCMP compression' : 'Enable MCMP compression'}
            aria-pressed={mcmpEnabled}
          >
            <Shrink
              className={cn('h-4 w-4', mcmpEnabled ? 'text-primary' : 'text-muted-foreground')}
              aria-hidden="true"
            />
          </button>
        )}
        {(conversation.type === 'channel' || conversation.type === 'contact') && (
          <button
            className="p-1 rounded hover:bg-accent text-lg leading-none transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
            onClick={() =>
              onToggleFavorite(conversation.type as 'channel' | 'contact', conversation.id)
            }
            title={favoriteTitle}
            aria-label={isFav ? 'Remove from favorites' : 'Add to favorites'}
          >
            {isFav ? (
              <Star className="h-4 w-4 fill-current text-favorite" aria-hidden="true" />
            ) : (
              <Star className="h-4 w-4 text-muted-foreground" aria-hidden="true" />
            )}
          </button>
        )}
        {!(conversation.type === 'channel' && isPublicChannelKey(conversation.id)) && (
          <button
            className="p-1 rounded hover:bg-destructive/10 text-muted-foreground hover:text-destructive text-lg leading-none transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
            onClick={() => {
              if (conversation.type === 'channel') {
                onDeleteChannel(conversation.id);
              } else {
                onDeleteContact(conversation.id);
              }
            }}
            title="Delete"
            aria-label="Delete"
          >
            <Trash2 className="h-4 w-4" aria-hidden="true" />
          </button>
        )}
      </div>
      {conversation.type === 'contact' && activeContact && (
        <ContactPathDiscoveryModal
          open={pathDiscoveryOpen}
          onClose={() => setPathDiscoveryOpen(false)}
          contact={activeContact}
          contacts={contacts}
          radioName={config?.name ?? null}
          onDiscover={onPathDiscovery}
        />
      )}
      {conversation.type === 'channel' && onSetChannelFloodScopeOverride && (
        <ChannelFloodScopeOverrideModal
          open={channelOverrideOpen}
          onClose={() => setChannelOverrideOpen(false)}
          roomName={conversation.name}
          currentOverride={activeFloodScopeOverride}
          onSetOverride={(value) => onSetChannelFloodScopeOverride(conversation.id, value)}
        />
      )}
      {showPathHashModeOverride && (
        <ChannelPathHashModeOverrideModal
          open={pathHashModeOverrideOpen}
          onClose={() => setPathHashModeOverrideOpen(false)}
          channelName={conversation.name}
          currentOverride={activePathHashModeOverride}
          radioDefault={config?.path_hash_mode ?? 0}
          onSetOverride={(value) => onSetChannelPathHashModeOverride(conversation.id, value)}
        />
      )}
    </header>
  );
}
