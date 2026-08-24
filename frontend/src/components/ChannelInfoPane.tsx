import { useEffect, useMemo, useState } from 'react';
import { PieChart, Pie, Cell, ResponsiveContainer, Tooltip as RechartsTooltip } from 'recharts';
import { Settings2, Star } from 'lucide-react';
import { api } from '../api';
import { formatTime } from '../utils/messageParser';
import { handleKeyboardActivate } from '../utils/a11y';
import { useEntranceSettled } from '../hooks/useEntranceSettled';
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from './ui/sheet';
import { toast } from './ui/sonner';
import { ChannelMcmpSettingsModal } from './ChannelMcmpSettingsModal';
import { ChannelPathHashModeOverrideModal } from './ChannelPathHashModeOverrideModal';
import type { Channel, ChannelDetail, PathHashWidthStats } from '../types';

interface ChannelInfoPaneProps {
  channelKey: string | null;
  onClose: () => void;
  channels: Channel[];
  onToggleFavorite: (type: 'channel' | 'contact', id: string) => void;
  onSetChannelMcmp?: (
    channelKey: string,
    mcmpEnabled: boolean,
    mcmpSignEnabled: boolean
  ) => Promise<void>;
  onSetChannelPathHashModeOverride?: (
    channelKey: string,
    pathHashModeOverride: number | null
  ) => Promise<void>;
}

