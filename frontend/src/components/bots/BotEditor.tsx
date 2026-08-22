import { lazy, Suspense, useCallback, useEffect, useMemo, useState } from 'react';
import { ArrowLeft, Play, Plus, X } from 'lucide-react';

import { api } from '../../api';
import type {
  Bot,
  BotRun,
  BotSettingsSchemaField,
  BotTestResponse,
  BotUiTrigger,
  BotUpdatePayload,
  Channel,
} from '../../types';
import { Button } from '../ui/button';
import { Input } from '../ui/input';
import { toast } from '../ui/sonner';
import { cn } from '@/lib/utils';
import { BotTriggerChips } from './BotsView';

const BotCodeEditor = lazy(() =>
  import('../BotCodeEditor').then((m) => ({ default: m.BotCodeEditor }))
);

const EDITOR_TABS = [
  { id: 'settings', label: 'Settings' },
  { id: 'triggers', label: 'Triggers' },
  { id: 'code', label: 'Code' },
  { id: 'test', label: 'Test' },
  { id: 'activity', label: 'Activity' },
] as const;

type EditorTab = (typeof EDITOR_TABS)[number]['id'];

type ScopeMode = 'all' | 'only' | 'except';

function scopeModeOf(bot: Bot): ScopeMode {
  const channels = bot.scope?.channels;
  if (typeof channels === 'object' && channels !== null) {
    if (channels.only) return 'only';
    if (channels.except) return 'except';
  }
  return 'all';
}

function scopeListOf(bot: Bot): string[] {
  const channels = bot.scope?.channels;
  if (typeof channels === 'object' && channels !== null) {
    return channels.only ?? channels.except ?? [];
  }
  return [];
}

function SectionTitle({ title, hint }: { title: string; hint?: string }) {
  return (
    <div className="mb-2.5">
      <h3 className="text-base font-semibold tracking-tight">{title}</h3>
      {hint && <p className="text-[0.8125rem] text-muted-foreground mt-0.5">{hint}</p>}
    </div>
  );
}

/** Renders one settings_schema field bound to the settings draft. */
function SchemaField({
  field,
  value,
  onChange,
}: {
  field: BotSettingsSchemaField;
  value: unknown;
  onChange: (value: unknown) => void;
}) {
  const [revealed, setRevealed] = useState(false);
  const current = value ?? field.default ?? (field.type === 'bool' ? false : '');

  if (field.type === 'bool') {
    return (
      <label className="flex items-center gap-2.5 cursor-pointer">
        <input
          type="checkbox"
          checked={Boolean(current)}
          onChange={(e) => onChange(e.target.checked)}
          className="w-4 h-4 rounded border-input accent-primary"
        />
        <span className="text-[0.8125rem]">{field.label}</span>
      </label>
    );
  }

  if (field.type === 'select') {
    return (
      <div>
        <div className="text-xs text-muted-foreground mb-1">{field.label}</div>
        <select
          value={String(current)}
          onChange={(e) => onChange(e.target.value)}
          className="h-8 w-full rounded-md border border-input bg-transparent px-2.5 text-[0.8125rem]"
        >
          {(field.options ?? []).map((opt) => (
            <option key={opt.value} value={opt.value}>
              {opt.label}
            </option>
          ))}
        </select>
        {field.help && (
          <div className="text-[0.6875rem] text-muted-foreground mt-1">{field.help}</div>
        )}
      </div>
    );
  }

  const isNumber = field.type === 'int' || field.type === 'float';
  return (
    <div>
      <div className="text-xs text-muted-foreground mb-1">{field.label}</div>
      <div className="flex gap-2">
        <Input
          type={field.type === 'password' && !revealed ? 'password' : isNumber ? 'number' : 'text'}
          value={String(current)}
          min={field.min}
          max={field.max}
          step={field.type === 'float' ? 'any' : undefined}
          onChange={(e) => {
            if (field.type === 'int')
              onChange(e.target.value === '' ? '' : parseInt(e.target.value, 10) || 0);
            else if (field.type === 'float')
              onChange(e.target.value === '' ? '' : parseFloat(e.target.value) || 0);
            else onChange(e.target.value);
          }}
          className="h-8 text-[0.8125rem]"
        />
        {field.type === 'password' && (
          <Button
            type="button"
            variant="ghost"
            size="sm"
            className="h-8 px-2 text-xs"
            onClick={() => setRevealed((r) => !r)}
          >
            {revealed ? 'Hide' : 'Reveal'}
          </Button>
        )}
      </div>
      {field.help && (
        <div className="text-[0.6875rem] text-muted-foreground mt-1">{field.help}</div>
      )}
    </div>
  );
}

