import { useEffect, useMemo, useRef, useState } from 'react';

import { api } from '../../api';
import { clearBotLogs, seedBotLogs, useBotLogs } from '../../stores/botLogStore';
import { Button } from '../ui/button';
import { cn } from '@/lib/utils';

const LEVEL_FILTERS = ['All', 'Debug', 'Info', 'Warn', 'Error'] as const;

type LevelFilter = (typeof LEVEL_FILTERS)[number];

function levelClass(level: string): string {
  const normalized = level.toLowerCase();
  if (normalized.startsWith('debug')) return 'text-muted-foreground';
  if (normalized.startsWith('warn')) return 'text-warning';
  if (normalized.startsWith('error')) return 'text-destructive';
  return 'text-console';
}

function matchesLevel(entryLevel: string, filter: LevelFilter): boolean {
  if (filter === 'All') return true;
  return entryLevel.toLowerCase() === filter.toLowerCase();
}

/** Live bot-engine log console — seeded from REST, appended from WebSocket events. */
export function LogsTab() {
  const entries = useBotLogs();
  const [levelFilter, setLevelFilter] = useState<LevelFilter>('All');
  const [sourceFilter, setSourceFilter] = useState('');
  const [autoscroll, setAutoscroll] = useState(true);
  const consoleRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    api
      .getBotLogs(200)
      .then(seedBotLogs)
      .catch(() => {
        // Live entries still stream in via WebSocket; the snapshot is best-effort.
      });
  }, []);

  const sources = useMemo(() => [...new Set(entries.map((e) => e.source))].sort(), [entries]);

  const levelCounts = useMemo(() => {
    const counts: Record<LevelFilter, number> = { All: 0, Debug: 0, Info: 0, Warn: 0, Error: 0 };
    for (const entry of entries) {
      counts.All += 1;
      for (const level of LEVEL_FILTERS) {
        if (level !== 'All' && matchesLevel(entry.level, level)) counts[level] += 1;
      }
    }
    return counts;
  }, [entries]);

  const visibleEntries = useMemo(
    () =>
      entries.filter(
        (entry) =>
          matchesLevel(entry.level, levelFilter) && (!sourceFilter || entry.source === sourceFilter)
      ),
    [entries, levelFilter, sourceFilter]
  );

  useEffect(() => {
    if (!autoscroll) return;
    const container = consoleRef.current;
    if (container) {
      container.scrollTop = container.scrollHeight;
    }
  }, [visibleEntries, autoscroll]);

  return (
    <div className="flex-1 min-h-0 flex flex-col">
      <div className="flex items-center gap-1.5 px-4 py-2.5 flex-shrink-0 flex-wrap">
        {LEVEL_FILTERS.map((level) => (
          <button
            key={level}
            type="button"
            onClick={() => setLevelFilter(level)}
            className={cn(
              'px-2.5 py-1 rounded-md text-xs border transition-colors',
              levelFilter === level
                ? 'border-primary/50 bg-primary/10 text-primary'
                : 'border-input text-muted-foreground hover:text-foreground'
            )}
          >
            {level} <span className="opacity-65">{levelCounts[level]}</span>
          </button>
        ))}
        <div className="flex-1" />
        <select
          value={sourceFilter}
          onChange={(e) => setSourceFilter(e.target.value)}
          className="h-7 rounded-md border border-input bg-transparent px-2 text-xs text-muted-foreground"
          aria-label="Filter by source"
        >
          <option value="">All bots</option>
          {sources.map((source) => (
            <option key={source} value={source}>
              {source}
            </option>
          ))}
        </select>
        <label className="flex items-center gap-1.5 cursor-pointer text-xs text-muted-foreground">
          <input
            type="checkbox"
            checked={autoscroll}
            onChange={(e) => setAutoscroll(e.target.checked)}
            className="w-3.5 h-3.5 rounded border-input accent-primary"
          />
          Autoscroll
        </label>
        <Button variant="ghost" size="sm" className="h-7 px-2 text-xs" onClick={clearBotLogs}>
          Clear
        </Button>
      </div>

      {/* overflow-auto (not -y-auto): long lines stay on one line and the
          console scrolls horizontally instead of ellipsizing them away */}
      <div
        ref={consoleRef}
        data-testid="bot-log-console"
        className="flex-1 min-h-0 bg-console-bg border border-border rounded-lg mx-4 mb-4 p-3 overflow-auto font-mono text-[0.71875rem] leading-relaxed"
      >
        {visibleEntries.length === 0 && (
          <div className="text-muted-foreground">
            No log entries{entries.length > 0 ? ' match the filters' : ' yet'}.
          </div>
        )}
        {visibleEntries.map((entry, index) => (
          <div key={`${entry.timestamp}-${index}`} className="whitespace-nowrap">
            <span className="text-muted-foreground">
              {new Date(entry.timestamp * 1000).toLocaleTimeString([], {
                hour: '2-digit',
                minute: '2-digit',
                second: '2-digit',
                hour12: false,
              })}
            </span>{' '}
            <span className={cn('font-semibold', levelClass(entry.level))}>
              {entry.level.toUpperCase()}
            </span>{' '}
            <span className="text-foreground font-semibold">{entry.source}</span>{' '}
            <span className="text-foreground/75">{entry.message}</span>
          </div>
        ))}
      </div>
    </div>
  );
}
