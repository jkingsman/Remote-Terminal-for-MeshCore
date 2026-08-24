import { useEffect, useMemo, useState } from 'react';
import { Plus } from 'lucide-react';

import { api } from '../../api';
import type { BotFeed, Channel } from '../../types';
import { Button } from '../ui/button';
import { Input } from '../ui/input';
import { toast } from '../ui/sonner';
import { cn } from '@/lib/utils';

const DEFAULT_FORMAT = '{title|truncate:120}\n{link}';

const INTERVAL_OPTIONS = [
  { value: 300, label: '5m' },
  { value: 900, label: '15m' },
  { value: 1800, label: '30m' },
  { value: 3600, label: '1h' },
  { value: 7200, label: '2h' },
  { value: 21600, label: '6h' },
];

function humanizeInterval(seconds: number): string {
  const preset = INTERVAL_OPTIONS.find((o) => o.value === seconds);
  if (preset) return preset.label;
  if (seconds < 3600) return `${Math.round(seconds / 60)}m`;
  return `${Math.round(seconds / 3600)}h`;
}

/** RSS / JSON-API feed subscriptions that post new items to a channel. */
export function FeedsTab({ channels }: { channels: Channel[] }) {
  const [feeds, setFeeds] = useState<BotFeed[]>([]);
  const [loading, setLoading] = useState(true);
  const [showForm, setShowForm] = useState(false);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [confirmDeleteId, setConfirmDeleteId] = useState<string | null>(null);

  // Form state
  const [name, setName] = useState('');
  const [feedType, setFeedType] = useState<'rss' | 'api'>('rss');
  const [url, setUrl] = useState('');
  const [channelKey, setChannelKey] = useState('');
  const [intervalSeconds, setIntervalSeconds] = useState(1800);
  const [format, setFormat] = useState(DEFAULT_FORMAT);
  const [itemsPath, setItemsPath] = useState('');
  const [maxPosts, setMaxPosts] = useState('3');
  const [saving, setSaving] = useState(false);
  const [testing, setTesting] = useState(false);
  const [testResult, setTestResult] = useState<{ item_count: number; preview: string[] } | null>(
    null
  );

  const targetChannels = useMemo(() => channels.filter((c) => c.key), [channels]);

  useEffect(() => {
    api
      .getBotFeeds()
      .then(setFeeds)
      .catch((err: unknown) =>
        toast.error('Failed to load feeds', {
          description: err instanceof Error ? err.message : undefined,
        })
      )
      .finally(() => setLoading(false));
  }, []);

  const activeCount = feeds.filter((f) => f.enabled).length;
  const itemsPosted = feeds.reduce((sum, f) => sum + f.items_posted, 0);
  const erroringCount = feeds.filter((f) => f.error_count > 0).length;

  const resetForm = () => {
    setEditingId(null);
    setName('');
    setFeedType('rss');
    setUrl('');
    setChannelKey('');
    setIntervalSeconds(1800);
    setFormat(DEFAULT_FORMAT);
    setItemsPath('');
    setMaxPosts('3');
    setTestResult(null);
  };

  const loadIntoForm = (feed: BotFeed) => {
    setEditingId(feed.id);
    setName(feed.name);
    setFeedType(feed.feed_type);
    setUrl(feed.url);
    setChannelKey(feed.channel_key);
    setIntervalSeconds(feed.interval_seconds);
    setFormat(feed.format);
    setItemsPath(feed.items_path ?? '');
    setMaxPosts(String(feed.max_posts_per_check));
    setTestResult(null);
    setShowForm(true);
  };

  const handleTestFetch = async () => {
    if (!url.trim()) {
      toast.error('Enter a URL to test');
      return;
    }
    setTesting(true);
    setTestResult(null);
    try {
      const result = await api.testBotFeed({
        url: url.trim(),
        feed_type: feedType,
        items_path: feedType === 'api' && itemsPath.trim() ? itemsPath.trim() : null,
        format,
      });
      setTestResult(result);
    } catch (err) {
      toast.error('Test fetch failed', {
        description: err instanceof Error ? err.message : undefined,
      });
    } finally {
      setTesting(false);
    }
  };

  const handleSave = async () => {
    if (!name.trim() || !url.trim() || !channelKey) {
      toast.error('Name, URL and channel are required');
      return;
    }
    setSaving(true);
    const body = {
      name: name.trim(),
      feed_type: feedType,
      url: url.trim(),
      channel_key: channelKey,
      interval_seconds: intervalSeconds,
      format,
      items_path: feedType === 'api' && itemsPath.trim() ? itemsPath.trim() : null,
      max_posts_per_check: parseInt(maxPosts, 10) || 3,
    };
    try {
      if (editingId) {
        const updated = await api.updateBotFeed(editingId, body);
        setFeeds((prev) => prev.map((f) => (f.id === editingId ? updated : f)));
        toast.success(`${updated.name} saved`);
      } else {
        const created = await api.createBotFeed(body);
        setFeeds((prev) => [...prev, created]);
        toast.success(`${created.name} added`);
      }
      resetForm();
      setShowForm(false);
    } catch (err) {
      toast.error('Failed to save feed', {
        description: err instanceof Error ? err.message : undefined,
      });
    } finally {
      setSaving(false);
    }
  };

  const handleToggleEnabled = async (feed: BotFeed) => {
    try {
      const updated = await api.updateBotFeed(feed.id, { enabled: !feed.enabled });
      setFeeds((prev) => prev.map((f) => (f.id === feed.id ? updated : f)));
    } catch (err) {
      toast.error(`Failed to ${feed.enabled ? 'disable' : 'enable'} ${feed.name}`, {
        description: err instanceof Error ? err.message : undefined,
      });
    }
  };

  const handleDelete = async (feed: BotFeed) => {
    if (confirmDeleteId !== feed.id) {
      setConfirmDeleteId(feed.id);
      return;
    }
    try {
      await api.deleteBotFeed(feed.id);
      setFeeds((prev) => prev.filter((f) => f.id !== feed.id));
      if (editingId === feed.id) {
        resetForm();
        setShowForm(false);
      }
      toast.success(`${feed.name} deleted`);
    } catch (err) {
      toast.error('Delete failed', {
        description: err instanceof Error ? err.message : undefined,
      });
    } finally {
      setConfirmDeleteId(null);
    }
  };

  const statTiles = [
    { label: 'Subscriptions', value: String(feeds.length) },
    { label: 'Active', value: String(activeCount) },
    { label: 'Items posted', value: String(itemsPosted) },
    {
      label: 'Errors',
      value: String(erroringCount),
      className: erroringCount > 0 ? 'text-destructive' : undefined,
    },
  ];

  return (
    <div className="flex-1 min-h-0 overflow-y-auto p-4 flex flex-col gap-3">
      <div className="flex items-start gap-2.5">
        {statTiles.map((tile) => (
          <div key={tile.label} className="flex-1 border border-border rounded-lg p-2.5">
            <div className="text-[0.625rem] uppercase tracking-wider text-muted-foreground font-medium">
              {tile.label}
            </div>
            <div className={cn('text-lg font-semibold mt-0.5', tile.className)}>{tile.value}</div>
          </div>
        ))}
        <Button
          size="sm"
          className="flex-shrink-0 mt-1.5"
          onClick={() => {
            if (showForm && !editingId) {
              setShowForm(false);
            } else {
              resetForm();
              setShowForm(true);
            }
          }}
        >
          <Plus className="h-3.5 w-3.5 mr-1" aria-hidden="true" />
          Add feed
        </Button>
      </div>

      {showForm && (
        <div className="border border-border rounded-lg p-3.5 flex flex-col gap-3 max-w-3xl">
          <div className="flex gap-3">
            <div className="flex-1">
              <div className="text-xs text-muted-foreground mb-1">Name</div>
              <Input
                value={name}
                onChange={(e) => setName(e.target.value)}
                placeholder="MeshCore Blog"
                className="h-8 text-[0.8125rem]"
              />
            </div>
            <div className="w-28">
              <div className="text-xs text-muted-foreground mb-1">Type</div>
              <select
                value={feedType}
                onChange={(e) => setFeedType(e.target.value as 'rss' | 'api')}
                className="h-8 w-full rounded-md border border-input bg-transparent px-2.5 text-[0.8125rem]"
                aria-label="Feed type"
              >
                <option value="rss">RSS</option>
                <option value="api">API</option>
              </select>
            </div>
            <div className="flex-[2]">
              <div className="text-xs text-muted-foreground mb-1">URL</div>
              <Input
                value={url}
                onChange={(e) => setUrl(e.target.value)}
                placeholder="https://example.com/feed.xml"
                className="h-8 font-mono text-[0.8125rem]"
              />
            </div>
          </div>
          <div className="flex gap-3">
            <div className="flex-1">
              <div className="text-xs text-muted-foreground mb-1">Channel</div>
              <select
                value={channelKey}
                onChange={(e) => setChannelKey(e.target.value)}
                className="h-8 w-full rounded-md border border-input bg-transparent px-2.5 text-[0.8125rem]"
                aria-label="Target channel"
              >
                <option value="">Select channel…</option>
                {targetChannels.map((c) => (
                  <option key={c.key} value={c.key}>
                    {c.name}
                  </option>
                ))}
              </select>
            </div>
            <div className="w-24">
              <div className="text-xs text-muted-foreground mb-1">Interval</div>
              <select
                value={intervalSeconds}
                onChange={(e) => setIntervalSeconds(parseInt(e.target.value, 10))}
                className="h-8 w-full rounded-md border border-input bg-transparent px-2.5 text-[0.8125rem]"
                aria-label="Check interval"
              >
                {INTERVAL_OPTIONS.map((opt) => (
                  <option key={opt.value} value={opt.value}>
                    {opt.label}
                  </option>
                ))}
              </select>
            </div>
            <div className="flex-1">
              <div className="text-xs text-muted-foreground mb-1">Format</div>
              <Input
                value={format}
                onChange={(e) => setFormat(e.target.value)}
                className="h-8 font-mono text-[0.8125rem]"
              />
            </div>
            {feedType === 'api' && (
              <div className="w-36">
                <div className="text-xs text-muted-foreground mb-1">Items path</div>
                <Input
                  value={itemsPath}
                  onChange={(e) => setItemsPath(e.target.value)}
                  placeholder="data.items"
                  className="h-8 font-mono text-[0.8125rem]"
                />
              </div>
            )}
            <div className="w-28">
              <div className="text-xs text-muted-foreground mb-1">Max posts/check</div>
              <Input
                type="number"
                min={1}
                value={maxPosts}
                onChange={(e) => setMaxPosts(e.target.value)}
                className="h-8 font-mono text-[0.8125rem]"
              />
            </div>
          </div>
          {testResult && (
            <div className="flex flex-col gap-1.5">
              <div className="text-[0.6875rem] text-muted-foreground">
                {testResult.item_count} item{testResult.item_count === 1 ? '' : 's'} found —
                preview:
              </div>
              {testResult.preview.slice(0, 3).map((text, i) => (
                <div
                  key={i}
                  className="self-start max-w-[75%] bg-msg-incoming rounded-lg px-3 py-2 text-[0.8125rem] leading-relaxed whitespace-pre-line"
                >
                  {text}
                </div>
              ))}
            </div>
          )}
          <div className="flex gap-2">
            <Button
              variant="outline"
              size="sm"
              onClick={() => void handleTestFetch()}
              disabled={testing}
            >
              {testing ? 'Fetching…' : 'Test fetch'}
            </Button>
            <div className="flex-1" />
            <Button size="sm" onClick={() => void handleSave()} disabled={saving}>
              {saving ? 'Saving…' : editingId ? 'Save' : 'Create'}
            </Button>
            <Button
              variant="ghost"
              size="sm"
              onClick={() => {
                resetForm();
                setShowForm(false);
              }}
            >
              Cancel
            </Button>
          </div>
        </div>
      )}

      <div className="border border-border rounded-lg overflow-hidden max-w-5xl">
        {loading && <div className="px-3 py-4 text-xs text-muted-foreground">Loading feeds…</div>}
        {!loading && feeds.length === 0 && (
          <div className="px-3 py-4 text-xs text-muted-foreground">No feed subscriptions yet.</div>
        )}
        {feeds.map((feed, index) => (
          <div
            key={feed.id}
            className={cn(
              'flex items-center gap-2.5 px-3 py-1.5 text-xs',
              index > 0 && 'border-t border-border/50'
            )}
          >
            <label className="flex items-center cursor-pointer">
              <input
                type="checkbox"
                checked={feed.enabled}
                onChange={() => void handleToggleEnabled(feed)}
                className="w-4 h-4 rounded border-input accent-primary"
                aria-label={`Enable ${feed.name}`}
              />
            </label>
            <div className="flex-1 min-w-0">
              <div className="flex items-center gap-2">
                <span
                  className={cn(
                    'text-[0.8125rem] font-medium truncate',
                    !feed.enabled && 'text-muted-foreground'
                  )}
                >
                  {feed.name}
                </span>
                <span
                  className={cn(
                    'text-[0.625rem] uppercase tracking-wider rounded px-1.5 py-0.5',
                    feed.feed_type === 'rss' ? 'bg-info/15 text-info' : 'bg-success/15 text-success'
                  )}
                >
                  {feed.feed_type}
                </span>
              </div>
              <div className="font-mono text-[0.6875rem] text-muted-foreground truncate">
                {feed.url}
              </div>
            </div>
            <span className="flex-shrink-0 text-[0.6875rem] bg-primary/10 text-primary rounded px-1.5 py-0.5 whitespace-nowrap">
              → {feed.channel_name ?? `${feed.channel_key.slice(0, 8)}…`}
            </span>
            <span className="w-10 flex-shrink-0 text-[0.6875rem] text-muted-foreground">
              {humanizeInterval(feed.interval_seconds)}
            </span>
            <span className="w-20 flex-shrink-0 text-[0.6875rem] text-muted-foreground">
              {feed.last_check_at
                ? new Date(feed.last_check_at * 1000).toLocaleTimeString([], {
                    hour: '2-digit',
                    minute: '2-digit',
                  })
                : '—'}
            </span>
            <span
              className={cn(
                'w-14 flex-shrink-0 text-[0.6875rem]',
                feed.error_count > 0 ? 'text-destructive' : 'text-muted-foreground'
              )}
              title={feed.last_error ?? undefined}
            >
              {feed.error_count > 0 ? `${feed.error_count} errs` : '—'}
            </span>
            <Button
              variant="ghost"
              size="sm"
              className="h-6 px-2 text-xs"
              onClick={() => loadIntoForm(feed)}
            >
              Edit
            </Button>
            <Button
              variant="ghost"
              size="sm"
              className="h-6 px-2 text-xs text-destructive hover:bg-destructive/10"
              onClick={() => void handleDelete(feed)}
              onBlur={() => setConfirmDeleteId(null)}
            >
              {confirmDeleteId === feed.id ? 'Confirm' : 'Delete'}
            </Button>
          </div>
        ))}
      </div>

      <p className="text-[0.6875rem] text-muted-foreground">
        Feeds poll RSS or JSON APIs and post new items. First check only marks position (no history
        flood). Private/LAN URLs are blocked (SSRF guard).
      </p>
    </div>
  );
}