interface BotEditorProps {
  botId: string;
  channels: Channel[];
  onBack: () => void;
  onDeleted: () => void;
}

export function BotEditor({ botId, channels, onBack, onDeleted }: BotEditorProps) {
  const [bot, setBot] = useState<Bot | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [tab, setTab] = useState<EditorTab>('settings');
  const [saving, setSaving] = useState(false);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [dirty, setDirty] = useState(false);
  const [renaming, setRenaming] = useState(false);
  const [nameDraft, setNameDraft] = useState('');

  // Draft state
  const [code, setCode] = useState('');
  const [enabled, setEnabled] = useState(false);
  const [scopeMode, setScopeMode] = useState<ScopeMode>('all');
  const [scopeList, setScopeList] = useState<string[]>([]);
  const [respondToDms, setRespondToDms] = useState(true);
  const [adminOnly, setAdminOnly] = useState(false);
  const [cooldown, setCooldown] = useState('0');
  const [perUserCooldown, setPerUserCooldown] = useState('0');
  const [queueThreshold, setQueueThreshold] = useState('0');
  const [settings, setSettings] = useState<Record<string, unknown>>({});
  const [uiTriggers, setUiTriggers] = useState<BotUiTrigger[]>([]);
  const [newKeyword, setNewKeyword] = useState('');
  const [newCron, setNewCron] = useState('');
  const [cronPreview, setCronPreview] = useState<string | null>(null);

  // Test tab state
  const [testText, setTestText] = useState('');
  const [testDm, setTestDm] = useState(false);
  const [testSender, setTestSender] = useState('TestUser');
  const [testRunning, setTestRunning] = useState(false);
  const [transcript, setTranscript] = useState<{ input: string; response: BotTestResponse }[]>([]);

  // Activity tab state
  const [runs, setRuns] = useState<BotRun[]>([]);

  const applyBot = useCallback((loaded: Bot) => {
    setBot(loaded);
    setCode(loaded.code);
    setEnabled(loaded.enabled);
    setScopeMode(scopeModeOf(loaded));
    setScopeList(scopeListOf(loaded));
    setRespondToDms(loaded.respond_to_dms);
    setAdminOnly(loaded.admin_only);
    setCooldown(String(loaded.cooldown_seconds));
    setPerUserCooldown(String(loaded.per_user_cooldown_seconds));
    setQueueThreshold(String(loaded.queue_threshold_seconds));
    setSettings({ ...loaded.settings });
    setUiTriggers([...loaded.ui_triggers]);
    setDirty(false);
  }, []);

  useEffect(() => {
    api
      .getBot(botId)
      .then((loaded) => {
        applyBot(loaded);
        setTestText(loaded.declared_keywords[0] ?? '');
      })
      .catch((err: unknown) =>
        setLoadError(err instanceof Error ? err.message : 'Failed to load bot')
      );
  }, [botId, applyBot]);

  useEffect(() => {
    if (tab !== 'activity') return;
    api
      .getBotRuns(botId, 50)
      .then(setRuns)
      .catch(() => setRuns([]));
  }, [tab, botId]);

  const markDirty = () => setDirty(true);

  const buildScope = (): Bot['scope'] => {
    if (scopeMode === 'only') return { channels: { only: scopeList } };
    if (scopeMode === 'except') return { channels: { except: scopeList } };
    return { channels: 'all' };
  };

  const handleSave = async (overrideEnabled?: boolean) => {
    if (!bot) return;
    setSaving(true);
    const payload: BotUpdatePayload = {
      code,
      enabled: overrideEnabled ?? enabled,
      scope: buildScope(),
      respond_to_dms: respondToDms,
      admin_only: adminOnly,
      cooldown_seconds: parseFloat(cooldown) || 0,
      per_user_cooldown_seconds: parseFloat(perUserCooldown) || 0,
      queue_threshold_seconds: parseFloat(queueThreshold) || 0,
      settings,
      ui_triggers: uiTriggers,
    };
    try {
      const updated = await api.updateBot(bot.id, payload);
      applyBot(updated);
      toast.success(`${updated.name} saved`);
    } catch (err) {
      toast.error('Save failed', {
        description: err instanceof Error ? err.message : undefined,
      });
    } finally {
      setSaving(false);
    }
  };

  const handleRename = async () => {
    setRenaming(false);
    const next = nameDraft.trim();
    if (!bot || !next || next === bot.name) return;
    try {
      const updated = await api.updateBot(bot.id, { name: next });
      // Adopt only the new name — leave any unsaved code/settings/scope drafts
      // in this session untouched (a full applyBot would reset them).
      setBot((prev) => (prev ? { ...prev, name: updated.name } : updated));
      toast.success(`Renamed to ${updated.name}`);
    } catch (err) {
      toast.error('Rename failed', {
        description: err instanceof Error ? err.message : undefined,
      });
    }
  };

  const handleReset = async () => {
    if (!bot) return;
    try {
      const updated = await api.resetBot(bot.id);
      applyBot(updated);
      toast.success(`${updated.name} restored to the built-in version`);
    } catch (err) {
      toast.error('Reset failed', {
        description: err instanceof Error ? err.message : undefined,
      });
    }
  };

  const handleDelete = async () => {
    if (!bot) return;
    if (!confirmDelete) {
      setConfirmDelete(true);
      return;
    }
    try {
      await api.deleteBot(bot.id);
      toast.success(`${bot.name} deleted`);
      onDeleted();
    } catch (err) {
      toast.error('Delete failed', {
        description: err instanceof Error ? err.message : undefined,
      });
    }
  };

  const handleRunTest = async () => {
    if (!bot || !testText.trim()) return;
    setTestRunning(true);
    try {
      const response = await api.testBot(bot.id, {
        text: testText,
        is_dm: testDm,
        sender_name: testSender || 'TestUser',
      });
      setTranscript((prev) => [...prev, { input: testText, response }]);
    } catch (err) {
      toast.error('Test run failed', {
        description: err instanceof Error ? err.message : undefined,
      });
    } finally {
      setTestRunning(false);
    }
  };

  const handleAddKeyword = () => {
    const kw = newKeyword.trim().toLowerCase();
    if (!kw) return;
    if (uiTriggers.some((t) => t.kind === 'keyword' && t.spec === kw)) return;
    setUiTriggers((prev) => [...prev, { kind: 'keyword', spec: kw }]);
    setNewKeyword('');
    markDirty();
  };

  const handleAddCron = async () => {
    const cron = newCron.trim();
    if (!cron) return;
    try {
      const result = await api.validateCron(cron);
      if (!result.valid) {
        setCronPreview(null);
        toast.error(`Invalid cron: ${result.error}`);
        return;
      }
      setUiTriggers((prev) => [...prev, { kind: 'cron', spec: cron }]);
      setNewCron('');
      setCronPreview(null);
      markDirty();
    } catch (err) {
      toast.error('Cron validation failed', {
        description: err instanceof Error ? err.message : undefined,
      });
    }
  };

  useEffect(() => {
    const cron = newCron.trim();
    if (!cron) {
      setCronPreview(null);
      return;
    }
    const handle = setTimeout(() => {
      api
        .validateCron(cron)
        .then((result) => {
          if (!result.valid) {
            setCronPreview(result.error ? `invalid: ${result.error}` : 'invalid');
          } else {
            const runs = result.next_runs
              .slice(0, 3)
              .map((ts) =>
                new Date(ts * 1000).toLocaleString([], {
                  weekday: 'short',
                  hour: '2-digit',
                  minute: '2-digit',
                })
              )
              .join(', ');
            setCronPreview(runs ? `next: ${runs}` : 'never fires');
          }
        })
        .catch(() => setCronPreview(null));
    }, 350);
    return () => clearTimeout(handle);
  }, [newCron]);

  const hashtagChannels = useMemo(() => channels.filter((c) => c.key), [channels]);

  if (loadError) {
    return (
      <div className="flex-1 flex flex-col items-center justify-center gap-3 text-muted-foreground">
        <div>{loadError}</div>
        <Button variant="outline" size="sm" onClick={onBack}>
          Back to Bots
        </Button>
      </div>
    );
  }

  if (!bot) {
    return (
      <div className="flex-1 flex items-center justify-center text-muted-foreground">
        Loading bot…
      </div>
    );
  }

  const genericCronHandler = bot.declared_crons.length === 0 && code.includes('on_cron()');

  return (
    <div className="flex-1 flex flex-col min-h-0">
      {/* Editor header — stacks into two rows below md so Save and the other
          actions stay reachable */}
      <div className="flex flex-col gap-2 md:flex-row md:items-center md:gap-2.5 px-4 py-2.5 border-b border-border flex-shrink-0">
        <div className="flex flex-wrap items-center gap-x-2.5 gap-y-1 min-w-0 md:flex-1">
          <button
            type="button"
            onClick={onBack}
            className="text-muted-foreground hover:text-foreground transition-colors flex-shrink-0"
            aria-label="Back to bots"
          >
            <ArrowLeft className="h-4 w-4" />
          </button>
          <button
            type="button"
            onClick={onBack}
            className="text-[0.8125rem] text-muted-foreground hover:text-foreground"
          >
            Bots
          </button>
          <span className="text-[0.8125rem] text-muted-foreground">/</span>
          {renaming ? (
            <Input
              value={nameDraft}
              autoFocus
              onChange={(e) => setNameDraft(e.target.value)}
              onFocus={(e) => e.currentTarget.select()}
              onBlur={() => void handleRename()}
              onKeyDown={(e) => {
                if (e.key === 'Enter') {
                  e.preventDefault();
                  e.currentTarget.blur();
                }
                if (e.key === 'Escape') {
                  e.preventDefault();
                  setRenaming(false);
                }
              }}
              aria-label="Bot name"
              className="h-7 w-44 text-base font-semibold"
            />
          ) : (
            <button
              type="button"
              onClick={() => {
                setNameDraft(bot.name);
                setRenaming(true);
              }}
              className="font-semibold text-base tracking-tight hover:text-foreground/80 cursor-text max-w-full truncate"
              title="Click to rename"
            >
              {bot.name}
            </button>
          )}
          <span className="text-[0.625rem] uppercase tracking-wider bg-muted text-muted-foreground rounded px-1.5 py-0.5 whitespace-nowrap">
            {bot.category}
          </span>
          {bot.builtin_key && (
            <span className="text-[0.6875rem] bg-info/15 text-info rounded-full px-2 py-0.5 whitespace-nowrap">
              built-in {bot.builtin_version ?? ''}
              {bot.modified ? ' · modified' : ''}
            </span>
          )}
          {dirty && (
            <span className="text-[0.6875rem] text-warning whitespace-nowrap">unsaved changes</span>
          )}
        </div>
        <div className="flex items-center gap-2.5 flex-shrink-0">
          <label className="flex items-center gap-2 cursor-pointer text-xs text-muted-foreground">
            <input
              type="checkbox"
              checked={enabled}
              onChange={(e) => {
                setEnabled(e.target.checked);
                markDirty();
              }}
              className="w-4 h-4 rounded border-input accent-primary"
            />
            Enabled
          </label>
          {bot.builtin_key && (
            <Button
              variant="ghost"
              size="sm"
              className="h-7 px-2 text-xs"
              onClick={() => void handleReset()}
            >
              Reset to default
            </Button>
          )}
          <Button
            variant="outline"
            size="sm"
            className="h-7 border-destructive/50 text-destructive hover:bg-destructive/10"
            onClick={() => void handleDelete()}
            onBlur={() => setConfirmDelete(false)}
          >
            {confirmDelete ? 'Confirm delete' : 'Delete'}
          </Button>
          <Button size="sm" className="h-7" onClick={() => void handleSave()} disabled={saving}>
            {saving ? 'Saving…' : 'Save'}
          </Button>
        </div>
      </div>

      {/* Sub-tab bar — the tab strip scrolls horizontally when it cannot fit */}
      <div className="flex items-center gap-3 px-4 py-2 border-b border-border flex-shrink-0">
        <div className="max-w-full overflow-x-auto">
          <div className="inline-flex gap-0.5 bg-muted rounded-lg p-[3px]">
            {EDITOR_TABS.map((entry) => (
              <button
                key={entry.id}
                type="button"
                onClick={() => setTab(entry.id)}
                className={cn(
                  'px-3.5 py-1 rounded-md text-sm font-medium transition-colors whitespace-nowrap',
                  tab === entry.id
                    ? 'bg-background text-foreground shadow-sm'
                    : 'text-muted-foreground hover:text-foreground'
                )}
              >
                {entry.label}
              </button>
            ))}
          </div>
        </div>
        <div className="flex-1" />
        <span className="hidden md:block text-[0.6875rem] text-muted-foreground">
          Runs in-process · 10s timeout · sends spaced by the engine
        </span>
      </div>

      {/* ── Settings ── */}
      {tab === 'settings' && (
        <div className="flex-1 min-h-0 overflow-y-auto p-4 flex flex-col md:flex-row gap-8">
          <div className="flex-1 max-w-md flex flex-col gap-5">
            <div>
              <SectionTitle
                title="Where it runs"
                hint="Which conversations this bot listens to. Triggers still apply."
              />
              <div className="inline-flex gap-0.5 bg-muted rounded-lg p-[3px] mb-2.5">
                {(['all', 'only', 'except'] as const).map((mode) => (
                  <button
                    key={mode}
                    type="button"
                    onClick={() => {
                      setScopeMode(mode);
                      markDirty();
                    }}
                    className={cn(
                      'px-3 py-1 rounded-md text-xs transition-colors',
                      scopeMode === mode
                        ? 'bg-background text-foreground shadow-sm'
                        : 'text-muted-foreground hover:text-foreground'
                    )}
                  >
                    {mode === 'all' ? 'All channels' : mode === 'only' ? 'Only…' : 'All except…'}
                  </button>
                ))}
              </div>
              {scopeMode !== 'all' && (
                <div className="flex flex-wrap gap-1.5 mb-2">
                  {scopeList.map((key) => {
                    const channel = channels.find((c) => c.key === key);
                    return (
                      <span
                        key={key}
                        className="inline-flex items-center gap-1.5 text-xs bg-primary/10 text-primary rounded-md px-2 py-1"
                      >
                        {channel?.name ?? `${key.slice(0, 8)}…`}
                        <button
                          type="button"
                          onClick={() => {
                            setScopeList((prev) => prev.filter((k) => k !== key));
                            markDirty();
                          }}
                          aria-label={`Remove ${channel?.name ?? key}`}
                        >
                          <X className="h-3 w-3 opacity-70" />
                        </button>
                      </span>
                    );
                  })}
                  <select
                    value=""
                    onChange={(e) => {
                      if (e.target.value && !scopeList.includes(e.target.value)) {
                        setScopeList((prev) => [...prev, e.target.value]);
                        markDirty();
                      }
                    }}
                    className="h-7 rounded-md border border-dashed border-input bg-transparent px-2 text-xs text-muted-foreground"
                  >
                    <option value="">+ Add channel</option>
                    {hashtagChannels
                      .filter((c) => !scopeList.includes(c.key))
                      .map((c) => (
                        <option key={c.key} value={c.key}>
                          {c.name}
                        </option>
                      ))}
                  </select>
                </div>
              )}
              <label className="flex items-center gap-2.5 cursor-pointer mt-2">
                <input
                  type="checkbox"
                  checked={respondToDms}
                  onChange={(e) => {
                    setRespondToDms(e.target.checked);
                    markDirty();
                  }}
                  className="w-4 h-4 rounded border-input accent-primary"
                />
                <span className="text-[0.8125rem]">Respond to direct messages</span>
              </label>
              <label className="flex items-start gap-2.5 cursor-pointer mt-2">
                <input
                  type="checkbox"
                  checked={adminOnly}
                  onChange={(e) => {
                    setAdminOnly(e.target.checked);
                    markDirty();
                  }}
                  className="w-4 h-4 rounded border-input accent-primary mt-0.5"
                />
                <span className="text-[0.8125rem]">
                  Admins only{' '}
                  <span className="text-[0.6875rem] text-muted-foreground">
                    — answer only senders on the Admin users list (Bots › Engine)
                  </span>
                </span>
              </label>
            </div>

            <div className="border-t border-border pt-4">
              <SectionTitle
                title="Limits"
                hint="Engine-wide limits (global reply, per-user, TX spacing) stack on top."
              />
              <div className="flex gap-3">
                <div className="flex-1">
                  <div className="text-xs text-muted-foreground mb-1">Cooldown (s)</div>
                  <Input
                    type="number"
                    value={cooldown}
                    min={0}
                    onChange={(e) => {
                      setCooldown(e.target.value);
                      markDirty();
                    }}
                    className="h-8 font-mono text-[0.8125rem]"
                  />
                </div>
                <div className="flex-1">
                  <div className="text-xs text-muted-foreground mb-1">Per-user (s)</div>
                  <Input
                    type="number"
                    value={perUserCooldown}
                    min={0}
                    onChange={(e) => {
                      setPerUserCooldown(e.target.value);
                      markDirty();
                    }}
                    className="h-8 font-mono text-[0.8125rem]"
                  />
                </div>
                <div className="flex-1">
                  <div className="text-xs text-muted-foreground mb-1">Queue threshold (s)</div>
                  <Input
                    type="number"
                    value={queueThreshold}
                    min={0}
                    onChange={(e) => {
                      setQueueThreshold(e.target.value);
                      markDirty();
                    }}
                    className="h-8 font-mono text-[0.8125rem]"
                  />
                </div>
              </div>
              <p className="text-[0.6875rem] text-muted-foreground mt-2">
                A request arriving within the queue threshold of cooldown expiry is queued instead
                of dropped.
              </p>
            </div>
          </div>

          <div className="flex-1 max-w-md">
            <SectionTitle
              title="Bot settings"
              hint="Declared by the bot's settings schema — read in code via ctx.settings."
            />
            {bot.settings_schema.length === 0 ? (
              <p className="text-xs text-muted-foreground">
                This bot declares no settings. Add a settings_schema to BOT_META in the code to get
                typed fields here.
              </p>
            ) : (
              <div className="flex flex-col gap-3.5">
                {bot.settings_schema.map((field) => (
                  <SchemaField
                    key={field.key}
                    field={field}
                    value={settings[field.key]}
                    onChange={(value) => {
                      setSettings((prev) => ({ ...prev, [field.key]: value }));
                      markDirty();
                    }}
                  />
                ))}
              </div>
            )}
          </div>
        </div>
      )}

      {/* ── Triggers ── */}
      {tab === 'triggers' && (
        <div className="flex-1 min-h-0 overflow-y-auto p-4 flex flex-col gap-4 max-w-2xl">
          <div className="border border-border rounded-lg p-3.5">
            <div className="flex items-center gap-2 mb-2">
              <span className="text-sm font-semibold">Declared in code</span>
              <span className="text-[0.6875rem] text-muted-foreground">
                edit the Code tab to change these
              </span>
            </div>
            <BotTriggerChips bot={bot} limit={16} />
            {bot.declared_keywords.length === 0 &&
              bot.declared_crons.length === 0 &&
              bot.declared_events.length === 0 &&
              bot.declared_webhooks.length === 0 &&
              !bot.is_legacy && (
                <p className="text-xs text-muted-foreground">No code-declared triggers.</p>
              )}
          </div>

          <div className="border border-border rounded-lg p-3.5">
            <div className="flex items-center gap-2 mb-2">
              <span className="text-sm font-semibold">Extra keywords</span>
              <span className="text-[0.6875rem] text-muted-foreground">
                routed to the bot's generic @bot.on_keyword() handler
              </span>
            </div>
            <div className="flex flex-wrap gap-1.5 mb-2.5">
              {uiTriggers
                .filter((t) => t.kind === 'keyword')
                .map((t) => (
                  <span
                    key={t.spec}
                    className="inline-flex items-center gap-1.5 font-mono text-xs bg-muted rounded-md px-2 py-1"
                  >
                    {t.spec}
                    <button
                      type="button"
                      onClick={() => {
                        setUiTriggers((prev) => prev.filter((x) => x !== t));
                        markDirty();
                      }}
                      aria-label={`Remove keyword ${t.spec}`}
                    >
                      <X className="h-3 w-3 opacity-70" />
                    </button>
                  </span>
                ))}
            </div>
            <div className="flex gap-2">
              <Input
                value={newKeyword}
                onChange={(e) => setNewKeyword(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === 'Enter') handleAddKeyword();
                }}
                placeholder="add keyword…"
                className="h-8 w-44 font-mono text-xs"
              />
              <Button variant="outline" size="sm" className="h-8" onClick={handleAddKeyword}>
                <Plus className="h-3.5 w-3.5 mr-1" /> Add
              </Button>
            </div>
          </div>

          <div className="border border-border rounded-lg p-3.5">
            <div className="flex items-center gap-2 mb-2">
              <span className="text-sm font-semibold">Cron schedules</span>
              <span className="text-[0.6875rem] text-muted-foreground">
                5-field crontab or @daily/@hourly/@weekly · day-of-week 0 = Monday
              </span>
            </div>
            {!genericCronHandler && bot.declared_crons.length === 0 && (
              <p className="text-xs text-warning mb-2">
                This bot has no @bot.on_cron() handler — UI schedules added here will not fire until
                the code declares one.
              </p>
            )}
            <div className="flex flex-col gap-1.5 mb-2.5">
              {uiTriggers
                .filter((t) => t.kind === 'cron')
                .map((t) => (
                  <div
                    key={t.spec}
                    className="flex items-center gap-2.5 bg-muted rounded-md px-2.5 py-1.5"
                  >
                    <span className="font-mono text-xs bg-info/15 text-info rounded px-1.5 py-0.5">
                      {t.spec}
                    </span>
                    <span className="text-xs text-muted-foreground">→ on_cron(ctx)</span>
                    <div className="flex-1" />
                    <button
                      type="button"
                      onClick={() => {
                        setUiTriggers((prev) => prev.filter((x) => x !== t));
                        markDirty();
                      }}
                      aria-label={`Remove schedule ${t.spec}`}
                    >
                      <X className="h-3.5 w-3.5 opacity-70" />
                    </button>
                  </div>
                ))}
            </div>
            <div className="flex gap-2 items-center">
              <Input
                value={newCron}
                onChange={(e) => setNewCron(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === 'Enter') void handleAddCron();
                }}
                placeholder="0 7 * * *"
                className="h-8 w-44 font-mono text-xs"
              />
              <Button
                variant="outline"
                size="sm"
                className="h-8"
                onClick={() => void handleAddCron()}
              >
                <Plus className="h-3.5 w-3.5 mr-1" /> Add schedule
              </Button>
              {cronPreview && (
                <span
                  className={cn(
                    'text-[0.6875rem]',
                    cronPreview.startsWith('invalid') ? 'text-destructive' : 'text-muted-foreground'
                  )}
                >
                  {cronPreview}
                </span>
              )}
            </div>
          </div>

          <p className="text-[0.6875rem] text-muted-foreground">
            Mesh events (@bot.on_event) and webhooks (@bot.on_webhook) are declared in code —
            webhook endpoints listen on POST /api/hooks/&lt;slug&gt; and require the bot's
            webhook_token setting.
          </p>
        </div>
      )}

      {/* ── Code ── */}
      {tab === 'code' && (
        <div className="flex-1 min-h-0 overflow-y-auto p-4 flex flex-col gap-2">
          <div className="flex flex-wrap items-center gap-x-2.5 gap-y-1 text-[0.6875rem] text-muted-foreground">
            <span className="font-mono">{bot.name}.py</span>
            <span>Python 3 · validated on save (syntax + at least one trigger)</span>
            <div className="flex-1" />
            {bot.is_legacy && (
              <span className="text-warning">legacy def bot(**kwargs) — auto-wrapped</span>
            )}
          </div>
          <Suspense
            fallback={<div className="text-sm text-muted-foreground py-8">Loading editor…</div>}
          >
            <BotCodeEditor
              value={code}
              onChange={(value) => {
                setCode(value);
                markDirty();
              }}
              height="52vh"
            />
          </Suspense>
          <div className="flex flex-wrap gap-x-4 gap-y-1 text-[0.6875rem] text-muted-foreground">
            <span>
              <span className="font-mono text-foreground">ctx.reply(text)</span> answer where
              triggered
            </span>
            <span>
              <span className="font-mono text-foreground">ctx.reply_split(text)</span> long text as
              (i/n) parts
            </span>
            <span>
              <span className="font-mono text-foreground">ctx.send("#any", text)</span>{' '}
              cross-channel
            </span>
            <span>
              <span className="font-mono text-foreground">ctx.send_dm(key, text)</span> direct
            </span>
            <span>
              <span className="font-mono text-foreground">ctx.state</span> persistent dict
            </span>
            <span>
              <span className="font-mono text-foreground">ctx.settings</span> typed settings
            </span>
            <span>
              <span className="font-mono text-foreground">ctx.http.get_json(url)</span> HTTP
            </span>
            <span>
              <span className="font-mono text-foreground">ctx.t("rt.key")</span> i18n
            </span>
          </div>
        </div>
      )}

      {/* ── Test ── */}
      {tab === 'test' && (
        <div className="flex-1 min-h-0 flex flex-col md:flex-row">
          <div className="md:w-80 md:flex-shrink-0 md:border-r border-border p-4 flex flex-col gap-3.5">
            <div>
              <SectionTitle
                title="Simulate a message"
                hint="Runs the saved code with live settings and state — nothing is transmitted."
              />
            </div>
            <div>
              <div className="text-xs text-muted-foreground mb-1">Message</div>
              <Input
                value={testText}
                onChange={(e) => setTestText(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === 'Enter') void handleRunTest();
                }}
                placeholder="wx 98101"
                className="h-8 font-mono text-[0.8125rem]"
              />
              <div className="flex gap-1.5 mt-2 flex-wrap">
                {bot.declared_keywords.slice(0, 3).map((kw) => (
                  <button
                    key={kw}
                    type="button"
                    onClick={() => setTestText(kw)}
                    className="font-mono text-[0.6875rem] border border-input text-muted-foreground rounded-md px-2 py-1 hover:text-foreground"
                  >
                    {kw}
                  </button>
                ))}
              </div>
            </div>
            <div className="flex gap-3">
              <div className="flex-1">
                <div className="text-xs text-muted-foreground mb-1">From</div>
                <Input
                  value={testSender}
                  onChange={(e) => setTestSender(e.target.value)}
                  className="h-8 text-[0.8125rem]"
                />
              </div>
              <label className="flex items-end gap-2 pb-1.5 cursor-pointer">
                <input
                  type="checkbox"
                  checked={testDm}
                  onChange={(e) => setTestDm(e.target.checked)}
                  className="w-4 h-4 rounded border-input accent-primary"
                />
                <span className="text-xs">as DM</span>
              </label>
            </div>
            <Button size="sm" onClick={() => void handleRunTest()} disabled={testRunning}>
              <Play className="h-3.5 w-3.5 mr-1.5" aria-hidden="true" />
              {testRunning ? 'Running…' : 'Run test'}
            </Button>
            <p className="text-[0.6875rem] text-muted-foreground">
              Rate limits and cooldowns are bypassed in test runs. Unsaved code changes are NOT used
              — save first.
            </p>
          </div>
          <div className="flex-1 min-w-0 p-4 overflow-y-auto flex flex-col gap-2.5">
            <div className="text-[0.625rem] uppercase tracking-wider text-muted-foreground font-medium">
              Transcript
            </div>
            {transcript.length === 0 && (
              <div className="border border-dashed border-input rounded-lg px-4 py-6 text-center text-xs text-muted-foreground">
                No test runs yet — enter a message and press Run test.
              </div>
            )}
            {transcript.map((entry, index) => (
              <div key={index} className="flex flex-col gap-1.5">
                <div className="self-end max-w-[75%] bg-msg-outgoing rounded-lg px-3 py-2 text-[0.8125rem]">
                  <span className="font-mono">{entry.input}</span>
                  <span className="text-[0.625rem] text-muted-foreground ml-2">
                    as {testSender || 'TestUser'} {testDm ? '(DM)' : 'in #test'}
                  </span>
                </div>
                {entry.response.error ? (
                  <div className="self-start max-w-[75%] border border-destructive/35 bg-destructive/10 rounded-lg px-3 py-2 text-xs font-mono">
                    {entry.response.error}
                  </div>
                ) : entry.response.replies.length === 0 ? (
                  <div className="self-start text-[0.6875rem] text-muted-foreground">
                    {entry.response.matched ? 'no reply' : 'no trigger matched'}
                  </div>
                ) : (
                  entry.response.replies.map((reply, replyIndex) => (
                    <div
                      key={replyIndex}
                      className="self-start max-w-[75%] bg-msg-incoming rounded-lg px-3 py-2 text-[0.8125rem] leading-relaxed"
                    >
                      {reply.text}
                      {reply.is_dm === false && reply.channel_key && (
                        <span className="text-[0.625rem] text-muted-foreground ml-2">
                          → channel
                        </span>
                      )}
                    </div>
                  ))
                )}
                <div className="self-start font-mono text-[0.6875rem] text-muted-foreground">
                  {entry.response.trigger ?? 'no trigger'} · {entry.response.duration_ms}ms ·{' '}
                  {entry.response.replies.length} repl
                  {entry.response.replies.length === 1 ? 'y' : 'ies'}
                </div>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* ── Activity ── */}
      {tab === 'activity' && (
        <div className="flex-1 min-h-0 overflow-y-auto p-4 flex flex-col gap-3">
          {bot.last_error && (
            <div className="border border-destructive/35 bg-destructive/10 rounded-lg px-3 py-2.5 max-w-3xl">
              <div className="text-[0.625rem] uppercase tracking-wider text-destructive font-semibold mb-1">
                Latest error
              </div>
              <div className="font-mono text-[0.6875rem] leading-relaxed">{bot.last_error}</div>
            </div>
          )}
          {/* Fixed-width columns; the table scrolls horizontally on narrow screens */}
          <div className="border border-border rounded-lg overflow-x-auto max-w-4xl">
            <div className="min-w-[44rem]">
              <div className="flex items-center gap-2.5 px-3 py-2 bg-muted text-[0.625rem] uppercase tracking-wider text-muted-foreground font-medium">
                <span className="w-32">Time</span>
                <span className="w-28">Trigger</span>
                <span className="w-28">From</span>
                <span className="w-24">Where</span>
                <span className="w-16">Exec</span>
                <span className="flex-1">Result</span>
              </div>
              {runs.length === 0 && (
                <div className="px-3 py-4 text-xs text-muted-foreground">No runs recorded yet.</div>
              )}
              {runs.map((run) => (
                <div
                  key={run.id}
                  className="flex items-center gap-2.5 px-3 py-1.5 border-t border-border/50 text-xs"
                >
                  <span className="w-32 font-mono text-muted-foreground">
                    {new Date(run.started_at * 1000).toLocaleString([], {
                      month: 'short',
                      day: 'numeric',
                      hour: '2-digit',
                      minute: '2-digit',
                    })}
                  </span>
                  <span className="w-28 truncate">
                    <span className="font-mono text-[0.6875rem] bg-muted rounded px-1.5 py-0.5">
                      {run.trigger}
                    </span>
                  </span>
                  <span className="w-28 truncate">{run.sender_name ?? '—'}</span>
                  <span className="w-24 truncate text-muted-foreground">
                    {run.is_dm ? 'DM' : (run.channel_name ?? run.channel_key?.slice(0, 8) ?? '—')}
                  </span>
                  <span className="w-16 font-mono text-muted-foreground">
                    {run.duration_ms != null ? `${(run.duration_ms / 1000).toFixed(1)}s` : '—'}
                  </span>
                  <span
                    className={cn(
                      'flex-1 truncate',
                      (run.result === 'error' || run.result === 'timeout') && 'text-destructive'
                    )}
                  >
                    {run.error ?? `${run.result}${run.replies ? ` (${run.replies})` : ''}`}
                    {run.test_run ? ' · test' : ''}
                  </span>
                </div>
              ))}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
