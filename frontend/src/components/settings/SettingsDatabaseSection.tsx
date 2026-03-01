import { useState, useEffect } from 'react';
import { Input } from '../ui/input';
import { Label } from '../ui/label';
import { Button } from '../ui/button';
import { Separator } from '../ui/separator';
import { toast } from '../ui/sonner';
import { api } from '../../api';
import { formatTime } from '../../utils/messageParser';
import type {
  AppSettings,
  AppSettingsUpdate,
  Channel,
  Contact,
  HealthStatus,
} from '../../types';

export function SettingsDatabaseSection({
  appSettings,
  health,
  contacts = [],
  channels = [],
  onSaveAppSettings,
  onHealthRefresh,
  blockedKeys = [],
  blockedNames = [],
  onToggleBlockedKey,
  onToggleBlockedName,
  className,
}: {
  appSettings: AppSettings;
  health: HealthStatus | null;
  contacts?: Contact[];
  channels?: Channel[];
  onSaveAppSettings: (update: AppSettingsUpdate) => Promise<void>;
  onHealthRefresh: () => Promise<void>;
  blockedKeys?: string[];
  blockedNames?: string[];
  onToggleBlockedKey?: (key: string) => void;
  onToggleBlockedName?: (name: string) => void;
  className?: string;
}) {
  const [retentionDays, setRetentionDays] = useState('14');
  const [cleaning, setCleaning] = useState(false);
  const [purgingDecryptedRaw, setPurgingDecryptedRaw] = useState(false);
  const [autoDecryptOnAdvert, setAutoDecryptOnAdvert] = useState(false);
  const [appriseEnabled, setAppriseEnabled] = useState(false);
  const [appriseUrl, setAppriseUrl] = useState('');
  const [appriseMode, setAppriseMode] = useState<'all' | 'selected'>('all');
  const [apprisePreserveIdentity, setApprisePreserveIdentity] = useState(true);
  const [appriseTargets, setAppriseTargets] = useState<
    Array<{ type: 'channel' | 'contact'; id: string }>
  >([]);

  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setAutoDecryptOnAdvert(appSettings.auto_decrypt_dm_on_advert);
    setAppriseEnabled(appSettings.apprise_enabled);
    setAppriseUrl(appSettings.apprise_url || '');
    setAppriseMode(appSettings.apprise_mode || 'all');
    setApprisePreserveIdentity(appSettings.apprise_preserve_identity ?? true);
    setAppriseTargets(appSettings.apprise_targets || []);
  }, [appSettings]);

  const toggleAppriseTarget = (type: 'channel' | 'contact', id: string) => {
    setAppriseTargets((prev) => {
      const exists = prev.some((t) => t.type === type && t.id === id);
      if (exists) return prev.filter((t) => !(t.type === type && t.id === id));
      return [...prev, { type, id }];
    });
  };

  const handleCleanup = async () => {
    const days = parseInt(retentionDays, 10);
    if (isNaN(days) || days < 1) {
      toast.error('Invalid retention days', {
        description: 'Retention days must be at least 1',
      });
      return;
    }

    setCleaning(true);

    try {
      const result = await api.runMaintenance({ pruneUndecryptedDays: days });
      toast.success('Database cleanup complete', {
        description: `Deleted ${result.packets_deleted} old packet${result.packets_deleted === 1 ? '' : 's'}`,
      });
      await onHealthRefresh();
    } catch (err) {
      console.error('Failed to run maintenance:', err);
      toast.error('Database cleanup failed', {
        description: err instanceof Error ? err.message : 'Unknown error',
      });
    } finally {
      setCleaning(false);
    }
  };

  const handlePurgeDecryptedRawPackets = async () => {
    setPurgingDecryptedRaw(true);

    try {
      const result = await api.runMaintenance({ purgeLinkedRawPackets: true });
      toast.success('Decrypted raw packets purged', {
        description: `Deleted ${result.packets_deleted} raw packet${result.packets_deleted === 1 ? '' : 's'}`,
      });
      await onHealthRefresh();
    } catch (err) {
      console.error('Failed to purge decrypted raw packets:', err);
      toast.error('Failed to purge decrypted raw packets', {
        description: err instanceof Error ? err.message : 'Unknown error',
      });
    } finally {
      setPurgingDecryptedRaw(false);
    }
  };

  const handleSave = async () => {
    setBusy(true);
    setError(null);

    try {
      await onSaveAppSettings({
        auto_decrypt_dm_on_advert: autoDecryptOnAdvert,
        apprise_enabled: appriseEnabled,
        apprise_url: appriseUrl.trim(),
        apprise_mode: appriseMode,
        apprise_preserve_identity: apprisePreserveIdentity,
        apprise_targets: appriseTargets,
      });
      toast.success('Database settings saved');
    } catch (err) {
      console.error('Failed to save database settings:', err);
      setError(err instanceof Error ? err.message : 'Failed to save');
      toast.error('Failed to save settings');
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className={className}>
      <div className="space-y-3">
        <div className="flex justify-between items-center">
          <span className="text-sm text-muted-foreground">Database size</span>
          <span className="font-medium">{health?.database_size_mb ?? '?'} MB</span>
        </div>

        {health?.oldest_undecrypted_timestamp ? (
          <div className="flex justify-between items-center">
            <span className="text-sm text-muted-foreground">Oldest undecrypted packet</span>
            <span className="font-medium">
              {formatTime(health.oldest_undecrypted_timestamp)}
              <span className="text-muted-foreground ml-1">
                ({Math.floor((Date.now() / 1000 - health.oldest_undecrypted_timestamp) / 86400)}{' '}
                days old)
              </span>
            </span>
          </div>
        ) : (
          <div className="flex justify-between items-center">
            <span className="text-sm text-muted-foreground">Oldest undecrypted packet</span>
            <span className="text-muted-foreground">None</span>
          </div>
        )}
      </div>

      <Separator />

      <div className="space-y-3">
        <Label>Delete Undecrypted Packets</Label>
        <p className="text-xs text-muted-foreground">
          Permanently deletes stored raw packets containing DMs and channel messages that have not
          yet been decrypted. These packets are retained in case you later obtain the correct key —
          once deleted, these messages can never be recovered or decrypted.
        </p>
        <div className="flex gap-2 items-end">
          <div className="space-y-1">
            <Label htmlFor="retention-days" className="text-xs">
              Older than (days)
            </Label>
            <Input
              id="retention-days"
              type="number"
              min="1"
              max="365"
              value={retentionDays}
              onChange={(e) => setRetentionDays(e.target.value)}
              className="w-24"
            />
          </div>
          <Button
            variant="outline"
            onClick={handleCleanup}
            disabled={cleaning}
            className="border-destructive/50 text-destructive hover:bg-destructive/10"
          >
            {cleaning ? 'Deleting...' : 'Permanently Delete'}
          </Button>
        </div>
      </div>

      <Separator />

      <div className="space-y-3">
        <Label>Purge Archival Raw Packets</Label>
        <p className="text-xs text-muted-foreground">
          Deletes archival copies of raw packet bytes for messages that are already decrypted and
          visible in your chat history.{' '}
          <em className="text-muted-foreground/80">
            This will not affect any displayed messages or app functionality.
          </em>{' '}
          The raw bytes are only useful for manual packet analysis.
        </p>
        <Button
          variant="outline"
          onClick={handlePurgeDecryptedRawPackets}
          disabled={purgingDecryptedRaw}
          className="w-full border-warning/50 text-warning hover:bg-warning/10"
        >
          {purgingDecryptedRaw ? 'Purging Archival Raw Packets...' : 'Purge Archival Raw Packets'}
        </Button>
      </div>

      <Separator />

      <div className="space-y-3">
        <Label>DM Decryption</Label>
        <label className="flex items-center gap-3 cursor-pointer">
          <input
            type="checkbox"
            checked={autoDecryptOnAdvert}
            onChange={(e) => setAutoDecryptOnAdvert(e.target.checked)}
            className="w-4 h-4 rounded border-input accent-primary"
          />
          <span className="text-sm">Auto-decrypt historical DMs when new contact advertises</span>
        </label>
        <p className="text-xs text-muted-foreground">
          When enabled, the server will automatically try to decrypt stored DM packets when a new
          contact sends an advertisement. This may cause brief delays on large packet backlogs.
        </p>
      </div>

      <Separator />

      <div className="space-y-3">
        <Label>External Notifications (Apprise)</Label>
        <label className="flex items-center gap-3 cursor-pointer">
          <input
            type="checkbox"
            checked={appriseEnabled}
            onChange={(e) => setAppriseEnabled(e.target.checked)}
            className="w-4 h-4 rounded border-input accent-primary"
          />
          <span className="text-sm">Enable incoming message notifications</span>
        </label>
        <div className="space-y-1">
          <Label htmlFor="apprise-url" className="text-xs">
            Apprise URL(s), one per line
          </Label>
          <textarea
            id="apprise-url"
            value={appriseUrl}
            onChange={(e) => setAppriseUrl(e.target.value)}
            rows={4}
            className="w-full rounded-md border border-input bg-background px-3 py-2 text-sm"
            placeholder={'discord://webhook_id/webhook_token\nhttps://discord.com/api/webhooks/...'}
          />
          <p className="text-xs text-muted-foreground">
            Supported destinations:{' '}
            <a
              className="underline"
              href="https://github.com/caronc/apprise"
              target="_blank"
              rel="noreferrer"
            >
              Apprise GitHub
            </a>
          </p>
        </div>
        <div className="space-y-2">
          <Label className="text-xs">Notify For</Label>
          <label className="flex items-center gap-2 cursor-pointer">
            <input
              type="radio"
              name="apprise-mode"
              checked={appriseMode === 'all'}
              onChange={() => setAppriseMode('all')}
              className="w-4 h-4 accent-primary"
            />
            <span className="text-sm">All incoming messages</span>
          </label>
          <label className="flex items-center gap-2 cursor-pointer">
            <input
              type="radio"
              name="apprise-mode"
              checked={appriseMode === 'selected'}
              onChange={() => setAppriseMode('selected')}
              className="w-4 h-4 accent-primary"
            />
            <span className="text-sm">Only selected channels/DMs</span>
          </label>
        </div>
        {appriseMode === 'selected' && (
          <div className="space-y-2 rounded-md border border-input p-3">
            {channels.length > 0 && (
              <div>
                <span className="text-xs text-muted-foreground font-medium">Channels</span>
                <div className="mt-1 space-y-1 max-h-32 overflow-y-auto">
                  {channels.map((ch) => {
                    const checked = appriseTargets.some(
                      (t) => t.type === 'channel' && t.id === ch.key
                    );
                    return (
                      <label key={ch.key} className="flex items-center gap-2 text-sm">
                        <input
                          type="checkbox"
                          checked={checked}
                          onChange={() => toggleAppriseTarget('channel', ch.key)}
                          className="w-4 h-4 rounded border-input accent-primary"
                        />
                        <span>{ch.name || `#${ch.key.slice(0, 8)}`}</span>
                      </label>
                    );
                  })}
                </div>
              </div>
            )}
            {contacts.length > 0 && (
              <div>
                <span className="text-xs text-muted-foreground font-medium">Direct Messages</span>
                <div className="mt-1 space-y-1 max-h-32 overflow-y-auto">
                  {contacts.map((c) => {
                    const checked = appriseTargets.some(
                      (t) => t.type === 'contact' && t.id === c.public_key
                    );
                    return (
                      <label key={c.public_key} className="flex items-center gap-2 text-sm">
                        <input
                          type="checkbox"
                          checked={checked}
                          onChange={() => toggleAppriseTarget('contact', c.public_key)}
                          className="w-4 h-4 rounded border-input accent-primary"
                        />
                        <span>{c.name || c.public_key.slice(0, 12)}</span>
                      </label>
                    );
                  })}
                </div>
              </div>
            )}
          </div>
        )}
        <label className="flex items-center gap-3 cursor-pointer">
          <input
            type="checkbox"
            checked={apprisePreserveIdentity}
            onChange={(e) => setApprisePreserveIdentity(e.target.checked)}
            className="w-4 h-4 rounded border-input accent-primary"
          />
          <span className="text-sm">Preserve webhook avatar/name where supported</span>
        </label>
      </div>

      <Separator />
      <div className="space-y-3">
        <Label>Blocked Contacts</Label>
        <p className="text-xs text-muted-foreground">
          Blocking only hides messages from the UI. MQTT forwarding and bot responses are not
          affected. Messages are still stored and will reappear if unblocked.
        </p>

        {blockedKeys.length === 0 && blockedNames.length === 0 ? (
          <p className="text-sm text-muted-foreground italic">No blocked contacts</p>
        ) : (
          <div className="space-y-2">
            {blockedKeys.length > 0 && (
              <div>
                <span className="text-xs text-muted-foreground font-medium">Blocked Keys</span>
                <div className="mt-1 space-y-1">
                  {blockedKeys.map((key) => (
                    <div key={key} className="flex items-center justify-between gap-2">
                      <span className="text-xs font-mono truncate flex-1">{key}</span>
                      {onToggleBlockedKey && (
                        <Button
                          variant="ghost"
                          size="sm"
                          onClick={() => onToggleBlockedKey(key)}
                          className="h-7 text-xs flex-shrink-0"
                        >
                          Unblock
                        </Button>
                      )}
                    </div>
                  ))}
                </div>
              </div>
            )}
            {blockedNames.length > 0 && (
              <div>
                <span className="text-xs text-muted-foreground font-medium">Blocked Names</span>
                <div className="mt-1 space-y-1">
                  {blockedNames.map((name) => (
                    <div key={name} className="flex items-center justify-between gap-2">
                      <span className="text-sm truncate flex-1">{name}</span>
                      {onToggleBlockedName && (
                        <Button
                          variant="ghost"
                          size="sm"
                          onClick={() => onToggleBlockedName(name)}
                          className="h-7 text-xs flex-shrink-0"
                        >
                          Unblock
                        </Button>
                      )}
                    </div>
                  ))}
                </div>
              </div>
            )}
          </div>
        )}
      </div>

      {error && (
        <div className="text-sm text-destructive" role="alert">
          {error}
        </div>
      )}

      <Button onClick={handleSave} disabled={busy} className="w-full">
        {busy ? 'Saving...' : 'Save Settings'}
      </Button>
    </div>
  );
}