export function ChannelInfoPane({
  channelKey,
  onClose,
  channels,
  onToggleFavorite,
  onSetChannelMcmp,
  onSetChannelPathHashModeOverride,
}: ChannelInfoPaneProps) {
  const [detail, setDetail] = useState<ChannelDetail | null>(null);
  const [loading, setLoading] = useState(false);
  const [showKey, setShowKey] = useState(false);
  const [showMcmpSettings, setShowMcmpSettings] = useState(false);
  const [showPathHashModeOverride, setShowPathHashModeOverride] = useState(false);

  const liveChannel = channelKey ? (channels.find((c) => c.key === channelKey) ?? null) : null;
  const chartReady = useEntranceSettled(channelKey !== null);

  useEffect(() => {
    setShowKey(false);
    if (!channelKey) {
      setDetail(null);
      return;
    }

    let cancelled = false;
    setLoading(true);
    api
      .getChannelDetail(channelKey)
      .then((data) => {
        if (!cancelled) setDetail(data);
      })
      .catch((err) => {
        if (!cancelled) {
          console.error('Failed to fetch channel detail:', err);
          toast.error('Failed to load channel info');
        }
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [channelKey]);

  const channel = liveChannel ?? detail?.channel ?? null;

  return (
    <Sheet open={channelKey !== null} onOpenChange={(open) => !open && onClose()}>
      <SheetContent side="right" className="w-full sm:max-w-[400px] p-0 flex flex-col">
        <SheetHeader className="sr-only">
          <SheetTitle>Channel Info</SheetTitle>
          <SheetDescription>Channel details and statistics</SheetDescription>
        </SheetHeader>

        {loading && !detail ? (
          <div className="flex-1 flex items-center justify-center text-muted-foreground">
            Loading...
          </div>
        ) : channel ? (
          <div className="flex-1 overflow-y-auto">
            {/* Header */}
            <div className="px-5 pt-5 pb-4 border-b border-border">
              <h2 className="text-lg font-semibold truncate">
                {channel.is_hashtag && !channel.name.startsWith('#')
                  ? `#${channel.name}`
                  : channel.name}
              </h2>
              {!channel.is_hashtag && !showKey ? (
                <button
                  className="text-xs font-mono text-muted-foreground hover:text-primary transition-colors"
                  onClick={() => setShowKey(true)}
                  title="Reveal channel key"
                >
                  Show Key
                </button>
              ) : (
                <span
                  className="text-xs font-mono text-muted-foreground cursor-pointer hover:text-primary transition-colors block truncate"
                  role="button"
                  tabIndex={0}
                  onKeyDown={handleKeyboardActivate}
                  onClick={() => {
                    navigator.clipboard.writeText(channel.key);
                    toast.success('Channel key copied!');
                  }}
                  title="Click to copy"
                >
                  {channel.key.toLowerCase()}
                </span>
              )}
              <div className="flex items-center gap-2 mt-1.5">
                <span className="text-[0.625rem] uppercase tracking-wider px-1.5 py-0.5 rounded bg-muted text-muted-foreground font-medium">
                  {channel.is_hashtag ? 'Hashtag' : 'Private Key'}
                </span>
                {channel.on_radio && (
                  <span className="text-[0.625rem] uppercase tracking-wider px-1.5 py-0.5 rounded bg-primary/10 text-primary font-medium">
                    On Radio
                  </span>
                )}
              </div>
            </div>

            {/* Favorite toggle */}
            <div className="px-5 py-3 border-b border-border">
              <button
                type="button"
                className="text-sm flex items-center gap-2 hover:text-primary transition-colors"
                onClick={() => onToggleFavorite('channel', channel.key)}
              >
                {channel.favorite ? (
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

            {/* MCMP & Routing Settings */}
            {(onSetChannelMcmp || onSetChannelPathHashModeOverride) && (
              <div className="px-5 py-3 border-b border-border space-y-2">
                {onSetChannelMcmp && (
                  <button
                    type="button"
                    className="text-sm flex items-center gap-2 hover:text-primary transition-colors"
                    onClick={() => setShowMcmpSettings(true)}
                  >
                    <Settings2 className="h-4.5 w-4.5 text-muted-foreground" aria-hidden="true" />
                    <span>MCMP Compression</span>
                  </button>
                )}
                {onSetChannelPathHashModeOverride && (
                  <button
                    type="button"
                    className="text-sm flex items-center gap-2 hover:text-primary transition-colors"
                    onClick={() => setShowPathHashModeOverride(true)}
                  >
                    <Settings2 className="h-4.5 w-4.5 text-muted-foreground" aria-hidden="true" />
                    <span>Path Hop Width Override</span>
                  </button>
                )}
              </div>
            )}

            {/* Message Activity */}
            {detail && detail.message_counts.all_time > 0 && (
              <div className="px-5 py-3 border-b border-border">
                <SectionLabel>Message Activity</SectionLabel>
                <div className="grid grid-cols-2 gap-x-4 gap-y-1 text-sm">
                  <InfoItem
                    label="Last Hour"
                    value={detail.message_counts.last_1h.toLocaleString()}
                  />
                  <InfoItem
                    label="Last 24h"
                    value={detail.message_counts.last_24h.toLocaleString()}
                  />
                  <InfoItem
                    label="Last 48h"
                    value={detail.message_counts.last_48h.toLocaleString()}
                  />
                  <InfoItem
                    label="Last 7d"
                    value={detail.message_counts.last_7d.toLocaleString()}
                  />
                  <InfoItem
                    label="All Time"
                    value={detail.message_counts.all_time.toLocaleString()}
                  />
                  <InfoItem
                    label="Unique Senders"
                    value={detail.unique_sender_count.toLocaleString()}
                  />
                </div>
              </div>
            )}

            {/* First Message */}
            {detail && detail.first_message_at && (
              <div className="px-5 py-3 border-b border-border">
                <SectionLabel>First Message</SectionLabel>
                <p className="text-sm font-medium">{formatTime(detail.first_message_at)}</p>
              </div>
            )}

            {/* Hop Byte Widths (24h) */}
            {detail && detail.path_hash_width_24h.total_packets > 0 && (
              <div className="px-5 py-3 border-b border-border">
                <SectionLabel>Hop Byte Widths (24h)</SectionLabel>
                <HopWidthChart stats={detail.path_hash_width_24h} ready={chartReady} />
              </div>
            )}

            {/* Top Senders 24h */}
            {detail && detail.top_senders_24h.length > 0 && (
              <div className="px-5 py-3">
                <SectionLabel>Top Senders (24h)</SectionLabel>
                <div className="space-y-1">
                  {detail.top_senders_24h.map((sender, idx) => (
                    <div
                      key={sender.sender_key ?? idx}
                      className="flex justify-between items-center text-sm"
                    >
                      <span className="truncate">{sender.sender_name}</span>
                      <span className="text-xs text-muted-foreground flex-shrink-0 ml-2">
                        {sender.message_count.toLocaleString()} msg
                        {sender.message_count !== 1 ? 's' : ''}
                      </span>
                    </div>
                  ))}
                </div>
              </div>
            )}
          </div>
        ) : (
          <div className="flex-1 flex items-center justify-center text-muted-foreground">
            Channel not found
          </div>
        )}
      </SheetContent>

      {/* Settings modals */}
      {channel && onSetChannelMcmp && (
        <ChannelMcmpSettingsModal
          open={showMcmpSettings}
          onClose={() => setShowMcmpSettings(false)}
          channel={channel}
          onSave={async (mcmpEnabled, mcmpSignEnabled) => {
            await onSetChannelMcmp(channel.key, mcmpEnabled, mcmpSignEnabled);
            setShowMcmpSettings(false);
          }}
        />
      )}
      {channel && onSetChannelPathHashModeOverride && (
        <ChannelPathHashModeOverrideModal
          open={showPathHashModeOverride}
          onClose={() => setShowPathHashModeOverride(false)}
          channelName={channel.name}
          currentOverride={channel.path_hash_mode_override ?? null}
          radioDefault={0}  // Заглушка, если нет config; в будущем можно передавать реальное значение
          onSetOverride={(value) => {
            onSetChannelPathHashModeOverride(channel.key, value);
            setShowPathHashModeOverride(false);
          }}
        />
      )}
    </Sheet>
  );
}

// ... остальные функции без изменений ...