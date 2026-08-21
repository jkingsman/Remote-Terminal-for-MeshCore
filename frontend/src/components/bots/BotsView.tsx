import { lazy, Suspense, useCallback, useEffect, useMemo, useState } from 'react';
import { Bot as BotIcon, Pencil, Plus, Search as SearchIcon } from 'lucide-react';

import { api } from '../../api';
import type { Bot, BotEngineStatus, Channel, Contact } from '../../types';
import { Button } from '../ui/button';
import { Input } from '../ui/input';
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from '../ui/dialog';
import { toast } from '../ui/sonner';
import { cn } from '@/lib/utils';
import { BotEditor } from './BotEditor';
import { NewBotDialogBody } from './NewBotDialog';

const SchedulerTab = lazy(() =>
  import('./SchedulerTab').then((m) => ({ default: m.SchedulerTab }))
);
const FeedsTab = lazy(() => import('./FeedsTab').then((m) => ({ default: m.FeedsTab })));
const LogsTab = lazy(() => import('./LogsTab').then((m) => ({ default: m.LogsTab })));
const DashboardTab = lazy(() =>
  import('./DashboardTab').then((m) => ({ default: m.DashboardTab }))
);
const EngineTab = lazy(() => import('./EngineTab').then((m) => ({ default: m.EngineTab })));

const CATEGORY_ORDER = [
  'Basic',
  'Weather',
  'Solar',
  'Mesh',
  'Sports',
  'Fun',
  'Info',
  'Alerts',
  'Admin',
  'Custom',
];

const WORKSPACE_TABS = [
  { id: 'bots', label: 'Bots' },
  { id: 'scheduler', label: 'Scheduler' },
  { id: 'feeds', label: 'Feeds' },
  { id: 'logs', label: 'Logs' },
  { id: 'dashboard', label: 'Dashboard' },
  { id: 'engine', label: 'Engine' },
] as const;

type WorkspaceTab = (typeof WORKSPACE_TABS)[number]['id'];

/** Chips summarizing a bot's triggers (keywords, cron, events, webhooks). */
export function BotTriggerChips({ bot, limit = 4 }: { bot: Bot; limit?: number }) {
  const chips: { text: string; kind: 'kw' | 'cron' | 'event' | 'hook' }[] = [];
  const uiKeywords = bot.ui_triggers.filter((t) => t.kind === 'keyword').map((t) => t.spec);
  const keywords = [...bot.declared_keywords, ...uiKeywords];
  for (const kw of keywords.slice(0, 3)) {
    chips.push({ text: kw, kind: 'kw' });
  }
  if (keywords.length > 3) {
    chips.push({ text: `+${keywords.length - 3}`, kind: 'kw' });
  }
  const crons = [
    ...bot.declared_crons,
    ...bot.ui_triggers.filter((t) => t.kind === 'cron').map((t) => t.spec),
  ];
  for (const cron of crons.slice(0, 1)) {
    chips.push({ text: `cron ${cron}`, kind: 'cron' });
  }
  for (const event of bot.declared_events.slice(0, 1)) {
    chips.push({ text: `on: ${event.replace(/_/g, ' ')}`, kind: 'event' });
  }
  for (const hook of bot.declared_webhooks.slice(0, 1)) {
    chips.push({ text: `webhook /${hook}`, kind: 'hook' });
  }
  if (bot.is_legacy) {
    chips.push({ text: 'all messages (legacy)', kind: 'event' });
  }

  const chipClass = (kind: string) =>
    cn(
      'font-mono text-[0.6875rem] rounded px-1.5 py-0.5 whitespace-nowrap',
      kind === 'kw' && 'bg-muted text-muted-foreground',
      kind === 'cron' && 'bg-info/15 text-info',
      kind === 'event' && 'bg-warning/15 text-warning',
      kind === 'hook' && 'bg-success/15 text-success'
    );

  return (
    <div className="flex flex-wrap gap-1 overflow-hidden">
      {chips.slice(0, limit).map((chip, i) => (
        <span key={i} className={chipClass(chip.kind)}>
          {chip.text}
        </span>
      ))}
    </div>
  );
}

function botStatusDot(bot: Bot): { className: string; label: string } {
  if (!bot.enabled) {
    return { className: 'bg-status-disconnected', label: 'Disabled' };
  }
  if (bot.load_error) {
    return { className: 'bg-destructive', label: 'Load error' };
  }
  if (bot.last_error) {
    return { className: 'bg-destructive', label: 'Error' };
  }
  return { className: 'bg-status-connected', label: 'Running' };
}

