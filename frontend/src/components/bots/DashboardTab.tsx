import { useEffect, useMemo, useState } from 'react';

import { api } from '../../api';
import type { BotStats, BotStatsRanked } from '../../types';
import { toast } from '../ui/sonner';
import { cn } from '@/lib/utils';

const WINDOWS = ['1h', '24h', '7d'] as const;

type StatsWindow = (typeof WINDOWS)[number];

function formatExec(ms: number): string {
  return `${(ms / 1000).toFixed(1)}s`;
}

/** Ranked horizontal bar list (label + count + proportional fill). */
function RankedBars({
  title,
  items,
  barClass,
  children,
}: {
  title: string;
  items: BotStatsRanked[];
  barClass: string;
  children?: React.ReactNode;
}) {
  const max = Math.max(1, ...items.map((item) => item.count));
  return (
    <div className="border border-border rounded-lg p-3">
      <div className="text-[0.625rem] uppercase tracking-wider text-muted-foreground font-medium mb-2.5">
        {title}
      </div>
      {items.length === 0 && <div className="text-xs text-muted-foreground">No data yet.</div>}
      {items.map((item) => (
        <div key={item.label} className="mb-2">
          <div className="flex justify-between text-xs mb-1">
            <span className="truncate">{item.label}</span>
            <span className="font-mono text-muted-foreground ml-2">{item.count}</span>
          </div>
          <div className="h-1.5 rounded-full bg-muted">
            <div
              className={cn('h-1.5 rounded-full', barClass)}
              style={{ width: `${(item.count / max) * 100}%` }}
            />
          </div>
        </div>
      ))}
      {children}
    </div>
  );
}

/** Aggregated bot-run statistics with a window switcher. */
export function DashboardTab() {
  const [window, setWindow] = useState<StatsWindow>('24h');
  const [stats, setStats] = useState<BotStats | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    api
      .getBotStats(window)
      .then((data) => {
        if (!cancelled) setStats(data);
      })
      .catch((err: unknown) => {
        if (!cancelled) {
          toast.error('Failed to load bot stats', {
            description: err instanceof Error ? err.message : undefined,
          });
        }
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [window]);

  // Last 24 hour buckets ending at the current hour, missing hours filled with 0.
  const hourBars = useMemo(() => {
    const byHour = new Map<number, number>();
    for (const bucket of stats?.runs_by_hour ?? []) {
      byHour.set(Math.floor(bucket.timestamp / 3600) * 3600, bucket.count);
    }
    const currentHour = Math.floor(Date.now() / 1000 / 3600) * 3600;
    const bars: { timestamp: number; count: number }[] = [];
    for (let i = 23; i >= 0; i--) {
      const ts = currentHour - i * 3600;
      bars.push({ timestamp: ts, count: byHour.get(ts) ?? 0 });
    }
    return bars;
  }, [stats]);

  const maxHourCount = Math.max(1, ...hourBars.map((bar) => bar.count));

  const tiles = stats
    ? [
        { label: 'Runs', value: String(stats.runs) },
        { label: 'Replies sent', value: String(stats.replies) },
        { label: 'Reply rate', value: `${Math.round(stats.reply_rate)}%` },
        { label: 'Unique users', value: String(stats.unique_users) },
        {
          label: 'Error runs',
          value: String(stats.errors),
          className: stats.errors > 0 ? 'text-destructive' : undefined,
        },
        { label: 'Avg exec', value: formatExec(stats.avg_duration_ms) },
      ]
    : [];

  return (
    <div className="flex-1 min-h-0 overflow-y-auto p-4 flex flex-col gap-3.5">
      <div className="flex items-center gap-1.5">
        <span className="text-xs text-muted-foreground mr-1">Window</span>
        {WINDOWS.map((w) => (
          <button
            key={w}
            type="button"
            onClick={() => setWindow(w)}
            className={cn(
              'px-2.5 py-1 rounded-md text-xs border transition-colors',
              window === w
                ? 'border-primary/50 bg-primary/10 text-primary'
                : 'border-input text-muted-foreground hover:text-foreground'
            )}
          >
            {w}
          </button>
        ))}
        <div className="flex-1" />
        {loading && <span className="text-[0.6875rem] text-muted-foreground">Loading…</span>}
      </div>

      {stats && (
        <>
          <div className="grid grid-cols-2 md:grid-cols-6 gap-2.5">
            {tiles.map((tile) => (
              <div key={tile.label} className="border border-border rounded-lg p-2.5">
                <div className="text-[0.625rem] uppercase tracking-wider text-muted-foreground font-medium">
                  {tile.label}
                </div>
                <div className={cn('text-lg font-semibold mt-0.5', tile.className)}>
                  {tile.value}
                </div>
              </div>
            ))}
          </div>

          <div className="border border-border rounded-lg p-3">
            <div className="text-[0.625rem] uppercase tracking-wider text-muted-foreground font-medium mb-2">
              Bot runs by hour
            </div>
            <div className="flex items-end gap-1 h-20">
              {hourBars.map((bar) => (
                <div
                  key={bar.timestamp}
                  className="flex-1 rounded-t bg-primary/75"
                  style={{ height: `${(bar.count / maxHourCount) * 100}%` }}
                  title={`${new Date(bar.timestamp * 1000).toLocaleTimeString([], {
                    hour: '2-digit',
                    minute: '2-digit',
                  })} — ${bar.count} run${bar.count === 1 ? '' : 's'}`}
                />
              ))}
            </div>
            <div className="flex justify-between text-[0.625rem] text-muted-foreground mt-1">
              <span>00:00</span>
              <span>06:00</span>
              <span>12:00</span>
              <span>18:00</span>
              <span>now</span>
            </div>
          </div>

          <div className="grid md:grid-cols-3 gap-3">
            <RankedBars title="Top bots" items={stats.top_bots} barClass="bg-primary/80" />
            <RankedBars title="Top channels" items={stats.top_channels} barClass="bg-info/80" />
            <RankedBars title="Top users" items={stats.top_users} barClass="bg-warning/75">
              {stats.error_bots.length > 0 && (
                <>
                  <div className="text-[0.625rem] uppercase tracking-wider text-muted-foreground font-medium mt-3 mb-2">
                    Errors by bot
                  </div>
                  {stats.error_bots.map((bot) => (
                    <div key={bot.label} className="flex justify-between text-xs mb-1">
                      <span className="truncate">{bot.label}</span>
                      <span className="font-mono text-destructive ml-2">{bot.count}</span>
                    </div>
                  ))}
                </>
              )}
            </RankedBars>
          </div>

          <p className="text-[0.6875rem] text-muted-foreground">
            Aggregated from bot_runs · survives restarts · test runs excluded
          </p>
        </>
      )}
    </div>
  );
}
