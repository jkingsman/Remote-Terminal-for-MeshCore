import { type ReactNode, useEffect, useState } from 'react';
import { Ban, Search, Star } from 'lucide-react';
import { api } from '../api';
import { formatTime } from '../utils/messageParser';
import {
  getContactDisplayName,
  isPrefixOnlyContact,
  isUnknownFullKeyContact,
} from '../utils/pubkey';
import {
  isValidLocation,
  calculateDistance,
  formatDistance,
  formatRouteLabel,
  getEffectiveContactRoute,
  hasRoutingOverride,
  parsePathHops,
} from '../utils/pathUtils';
import { getMapFocusHash } from '../utils/urlHash';
import { isFavorite } from '../utils/favorites';
import { handleKeyboardActivate } from '../utils/a11y';
import { ContactAvatar } from './ContactAvatar';
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from './ui/sheet';
import { toast } from './ui/sonner';
import type {
  Contact,
  ContactActiveRoom,
  ContactAnalytics,
  ContactAnalyticsHourlyBucket,
  ContactAnalyticsWeeklyBucket,
  Favorite,
  RadioConfig,
} from '../types';

const CONTACT_TYPE_LABELS: Record<number, string> = {
  0: 'Unknown',
  1: 'Client',
  2: 'Repeater',
  3: 'Room',
  4: 'Sensor',
};

function formatPathHashMode(mode: number): string | null {
  if (mode < 0 || mode > 2) {
    return null;
  }
  return `${mode + 1}-byte IDs`;
}

interface ContactInfoPaneProps {
  contactKey: string | null;
  fromChannel?: boolean;
  onClose: () => void;
  contacts: Contact[];
  config: RadioConfig | null;
  favorites: Favorite[];
  onToggleFavorite: (type: 'channel' | 'contact', id: string) => void;
  onNavigateToChannel?: (channelKey: string) => void;
  onSearchMessagesByKey?: (publicKey: string) => void;
  onSearchMessagesByName?: (name: string) => void;
  blockedKeys?: string[];
  blockedNames?: string[];
  onToggleBlockedKey?: (key: string) => void;
  onToggleBlockedName?: (name: string) => void;
}