function describeScope(bot: Bot): string {
  const channels = bot.scope?.channels ?? 'all';
  let channelPart = 'All channels';
  if (channels === 'none') {
    channelPart = 'No channels';
  } else if (typeof channels === 'object') {
    if (channels.only) channelPart = `Only ${channels.only.length} channel(s)`;
    else if (channels.except) channelPart = `All except ${channels.except.length}`;
  }
  return bot.respond_to_dms ? `${channelPart} + DMs` : channelPart;
}

function describeLimits(bot: Bot): string {
  const parts: string[] = [];
  if (bot.cooldown_seconds > 0) parts.push(`${bot.cooldown_seconds}s cd`);
  if (bot.per_user_cooldown_seconds > 0) parts.push(`${bot.per_user_cooldown_seconds}s/user`);
  if (bot.admin_only) parts.push('admins');
  return parts.join(' · ') || '—';
}

interface BotsViewProps {
  botId: string | null;
  channels: Channel[];
  contacts: Contact[];
  onOpenBot: (botId: string) => void;
  onCloseBot: () => void;
}

export function BotsView({ botId, channels, contacts, onOpenBot, onCloseBot }: BotsViewProps) {
  const [bots, setBots] = useState<Bot[]>([]);
  const [loading, setLoading] = useState(true);
  const [engine, setEngine] = useState<BotEngineStatus | null>(null);
  const [activeTab, setActiveTab] = useState<WorkspaceTab>('bots');
  const [category, setCategory] = useState('All');
  const [filter, setFilter] = useState('');
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [showNewBot, setShowNewBot] = useState(false);

  const refresh = useCallback(async () => {
    try {
      const [botList, engineStatus] = await Promise.all([api.getBots(), api.getBotEngine()]);
      setBots(botList);
      setEngine(engineStatus);
      setSelectedId((prev) => prev ?? botList[0]?.id ?? null);
    } catch (err) {
      toast.error('Failed to load bots', {
        description: err instanceof Error ? err.message : undefined,
      });
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const categories = useMemo(() => {
    const present = new Set(bots.map((b) => b.category));
    const ordered = CATEGORY_ORDER.filter((c) => present.has(c));
    const extras = [...present].filter((c) => !CATEGORY_ORDER.includes(c)).sort();
    return ['All', ...ordered, ...extras];
  }, [bots]);

  const visibleBots = useMemo(() => {
    const query = filter.trim().toLowerCase();
    return bots.filter((bot) => {
      if (category !== 'All' && bot.category !== category) return false;
      if (!query) return true;
      const haystack = [
        bot.name,
        bot.description,
        ...bot.declared_keywords,
        ...bot.ui_triggers.map((t) => t.spec),
      ]
        .join(' ')
        .toLowerCase();
      return haystack.includes(query);
    });
  }, [bots, category, filter]);

  const selectedBot = useMemo(
    () => bots.find((b) => b.id === selectedId) ?? null,
    [bots, selectedId]
  );

  const enabledCount = bots.filter((b) => b.enabled).length;
  const erroringCount = bots.filter((b) => b.enabled && (b.last_error || b.load_error)).length;

  const handleToggleEnabled = async (bot: Bot) => {
    try {
      const updated = await api.updateBot(bot.id, { enabled: !bot.enabled });
      setBots((prev) => prev.map((b) => (b.id === bot.id ? updated : b)));
    } catch (err) {
      toast.error(`Failed to ${bot.enabled ? 'disable' : 'enable'} ${bot.name}`, {
        description: err instanceof Error ? err.message : undefined,
      });
    }
  };

  const handleDisableAll = async () => {
    try {
      await api.disableBotsUntilRestart();
      await refresh();
      toast.success('All bots disabled until restart');
    } catch (err) {
      toast.error('Failed to disable bots', {
        description: err instanceof Error ? err.message : undefined,
      });
    }
  };

  // Editor mode: a bot id in the URL hash replaces the workspace with the editor.
  if (botId) {
    return (
      <BotEditor
        botId={botId}
        channels={channels}
        onBack={() => {
          onCloseBot();
          void refresh();
        }}
        onDeleted={() => {
          onCloseBot();
          void refresh();
        }}
      />
    );
  }

  return (
    <div className="flex-1 flex flex-col min-h-0">
      {/* Workspace header — stacks into two rows below md so the actions stay
          reachable */}
      <div className="flex flex-col gap-2 md:flex-row md:items-center md:gap-3 px-4 py-2.5 border-b border-border flex-shrink-0">
        <div className="flex flex-wrap items-center gap-x-3 gap-y-1 min-w-0 md:flex-1">
          <BotIcon className="h-[18px] w-[18px]" aria-hidden="true" />
          <h2 className="font-semibold text-base tracking-tight">Bots</h2>
          <span className="text-xs text-muted-foreground truncate">
            {enabledCount} of {bots.length} enabled
            {engine ? ` · ${engine.runs_24h} runs (24h)` : ''}
          </span>
          {erroringCount > 0 && (
            <span className="text-[0.6875rem] bg-destructive/10 text-destructive rounded-full px-2 py-0.5 whitespace-nowrap">
              {erroringCount} bot{erroringCount === 1 ? '' : 's'} erroring
            </span>
          )}
          {(engine?.disabled_until_restart || engine?.disabled_by_env) && (
            <span className="text-[0.6875rem] bg-warning/10 text-warning rounded-full px-2 py-0.5 whitespace-nowrap">
              {engine.disabled_by_env ? 'disabled by server env' : 'disabled until restart'}
            </span>
          )}
        </div>
        <div className="flex items-center gap-3 flex-shrink-0">
          <Button
            variant="outline"
            size="sm"
            className="border-warning/50 text-warning hover:bg-warning/10"
            onClick={() => void handleDisableAll()}
            disabled={engine?.disabled_until_restart}
          >
            Disable all until restart
          </Button>
          <Button size="sm" onClick={() => setShowNewBot(true)}>
            <Plus className="h-3.5 w-3.5 mr-1" aria-hidden="true" />
            New Bot
          </Button>
        </div>
      </div>

      {/* Tab bar — the tab strip scrolls horizontally when it cannot fit, and
          the filter drops to its own row below md */}
      <div className="flex flex-col gap-2 md:flex-row md:items-center md:gap-3 px-4 py-2 border-b border-border flex-shrink-0">
        <div className="max-w-full overflow-x-auto">
          <div className="inline-flex gap-0.5 bg-muted rounded-lg p-[3px]">
            {WORKSPACE_TABS.map((tab) => (
              <button
                key={tab.id}
                type="button"
                onClick={() => setActiveTab(tab.id)}
                className={cn(
                  'px-3.5 py-1 rounded-md text-sm font-medium transition-colors whitespace-nowrap',
                  activeTab === tab.id
                    ? 'bg-background text-foreground shadow-sm'
                    : 'text-muted-foreground hover:text-foreground'
                )}
              >
                {tab.label}
              </button>
            ))}
          </div>
        </div>
        <div className="hidden md:block flex-1" />
        {activeTab === 'bots' && (
          <div className="relative w-full md:w-52">
            <SearchIcon
              className="h-3.5 w-3.5 absolute left-2.5 top-1/2 -translate-y-1/2 text-muted-foreground"
              aria-hidden="true"
            />
            <Input
              value={filter}
              onChange={(e) => setFilter(e.target.value)}
              placeholder="Filter bots..."
              className="h-8 pl-8 text-xs"
            />
          </div>
        )}
      </div>

      {/* Tab content */}
      {activeTab === 'bots' ? (
        <div className="flex-1 flex min-h-0">
          <div className="flex-1 min-w-0 flex flex-col">
            <div className="flex flex-wrap gap-1.5 px-4 pt-2.5 pb-2 flex-shrink-0">
              {categories.map((cat) => (
                <button
                  key={cat}
                  type="button"
                  onClick={() => setCategory(cat)}
                  className={cn(
                    'px-2.5 py-1 rounded-md text-xs border transition-colors',
                    category === cat
                      ? 'border-primary/50 bg-primary/10 text-primary'
                      : 'border-input text-muted-foreground hover:text-foreground'
                  )}
                >
                  {cat}
                </button>
              ))}
            </div>
            <div className="hidden md:flex items-center gap-2.5 px-4 py-1 text-[0.625rem] uppercase tracking-wider text-muted-foreground font-medium flex-shrink-0">
              <span className="w-6" />
              <span className="flex-1">Bot</span>
              <span className="w-56">Triggers</span>
              <span className="w-24">Limits</span>
              <span className="w-14 text-right">Runs 24h</span>
              <span className="w-5" />
            </div>
            <div className="flex-1 min-h-0 overflow-y-auto" data-testid="bot-list">
              {loading && (
                <div className="px-4 py-8 text-sm text-muted-foreground">Loading bots…</div>
              )}
              {!loading && visibleBots.length === 0 && (
                <div className="px-4 py-8 text-sm text-muted-foreground">
                  No bots match this filter.
                </div>
              )}
              {visibleBots.map((bot) => {
                const dot = botStatusDot(bot);
                const selected = bot.id === selectedId;
                // Below lg the detail rail (and its "Open editor" button) does
                // not exist, so selecting a row has no visible effect — a tap
                // opens the editor directly instead.
                const selectOrOpen = () => {
                  setSelectedId(bot.id);
                  if (!window.matchMedia('(min-width: 1024px)').matches) {
                    onOpenBot(bot.id);
                  }
                };
                return (
                  <div
                    key={bot.id}
                    role="button"
                    tabIndex={0}
                    onClick={selectOrOpen}
                    onDoubleClick={() => onOpenBot(bot.id)}
                    onKeyDown={(e) => {
                      if (e.key === 'Enter') selectOrOpen();
                    }}
                    className={cn(
                      'flex items-center gap-2.5 px-4 py-1.5 border-l-2 border-b border-border/50 cursor-pointer hover:bg-accent/50 transition-colors',
                      selected ? 'bg-accent border-l-primary' : 'border-l-transparent'
                    )}
                  >
                    <label
                      className="flex items-center cursor-pointer"
                      onClick={(e) => e.stopPropagation()}
                    >
                      <input
                        type="checkbox"
                        checked={bot.enabled}
                        onChange={() => void handleToggleEnabled(bot)}
                        className="w-4 h-4 rounded border-input accent-primary"
                        aria-label={`Enable ${bot.name}`}
                      />
                    </label>
                    <div className="flex-1 min-w-0">
                      <div className="flex items-center gap-2">
                        <span
                          className={cn(
                            'text-[0.8125rem] font-medium truncate',
                            !bot.enabled && 'text-muted-foreground'
                          )}
                        >
                          {bot.name}
                        </span>
                        <span className="text-[0.625rem] uppercase tracking-wider bg-muted text-muted-foreground rounded px-1.5 py-0.5">
                          {bot.category}
                        </span>
                        {bot.builtin_key && bot.modified && (
                          <span className="text-[0.625rem] uppercase tracking-wider bg-info/15 text-info rounded px-1.5 py-0.5">
                            modified
                          </span>
                        )}
                      </div>
                      <div className="text-[0.6875rem] text-muted-foreground truncate">
                        {bot.load_error ?? bot.description}
                      </div>
                    </div>
                    <div className="hidden md:block w-56 flex-shrink-0">
                      <BotTriggerChips bot={bot} />
                    </div>
                    <div className="hidden md:block w-24 flex-shrink-0 text-[0.6875rem] text-muted-foreground truncate">
                      {describeLimits(bot)}
                    </div>
                    <div className="hidden md:block w-14 flex-shrink-0 text-right font-mono text-xs">
                      {bot.enabled ? bot.runs_24h : '—'}
                    </div>
                    <button
                      type="button"
                      className="lg:hidden flex-shrink-0 p-1.5 -my-1 text-muted-foreground"
                      aria-label={`Edit ${bot.name}`}
                      onClick={(e) => {
                        e.stopPropagation();
                        onOpenBot(bot.id);
                      }}
                    >
                      <Pencil className="h-3.5 w-3.5" aria-hidden="true" />
                    </button>
                    <div className="w-5 flex-shrink-0 flex justify-center">
                      <div
                        className={cn('w-2 h-2 rounded-full', dot.className)}
                        title={dot.label}
                        aria-hidden="true"
                      />
                    </div>
                  </div>
                );
              })}
              {!loading && (
                <div className="px-4 py-3 text-[0.6875rem] text-muted-foreground">
                  {visibleBots.length} bot{visibleBots.length === 1 ? '' : 's'} shown · seeded
                  library ships disabled — enable what your mesh should answer
                </div>
              )}
            </div>
          </div>

          {/* Detail rail */}
          <div className="hidden lg:flex w-72 flex-shrink-0 border-l border-border flex-col overflow-y-auto">
            {selectedBot ? (
              <div className="p-4 flex flex-col gap-3.5">
                <div>
                  <div className="flex items-center gap-2">
                    <span className="text-[0.9375rem] font-semibold">{selectedBot.name}</span>
                    <span className="text-[0.625rem] uppercase tracking-wider bg-muted text-muted-foreground rounded px-1.5 py-0.5">
                      {selectedBot.category}
                    </span>
                    <div className="flex-1" />
                    <div
                      className={cn('w-2 h-2 rounded-full', botStatusDot(selectedBot).className)}
                      aria-hidden="true"
                    />
                    <span className="text-[0.6875rem] text-muted-foreground">
                      {botStatusDot(selectedBot).label}
                    </span>
                  </div>
                  <p className="text-xs text-muted-foreground mt-1 leading-relaxed">
                    {selectedBot.description}
                  </p>
                </div>
                <div>
                  <div className="text-[0.625rem] uppercase tracking-wider text-muted-foreground font-medium mb-1.5">
                    Triggers
                  </div>
                  <BotTriggerChips bot={selectedBot} limit={8} />
                </div>
                <div>
                  <div className="text-[0.625rem] uppercase tracking-wider text-muted-foreground font-medium mb-1">
                    Scope
                  </div>
                  <div className="text-xs">{describeScope(selectedBot)}</div>
                </div>
                <div className="flex gap-4">
                  <div>
                    <div className="text-[0.625rem] uppercase tracking-wider text-muted-foreground font-medium mb-1">
                      Limits
                    </div>
                    <div className="text-xs">{describeLimits(selectedBot)}</div>
                  </div>
                  <div>
                    <div className="text-[0.625rem] uppercase tracking-wider text-muted-foreground font-medium mb-1">
                      Runs 24h
                    </div>
                    <div className="text-xs">{selectedBot.runs_24h}</div>
                  </div>
                </div>
                {(selectedBot.last_error || selectedBot.load_error) && (
                  <div className="border border-destructive/35 bg-destructive/10 rounded-md px-2.5 py-2">
                    <div className="text-[0.625rem] uppercase tracking-wider text-destructive font-semibold mb-1">
                      Latest error
                    </div>
                    <div className="font-mono text-[0.6875rem] leading-relaxed break-words">
                      {selectedBot.load_error ?? selectedBot.last_error}
                    </div>
                  </div>
                )}
                <div className="flex flex-col gap-2 mt-1">
                  <Button size="sm" onClick={() => onOpenBot(selectedBot.id)}>
                    <Pencil className="h-3.5 w-3.5 mr-1.5" aria-hidden="true" />
                    Open editor
                  </Button>
                  <Button
                    size="sm"
                    variant="outline"
                    onClick={() => void handleToggleEnabled(selectedBot)}
                  >
                    {selectedBot.enabled ? 'Disable' : 'Enable'}
                  </Button>
                </div>
                <div className="text-[0.6875rem] text-muted-foreground border-t border-border pt-2.5">
                  {selectedBot.builtin_key
                    ? `Seeded from built-in ${selectedBot.builtin_key} ${selectedBot.builtin_version ?? ''}${selectedBot.modified ? ' · modified' : ''}`
                    : 'Custom bot'}
                </div>
              </div>
            ) : (
              <div className="p-4 text-xs text-muted-foreground">Select a bot to see details.</div>
            )}
          </div>
        </div>
      ) : (
        <Suspense
          fallback={
            <div className="flex-1 flex items-center justify-center text-muted-foreground">
              Loading…
            </div>
          }
        >
          {activeTab === 'scheduler' && <SchedulerTab channels={channels} />}
          {activeTab === 'feeds' && <FeedsTab channels={channels} />}
          {activeTab === 'logs' && <LogsTab />}
          {activeTab === 'dashboard' && <DashboardTab />}
          {activeTab === 'engine' && <EngineTab contacts={contacts} onChanged={refresh} />}
        </Suspense>
      )}

      {/* New Bot dialog */}
      <Dialog open={showNewBot} onOpenChange={setShowNewBot}>
        <DialogContent className="sm:max-w-lg">
          <DialogHeader className="border-b border-border px-5 py-4">
            <DialogTitle>New Bot</DialogTitle>
            <DialogDescription>
              Bots are Python scripts stored in the database. Code runs on the server with full
              Python — trusted operators only.
            </DialogDescription>
          </DialogHeader>
          <NewBotDialogBody
            onCreated={(bot) => {
              setShowNewBot(false);
              void refresh();
              onOpenBot(bot.id);
            }}
          />
        </DialogContent>
      </Dialog>
    </div>
  );
}
