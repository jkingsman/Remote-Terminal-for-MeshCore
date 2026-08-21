import { useEffect, useMemo, useState } from 'react';
import { Plus } from 'lucide-react';

import { api } from '../../api';
import type { BotSchedule, Channel } from '../../types';
import { Button } from '../ui/button';
import { Input } from '../ui/input';
import { toast } from '../ui/sonner';
import { cn } from '@/lib/utils';

/** Standalone cron messages, independent of any bot. */
export function SchedulerTab({ channels }: { channels: Channel[] }) {
  const [schedules, setSchedules] = useState<BotSchedule[]>([]);
  const [loading, setLoading] = useState(true);
  const [showForm, setShowForm] = useState(false);
  const [confirmDeleteId, setConfirmDeleteId] = useState<string | null>(null);

  // Create form state
  const [label, setLabel] = useState('');
  const [cron, setCron] = useState('');
  const [cronPreview, setCronPreview] = useState<string | null>(null);
  const [channelKey, setChannelKey] = useState('');
  const [message, setMessage] = useState('');
  const [region, setRegion] = useState('');
  const [creating, setCreating] = useState(false);

  const targetChannels = useMemo(() => channels.filter((c) => c.key), [channels]);

  useEffect(() => {
    api
      .getBotSchedules()
      .then(setSchedules)
      .catch((err: unknown) =>
        toast.error('Failed to load schedules', {
          description: err instanceof Error ? err.message : undefined,
        })
      )
      .finally(() => setLoading(false));
  }, []);

  // Live cron validation, debounced like the bot editor's trigger form.
  useEffect(() => {
    const spec = cron.trim();
    if (!spec) {
      setCronPreview(null);
      return;
    }
    const handle = setTimeout(() => {
      api
        .validateCron(spec)
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
  }, [cron]);

  const resetForm = () => {
    setLabel('');
    setCron('');
    setCronPreview(null);
    setChannelKey('');
    setMessage('');
    setRegion('');
  };

  const handleCreate = async () => {
    if (!label.trim() || !cron.trim() || !channelKey || !message.trim()) {
      toast.error('Label, cron, channel and message are required');
      return;
    }
    setCreating(true);
    try {
      const created = await api.createBotSchedule({
        label: label.trim(),
        cron: cron.trim(),
        channel_key: channelKey,
        message: message.trim(),
        flood_scope: region.trim() ? region.trim() : null,
      });
      setSchedules((prev) => [...prev, created]);
      resetForm();
      setShowForm(false);
      toast.success(`${created.label} scheduled`);
    } catch (err) {
      toast.error('Failed to create schedule', {
        description: err instanceof Error ? err.message : undefined,
      });
    } finally {
      setCreating(false);
    }
  };

  const handleToggleEnabled = async (schedule: BotSchedule) => {
    try {
      const updated = await api.updateBotSchedule(schedule.id, { enabled: !schedule.enabled });
      setSchedules((prev) => prev.map((s) => (s.id === schedule.id ? updated : s)));
    } catch (err) {
      toast.error(`Failed to ${schedule.enabled ? 'pause' : 'enable'} ${schedule.label}`, {
        description: err instanceof Error ? err.message : undefined,
      });
    }
  };

  const handleDelete = async (schedule: BotSchedule) => {
    if (confirmDeleteId !== schedule.id) {
      setConfirmDeleteId(schedule.id);
      return;
    }
    try {
      await api.deleteBotSchedule(schedule.id);
      setSchedules((prev) => prev.filter((s) => s.id !== schedule.id));
      toast.success(`${schedule.label} deleted`);
    } catch (err) {
      toast.error('Delete failed', {
        description: err instanceof Error ? err.message : undefined,
      });
    } finally {
      setConfirmDeleteId(null);
    }
  };

  return (
    <div className="flex-1 min-h-0 overflow-y-auto p-4 flex flex-col gap-3">
      <div className="flex items-start gap-3">
        <div className="flex-1 border border-primary/20 bg-primary/5 rounded-lg px-3 py-2 text-xs text-muted-foreground">
          Standalone cron messages, independent of any bot. 5-field crontab or{' '}
          <span className="font-mono text-foreground">@daily @hourly @weekly</span> presets ·
          day-of-week: <span className="text-foreground">0 = Monday</span> · supports placeholders
          like <span className="font-mono text-foreground">{'{total_contacts}'}</span>,{' '}
          <span className="font-mono text-foreground">{'{messages_24h}'}</span>
        </div>
        <Button size="sm" className="flex-shrink-0" onClick={() => setShowForm((v) => !v)}>
          <Plus className="h-3.5 w-3.5 mr-1" aria-hidden="true" />
          New schedule
        </Button>
      </div>

      {showForm && (
        <div className="border border-border rounded-lg p-3.5 flex flex-col gap-3 max-w-2xl">
          <div className="flex gap-3">
            <div className="flex-1">
              <div className="text-xs text-muted-foreground mb-1">Label</div>
              <Input
                value={label}
                onChange={(e) => setLabel(e.target.value)}
                placeholder="Morning digest"
                className="h-8 text-[0.8125rem]"
              />
            </div>
            <div className="flex-1">
              <div className="text-xs text-muted-foreground mb-1">Cron</div>
              <Input
                value={cron}
                onChange={(e) => setCron(e.target.value)}
                placeholder="0 7 * * *"
                className="h-8 font-mono text-[0.8125rem]"
              />
              {cronPreview && (
                <div
                  className={cn(
                    'text-[0.6875rem] mt-1',
                    cronPreview.startsWith('invalid') ? 'text-destructive' : 'text-muted-foreground'
                  )}
                >
                  {cronPreview}
                </div>
              )}
            </div>
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
          </div>
          <div>
            <div className="text-xs text-muted-foreground mb-1">Message</div>
            <Input
              value={message}
              onChange={(e) => setMessage(e.target.value)}
              placeholder="Good morning! {total_contacts} nodes heard, {messages_24h} messages in 24h."
              className="h-8 text-[0.8125rem]"
            />
            <div className="text-[0.6875rem] text-muted-foreground mt-1">
              Placeholders like {'{total_contacts}'} and {'{messages_24h}'} are filled at send time.
            </div>
          </div>
          <div className="flex items-end gap-3">
            <div className="w-44">
              <div className="text-xs text-muted-foreground mb-1">Region (optional)</div>
              <Input
                value={region}
                onChange={(e) => setRegion(e.target.value)}
                placeholder="channel default"
                className="h-8 text-[0.8125rem]"
              />
            </div>
            <div className="flex-1" />
            <Button size="sm" onClick={() => void handleCreate()} disabled={creating}>
              {creating ? 'Creating…' : 'Create'}
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
        <div className="flex items-center gap-2.5 px-3 py-2 bg-muted text-[0.625rem] uppercase tracking-wider text-muted-foreground font-medium">
          <span className="w-6" />
          <span className="w-32">Label</span>
          <span className="w-28">Cron</span>
          <span className="w-28">Target</span>
          <span className="flex-1">Message</span>
          <span className="w-36">Next run</span>
          <span className="w-24">Last result</span>
          <span className="w-16" />
        </div>
        {loading && (
          <div className="px-3 py-4 text-xs text-muted-foreground">Loading schedules…</div>
        )}
        {!loading && schedules.length === 0 && (
          <div className="px-3 py-4 text-xs text-muted-foreground">No scheduled messages yet.</div>
        )}
        {schedules.map((schedule) => (
          <div
            key={schedule.id}
            className="flex items-center gap-2.5 px-3 py-1.5 border-t border-border/50 text-xs"
          >
            <label className="w-6 flex items-center cursor-pointer">
              <input
                type="checkbox"
                checked={schedule.enabled}
                onChange={() => void handleToggleEnabled(schedule)}
                className="w-4 h-4 rounded border-input accent-primary"
                aria-label={`Enable ${schedule.label}`}
              />
            </label>
            <span
              className={cn(
                'w-32 text-[0.8125rem] font-medium truncate',
                !schedule.enabled && 'text-muted-foreground'
              )}
            >
              {schedule.label}
            </span>
            <span className="w-28 flex-shrink-0">
              <span className="font-mono text-[0.6875rem] bg-info/15 text-info rounded px-1.5 py-0.5">
                {schedule.cron}
              </span>
            </span>
            <span className="w-28 flex-shrink-0 truncate">
              <span className="text-[0.6875rem] bg-primary/10 text-primary rounded px-1.5 py-0.5 whitespace-nowrap">
                → {schedule.channel_name ?? `${schedule.channel_key.slice(0, 8)}…`}
              </span>
            </span>
            <span className="flex-1 min-w-0 text-muted-foreground truncate">
              {schedule.message}
            </span>
            <span className="w-36 flex-shrink-0 text-[0.6875rem] text-muted-foreground">
              {schedule.enabled && schedule.next_run_at
                ? new Date(schedule.next_run_at * 1000).toLocaleString([], {
                    month: 'short',
                    day: 'numeric',
                    hour: '2-digit',
                    minute: '2-digit',
                  })
                : 'paused'}
            </span>
            <span
              className={cn(
                'w-24 flex-shrink-0 text-[0.6875rem] truncate',
                schedule.last_result?.startsWith('error')
                  ? 'text-destructive'
                  : 'text-muted-foreground'
              )}
              title={schedule.last_result ?? undefined}
            >
              {schedule.last_result ?? '—'}
            </span>
            <span className="w-16 flex-shrink-0 flex justify-end">
              <Button
                variant="ghost"
                size="sm"
                className="h-6 px-2 text-xs text-destructive hover:bg-destructive/10"
                onClick={() => void handleDelete(schedule)}
                onBlur={() => setConfirmDeleteId(null)}
              >
                {confirmDeleteId === schedule.id ? 'Confirm' : 'Delete'}
              </Button>
            </span>
          </div>
        ))}
      </div>

      <p className="text-[0.6875rem] text-muted-foreground">
        Bot-owned cron triggers live on each bot's Triggers tab — this list is for plain scheduled
        messages.
      </p>
    </div>
  );
}