export function ContactInfoPane({
  contactKey,
  fromChannel = false,
  onClose,
  contacts,
  config,
  favorites,
  onToggleFavorite,
  onNavigateToChannel,
  onSearchMessagesByKey,
  onSearchMessagesByName,
  blockedKeys = [],
  blockedNames = [],
  onToggleBlockedKey,
  onToggleBlockedName,
}: ContactInfoPaneProps) {
  const isNameOnly = contactKey?.startsWith('name:') ?? false;
  const nameOnlyValue = isNameOnly && contactKey ? contactKey.slice(5) : null;

  const [analytics, setAnalytics] = useState<ContactAnalytics | null>(null);
  const [loading, setLoading] = useState(false);

  // Get live contact data from contacts array (real-time via WS)
  const liveContact =
    contactKey && !isNameOnly ? (contacts.find((c) => c.public_key === contactKey) ?? null) : null;

  useEffect(() => {
    if (!contactKey) {
      setAnalytics(null);
      return;
    }

    let cancelled = false;
    setAnalytics(null);
    setLoading(true);
    const request =
      isNameOnly && nameOnlyValue
        ? api.getContactAnalytics({ name: nameOnlyValue })
        : api.getContactAnalytics({ publicKey: contactKey });

    request
      .then((data) => {
        if (!cancelled) setAnalytics(data);
      })
      .catch((err) => {
        if (!cancelled) {
          console.error('Failed to fetch contact analytics:', err);
          toast.error('Failed to load contact info');
        }
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [contactKey, isNameOnly, nameOnlyValue]);

  // Use live contact data where available, fall back to analytics snapshot
  const contact = liveContact ?? analytics?.contact ?? null;

  const distFromUs =
    contact &&
    config &&
    isValidLocation(config.lat, config.lon) &&
    isValidLocation(contact.lat, contact.lon)
      ? calculateDistance(config.lat, config.lon, contact.lat, contact.lon)
      : null;
  const effectiveRoute = contact ? getEffectiveContactRoute(contact) : null;
  const pathHashModeLabel =
    effectiveRoute && effectiveRoute.pathLen >= 0
      ? formatPathHashMode(effectiveRoute.pathHashMode)
      : null;
  const learnedRouteLabel = contact ? formatRouteLabel(contact.last_path_len, true) : null;
  const isPrefixOnlyResolvedContact = contact ? isPrefixOnlyContact(contact.public_key) : false;
  const isUnknownFullKeyResolvedContact =
    contact !== null &&
    !isPrefixOnlyResolvedContact &&
    isUnknownFullKeyContact(contact.public_key, contact.last_advert);

  return (
    <Sheet open={contactKey !== null} onOpenChange={(open) => !open && onClose()}>
      <SheetContent side="right" className="w-full sm:max-w-[400px] p-0 flex flex-col">
        <SheetHeader className="sr-only">
          <SheetTitle>Contact Info</SheetTitle>
          <SheetDescription>Contact details and actions</SheetDescription>
        </SheetHeader>

        {isNameOnly && nameOnlyValue ? (
          <div className="flex-1 overflow-y-auto">
            {/* Name-only header */}
            <div className="px-5 pt-5 pb-4 border-b border-border">
              <div className="flex items-start gap-4">
                <ContactAvatar
                  name={analytics?.name ?? nameOnlyValue}
                  publicKey={`name:${nameOnlyValue}`}
                  size={56}
                />
                <div className="flex-1 min-w-0">
                  <h2 className="text-lg font-semibold truncate">
                    {analytics?.name ?? nameOnlyValue}
                  </h2>
                  <p className="text-xs text-muted-foreground mt-1">
                    We have not heard an advertisement associated with this name, so we cannot
                    identify their key.
                  </p>
                </div>
              </div>
            </div>

            {/* Block by name toggle */}
            {onToggleBlockedName && (
              <div className="px-5 py-3 border-b border-border">
                <button
                  type="button"
                  className="text-sm flex items-center gap-2 hover:text-primary transition-colors"
                  onClick={() => onToggleBlockedName(nameOnlyValue)}
                >
                  {blockedNames.includes(nameOnlyValue) ? (
                    <>
                      <Ban className="h-4.5 w-4.5 text-destructive" aria-hidden="true" />
                      <span>Unblock this name</span>
                    </>
                  ) : (
                    <>
                      <Ban className="h-4.5 w-4.5 text-muted-foreground" aria-hidden="true" />
                      <span>Block this name</span>
                    </>
                  )}
                </button>
              </div>
            )}

            {onSearchMessagesByName && (
              <div className="px-5 py-3 border-b border-border">
                <button
                  type="button"
                  className="text-sm flex items-center gap-2 hover:text-primary transition-colors"
                  onClick={() => onSearchMessagesByName(nameOnlyValue)}
                >
                  <Search className="h-4.5 w-4.5 text-muted-foreground" aria-hidden="true" />
                  <span>Search user&apos;s messages by name</span>
                </button>
              </div>
            )}

            {fromChannel && (
              <ChannelAttributionWarning
                nameOnly
                includeAliasNote={false}
                className="border-b border-border mx-0 my-0 rounded-none px-5 py-3"
              />
            )}

            <MessageStatsSection
              dmMessageCount={0}
              channelMessageCount={analytics?.channel_message_count ?? 0}
              showDirectMessages={false}
            />

            {analytics?.name_first_seen_at && (
              <div className="px-5 py-3 border-b border-border">
                <div className="grid grid-cols-2 gap-x-4 gap-y-1 text-sm">
                  <InfoItem
                    label="Name First In Use"
                    value={formatTime(analytics.name_first_seen_at)}
                  />
                </div>
              </div>
            )}

            <ActivityChartsSection analytics={analytics} />

            <MostActiveRoomsSection
              rooms={analytics?.most_active_rooms ?? []}
              onNavigateToChannel={onNavigateToChannel}
            />
          </div>
        ) : loading && !analytics && !contact ? (
          <div className="flex-1 flex items-center justify-center text-muted-foreground">
            Loading...
          </div>
        ) : contact ? (
          <div className="flex-1 overflow-y-auto">
            {/* Header */}
            <div className="px-5 pt-5 pb-4 border-b border-border">
              <div className="flex items-start gap-4">
                <ContactAvatar
                  name={contact.name}
                  publicKey={contact.public_key}
                  size={56}
                  contactType={contact.type}
                />
                <div className="flex-1 min-w-0">
                  <h2 className="text-lg font-semibold truncate">
                    {getContactDisplayName(contact.name, contact.public_key, contact.last_advert)}
                  </h2>
                  <span
                    className="text-xs font-mono text-muted-foreground cursor-pointer hover:text-primary transition-colors block truncate"
                    role="button"
                    tabIndex={0}
                    onKeyDown={handleKeyboardActivate}
                    onClick={() => {
                      navigator.clipboard.writeText(contact.public_key);
                      toast.success('Public key copied!');
                    }}
                    title="Click to copy"
                  >
                    {contact.public_key}
                  </span>
                  <div className="flex items-center gap-2 mt-1.5">
                    <span className="text-[10px] uppercase tracking-wider px-1.5 py-0.5 rounded bg-muted text-muted-foreground font-medium">
                      {CONTACT_TYPE_LABELS[contact.type] ?? 'Unknown'}
                    </span>
                  </div>
                </div>
              </div>
            </div>

            {isPrefixOnlyResolvedContact && (
              <div className="mx-5 mt-4 rounded-md border border-destructive/30 bg-destructive/10 px-3 py-2 text-sm text-destructive">
                We only know a key prefix for this sender, which can happen when a fallback DM
                arrives before we hear an advertisement. This contact stays read-only until the full
                key resolves from a later advertisement.
              </div>
            )}

            {isUnknownFullKeyResolvedContact && (
              <div className="mx-5 mt-4 rounded-md border border-warning/30 bg-warning/10 px-3 py-2 text-sm text-warning">
                We know this sender&apos;s full key, but we have not yet heard an advertisement that
                fills in their identity details. Those details will appear automatically when an
                advertisement arrives.
              </div>
            )}

            {/* Info grid */}
            <div className="px-5 py-3 border-b border-border">
              <div className="grid grid-cols-2 gap-x-4 gap-y-2 text-sm">
                {contact.last_seen && (
                  <InfoItem label="Last Seen" value={formatTime(contact.last_seen)} />
                )}
                {contact.first_seen && (
                  <InfoItem label="First Heard" value={formatTime(contact.first_seen)} />
                )}
                {contact.last_contacted && (
                  <InfoItem label="Last Contacted" value={formatTime(contact.last_contacted)} />
                )}
                {distFromUs !== null && (
                  <InfoItem label="Distance" value={formatDistance(distFromUs)} />
                )}
                {effectiveRoute && (
                  <InfoItem
                    label="Routing"
                    value={
                      effectiveRoute.forced ? (
                        <span>
                          {formatRouteLabel(effectiveRoute.pathLen, true)}{' '}
                          <span className="text-destructive">(forced)</span>
                        </span>
                      ) : (
                        formatRouteLabel(effectiveRoute.pathLen, true)
                      )
                    }
                  />
                )}
                {contact && hasRoutingOverride(contact) && learnedRouteLabel && (
                  <InfoItem label="Learned Route" value={learnedRouteLabel} />
                )}
                {pathHashModeLabel && <InfoItem label="Hop Width" value={pathHashModeLabel} />}
              </div>
            </div>

            {/* GPS */}
            {isValidLocation(contact.lat, contact.lon) && (
              <div className="px-5 py-3 border-b border-border">
                <SectionLabel>Location</SectionLabel>
                <span
                  className="text-sm font-mono cursor-pointer hover:text-primary hover:underline transition-colors"
                  role="button"
                  tabIndex={0}
                  onKeyDown={handleKeyboardActivate}
                  onClick={() => {
                    const url =
                      window.location.origin +
                      window.location.pathname +
                      getMapFocusHash(contact.public_key);
                    window.open(url, '_blank');
                  }}
                  title="View on map"
                >
                  {contact.lat!.toFixed(5)}, {contact.lon!.toFixed(5)}
                </span>
              </div>
            )}

            {/* Favorite toggle */}
            <div className="px-5 py-3 border-b border-border">
              <button
                type="button"
                className="text-sm flex items-center gap-2 hover:text-primary transition-colors"
                onClick={() => onToggleFavorite('contact', contact.public_key)}
                title="Favorite contacts stay loaded on the radio for ACK support"
              >
                {isFavorite(favorites, 'contact', contact.public_key) ? (
                  <>
                    <Star className="h-4.5 w-4.5 fill-current text-favorite" aria-hidden="true" />
                    <span>Remove from favorites</span>
                  </>
                ) : (
                  <>
                    <Star className="h-4.5 w-4.5 text-muted-foreground" aria-hidden="true" />
                    <span>Add to favorites</span>
                  </>
                )}
              </button>
            </div>

            {/* Block toggles */}
            {(onToggleBlockedKey || onToggleBlockedName) && (
              <div className="px-5 py-3 border-b border-border space-y-2">
                {onToggleBlockedKey && (
                  <button
                    type="button"
                    className="text-sm flex items-center gap-2 hover:text-primary transition-colors"
                    onClick={() => onToggleBlockedKey(contact.public_key)}
                  >
                    {blockedKeys.includes(contact.public_key.toLowerCase()) ? (
                      <>
                        <Ban className="h-4.5 w-4.5 text-destructive" aria-hidden="true" />
                        <span>Unblock this key</span>
                      </>
                    ) : (
                      <>
                        <Ban className="h-4.5 w-4.5 text-muted-foreground" aria-hidden="true" />
                        <span>Block this key</span>
                      </>
                    )}
                  </button>
                )}
                {onToggleBlockedName && contact.name && (
                  <button
                    type="button"
                    className="text-sm flex items-center gap-2 hover:text-primary transition-colors"
                    onClick={() => onToggleBlockedName(contact.name!)}
                  >
                    {blockedNames.includes(contact.name) ? (
                      <>
                        <Ban className="h-4.5 w-4.5 text-destructive" aria-hidden="true" />
                        <span>Unblock name &ldquo;{contact.name}&rdquo;</span>
                      </>
                    ) : (
                      <>
                        <Ban className="h-4.5 w-4.5 text-muted-foreground" aria-hidden="true" />
                        <span>Block name &ldquo;{contact.name}&rdquo;</span>
                      </>
                    )}
                  </button>
                )}
              </div>
            )}

            {onSearchMessagesByKey && (
              <div className="px-5 py-3 border-b border-border">
                <button
                  type="button"
                  className="text-sm flex items-center gap-2 hover:text-primary transition-colors"
                  onClick={() => onSearchMessagesByKey(contact.public_key)}
                >
                  <Search className="h-4.5 w-4.5 text-muted-foreground" aria-hidden="true" />
                  <span>Search user&apos;s messages by key</span>
                </button>
              </div>
            )}

            {/* Nearest Repeaters */}
            {analytics && analytics.nearest_repeaters.length > 0 && (
              <div className="px-5 py-3 border-b border-border">
                <SectionLabel>Nearest Repeaters</SectionLabel>
                <div className="space-y-1">
                  {analytics.nearest_repeaters.map((r) => (
                    <div key={r.public_key} className="flex justify-between items-center text-sm">
                      <span className="truncate">{r.name || r.public_key.slice(0, 12)}</span>
                      <span className="text-xs text-muted-foreground flex-shrink-0 ml-2">
                        {r.path_len === 0
                          ? 'direct'
                          : `${r.path_len} hop${r.path_len > 1 ? 's' : ''}`}{' '}
                        · {r.heard_count}x
                      </span>
                    </div>
                  ))}
                </div>
              </div>
            )}

            {/* Advert Paths */}
            {analytics && analytics.advert_paths.length > 0 && (
              <div className="px-5 py-3 border-b border-border">
                <SectionLabel>Recent Advert Paths</SectionLabel>
                <div className="space-y-1">
                  {analytics.advert_paths.map((p) => (
                    <div
                      key={p.path + p.first_seen}
                      className="flex justify-between items-center text-sm"
                    >
                      <span className="font-mono text-xs truncate">
                        {p.path ? parsePathHops(p.path, p.path_len).join(' → ') : '(direct)'}
                      </span>
                      <span className="text-xs text-muted-foreground flex-shrink-0 ml-2">
                        {p.heard_count}x · {formatTime(p.last_seen)}
                      </span>
                    </div>
                  ))}
                </div>
              </div>
            )}

            {fromChannel && (
              <ChannelAttributionWarning
                includeAliasNote={Boolean(analytics && analytics.name_history.length > 1)}
              />
            )}

            {/* AKA (Name History) - only show if more than one name */}
            {analytics && analytics.name_history.length > 1 && (
              <div className="px-5 py-3 border-b border-border">
                <SectionLabel>Also Known As</SectionLabel>
                <div className="space-y-1">
                  {analytics.name_history.map((h) => (
                    <div key={h.name} className="flex justify-between items-center text-sm">
                      <span className="font-medium truncate">{h.name}</span>
                      <span className="text-xs text-muted-foreground flex-shrink-0 ml-2">
                        {formatTime(h.first_seen)} &ndash; {formatTime(h.last_seen)}
                      </span>
                    </div>
                  ))}
                </div>
              </div>
            )}

            <MessageStatsSection
              dmMessageCount={analytics?.dm_message_count ?? 0}
              channelMessageCount={analytics?.channel_message_count ?? 0}
            />

            <ActivityChartsSection analytics={analytics} />

            <MostActiveRoomsSection
              rooms={analytics?.most_active_rooms ?? []}
              onNavigateToChannel={onNavigateToChannel}
            />
          </div>
        ) : (
          <div className="flex-1 flex items-center justify-center text-muted-foreground">
            Contact not found
          </div>
        )}
      </SheetContent>
    </Sheet>
  );
}

function SectionLabel({ children }: { children: React.ReactNode }) {
  return (
    <h3 className="text-[10px] uppercase tracking-wider text-muted-foreground font-medium mb-1.5">
      {children}
    </h3>
  );
}

function ChannelAttributionWarning({
  includeAliasNote = false,
  nameOnly = false,
  className = 'mx-5 my-3 px-3 py-2 rounded-md bg-warning/10 border border-warning/20',
}: {
  includeAliasNote?: boolean;
  nameOnly?: boolean;
  className?: string;
}) {
  return (
    <div className={className}>
      <p className="text-xs text-warning">
        Channel sender identity is based on best-effort name matching. Different nodes using the
        same name will be attributed to the same {nameOnly ? 'sender name' : 'contact'}. Stats below
        may be inaccurate.
        {includeAliasNote &&
          ' Historical counts below may include messages previously attributed under names shown in Also Known As.'}
      </p>
    </div>
  );
}

function MessageStatsSection({
  dmMessageCount,
  channelMessageCount,
  showDirectMessages = true,
}: {
  dmMessageCount: number;
  channelMessageCount: number;
  showDirectMessages?: boolean;
}) {
  if ((showDirectMessages ? dmMessageCount : 0) <= 0 && channelMessageCount <= 0) {
    return null;
  }

  return (
    <div className="px-5 py-3 border-b border-border">
      <SectionLabel>Messages</SectionLabel>
      <div className="grid grid-cols-2 gap-x-4 gap-y-1 text-sm">
        {showDirectMessages && dmMessageCount > 0 && (
          <InfoItem label="Direct Messages" value={dmMessageCount.toLocaleString()} />
        )}
        {channelMessageCount > 0 && (
          <InfoItem label="Channel Messages" value={channelMessageCount.toLocaleString()} />
        )}
      </div>
    </div>
  );
}

function MostActiveRoomsSection({
  rooms,
  onNavigateToChannel,
}: {
  rooms: ContactActiveRoom[];
  onNavigateToChannel?: (channelKey: string) => void;
}) {
  if (rooms.length === 0) {
    return null;
  }

  return (
    <div className="px-5 py-3 border-b border-border">
      <SectionLabel>Most Active Rooms</SectionLabel>
      <div className="space-y-1">
        {rooms.map((room) => (
          <div key={room.channel_key} className="flex justify-between items-center text-sm">
            <span
              className={
                onNavigateToChannel
                  ? 'cursor-pointer hover:text-primary transition-colors truncate'
                  : 'truncate'
              }
              role={onNavigateToChannel ? 'button' : undefined}
              tabIndex={onNavigateToChannel ? 0 : undefined}
              onKeyDown={onNavigateToChannel ? handleKeyboardActivate : undefined}
              onClick={() => onNavigateToChannel?.(room.channel_key)}
            >
              {room.channel_name.startsWith('#') || room.channel_name === 'Public'
                ? room.channel_name
                : `#${room.channel_name}`}
            </span>
            <span className="text-xs text-muted-foreground flex-shrink-0 ml-2">
              {room.message_count.toLocaleString()} msg
              {room.message_count !== 1 ? 's' : ''}
            </span>
          </div>
        ))}
      </div>
    </div>
  );
}

function ActivityChartsSection({ analytics }: { analytics: ContactAnalytics | null }) {
  if (!analytics) {
    return null;
  }

  const hasHourlyActivity = analytics.hourly_activity.some(
    (bucket) =>
      bucket.last_24h_count > 0 || bucket.last_week_average > 0 || bucket.all_time_average > 0
  );
  const hasWeeklyActivity = analytics.weekly_activity.some((bucket) => bucket.message_count > 0);
  if (!hasHourlyActivity && !hasWeeklyActivity) {
    return null;
  }

  return (
    <div className="px-5 py-3 border-b border-border space-y-4">
      {hasHourlyActivity && (
        <div>
          <SectionLabel>Messages Per Hour</SectionLabel>
          <ChartLegend
            items={[
              { label: 'Last 24h', color: '#2563eb' },
              { label: '7-day avg', color: '#ea580c' },
              { label: 'All-time avg', color: '#64748b' },
            ]}
          />
          <ActivityLineChart
            ariaLabel="Messages per hour"
            points={analytics.hourly_activity}
            series={[
              { key: 'last_24h_count', color: '#2563eb' },
              { key: 'last_week_average', color: '#ea580c' },
              { key: 'all_time_average', color: '#64748b' },
            ]}
            valueFormatter={(value) => value.toFixed(value % 1 === 0 ? 0 : 1)}
            tickFormatter={(bucket) =>
              new Date(bucket.bucket_start * 1000).toLocaleTimeString([], {
                hour: '2-digit',
                minute: '2-digit',
                hour12: false,
              })
            }
          />
        </div>
      )}

      {hasWeeklyActivity && (
        <div>
          <SectionLabel>Messages Per Week</SectionLabel>
          <ActivityLineChart
            ariaLabel="Messages per week"
            points={analytics.weekly_activity}
            series={[{ key: 'message_count', color: '#16a34a' }]}
            valueFormatter={(value) => value.toFixed(0)}
            tickFormatter={(bucket) =>
              new Date(bucket.bucket_start * 1000).toLocaleDateString([], {
                month: 'short',
                day: 'numeric',
              })
            }
          />
        </div>
      )}

      <p className="text-[11px] text-muted-foreground">
        Hourly lines compare the last 24 hours against 7-day and all-time averages for the same hour
        slots.
        {!analytics.includes_direct_messages &&
          ' Name-only analytics include channel messages only.'}
      </p>
    </div>
  );
}

function ChartLegend({ items }: { items: Array<{ label: string; color: string }> }) {
  return (
    <div className="flex flex-wrap gap-x-3 gap-y-1 mb-2 text-[11px] text-muted-foreground">
      {items.map((item) => (
        <span key={item.label} className="inline-flex items-center gap-1.5">
          <span
            className="inline-block h-2 w-2 rounded-full"
            style={{ backgroundColor: item.color }}
            aria-hidden="true"
          />
          {item.label}
        </span>
      ))}
    </div>
  );
}

function ActivityLineChart<T extends ContactAnalyticsHourlyBucket | ContactAnalyticsWeeklyBucket>({
  ariaLabel,
  points,
  series,
  tickFormatter,
  valueFormatter,
}: {
  ariaLabel: string;
  points: T[];
  series: Array<{ key: keyof T; color: string }>;
  tickFormatter: (point: T) => string;
  valueFormatter: (value: number) => string;
}) {
  const width = 320;
  const height = 132;
  const padding = { top: 8, right: 8, bottom: 24, left: 32 };
  const plotWidth = width - padding.left - padding.right;
  const plotHeight = height - padding.top - padding.bottom;
  const allValues = points.flatMap((point) =>
    series.map((entry) => {
      const value = point[entry.key];
      return typeof value === 'number' ? value : 0;
    })
  );
  const maxValue = Math.max(1, ...allValues);
  const tickIndices = Array.from(
    new Set([
      0,
      Math.floor((points.length - 1) / 3),
      Math.floor(((points.length - 1) * 2) / 3),
      points.length - 1,
    ])
  );

  const buildPolyline = (key: keyof T) =>
    points
      .map((point, index) => {
        const rawValue = point[key];
        const value = typeof rawValue === 'number' ? rawValue : 0;
        const x =
          padding.left + (points.length === 1 ? 0 : (index / (points.length - 1)) * plotWidth);
        const y = padding.top + plotHeight - (value / maxValue) * plotHeight;
        return `${x},${y}`;
      })
      .join(' ');

  return (
    <div>
      <svg
        viewBox={`0 0 ${width} ${height}`}
        className="w-full h-auto"
        role="img"
        aria-label={ariaLabel}
      >
        {[0, 0.5, 1].map((ratio) => {
          const y = padding.top + plotHeight - ratio * plotHeight;
          const value = maxValue * ratio;
          return (
            <g key={ratio}>
              <line
                x1={padding.left}
                x2={width - padding.right}
                y1={y}
                y2={y}
                stroke="hsl(var(--border))"
                strokeWidth="1"
              />
              <text
                x={padding.left - 6}
                y={y + 4}
                fontSize="10"
                textAnchor="end"
                fill="hsl(var(--muted-foreground))"
              >
                {valueFormatter(value)}
              </text>
            </g>
          );
        })}

        {series.map((entry) => (
          <polyline
            key={String(entry.key)}
            fill="none"
            stroke={entry.color}
            strokeWidth="2"
            strokeLinejoin="round"
            strokeLinecap="round"
            points={buildPolyline(entry.key)}
          />
        ))}

        {tickIndices.map((index) => {
          const point = points[index];
          const x =
            padding.left + (points.length === 1 ? 0 : (index / (points.length - 1)) * plotWidth);
          return (
            <text
              key={`${ariaLabel}-${point.bucket_start}`}
              x={x}
              y={height - 6}
              fontSize="10"
              textAnchor={index === 0 ? 'start' : index === points.length - 1 ? 'end' : 'middle'}
              fill="hsl(var(--muted-foreground))"
            >
              {tickFormatter(point)}
            </text>
          );
        })}
      </svg>
    </div>
  );
}

function InfoItem({ label, value }: { label: string; value: ReactNode }) {
  return (
    <div>
      <span className="text-muted-foreground text-xs">{label}</span>
      <p className="font-medium text-sm leading-tight">{value}</p>
    </div>
  );
}
