import { useEffect, useMemo, useState } from 'react';
import { Plus, X } from 'lucide-react';

import { api } from '../../api';
import type { BotAdminUser, BotEngineStatus, Contact } from '../../types';
import { Button } from '../ui/button';
import { Input } from '../ui/input';
import { toast } from '../ui/sonner';
import { cn } from '@/lib/utils';

const MENTION_MODES = [
  { value: 'also', label: 'Also' },
  { value: 'only', label: 'Only' },
  { value: 'off', label: 'Off' },
] as const;

const PROFANITY_MODES = [
  { value: 'off', label: 'Off' },
  { value: 'censor', label: 'Censor' },
  { value: 'drop', label: 'Drop' },
] as const;

const LANGUAGES: { value: string; label: string }[] = [
  { value: 'en', label: 'English' },
  { value: 'en-GB', label: 'English (UK)' },
  { value: 'de', label: 'Deutsch' },
  { value: 'es', label: 'Español' },
  { value: 'fr', label: 'Français' },
  { value: 'fr-CA', label: 'Français (CA)' },
  { value: 'nl', label: 'Nederlands' },
  { value: 'pl', label: 'Polski' },
  { value: 'pt', label: 'Português' },
  { value: 'pt-BR', label: 'Português (BR)' },
];

const HEX_KEY_RE = /^[0-9a-f]{64}$/;

function SectionTitle({ title, hint }: { title: string; hint?: string }) {
  return (
    <div className="mb-2.5">
      <h3 className="text-base font-semibold tracking-tight">{title}</h3>
      {hint && <p className="text-[0.8125rem] text-muted-foreground mt-0.5">{hint}</p>}
    </div>
  );
}

/** Engine-wide bot settings: identity, rate limits, language, moderation, admins. */
export function EngineTab({ contacts, onChanged }: { contacts: Contact[]; onChanged: () => void }) {
  const [engine, setEngine] = useState<BotEngineStatus | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [dirty, setDirty] = useState(false);

  // Draft settings (numeric fields held as strings while editing)
  const [commandPrefix, setCommandPrefix] = useState('!');
  const [requirePrefix, setRequirePrefix] = useState(false);
  const [mentionMode, setMentionMode] = useState<'also' | 'only' | 'off'>('also');
  const [globalReply, setGlobalReply] = useState('0');
  const [perUser, setPerUser] = useState('0');
  const [txSpacing, setTxSpacing] = useState('0');
  const [maxHops, setMaxHops] = useState('0');
  const [defaultLanguage, setDefaultLanguage] = useState('en');
  const [autoDetectLanguage, setAutoDetectLanguage] = useState(false);
  const [bannedUsers, setBannedUsers] = useState<string[]>([]);
  const [profanityMode, setProfanityMode] = useState<'off' | 'censor' | 'drop'>('off');
  const [adminUsers, setAdminUsers] = useState<BotAdminUser[]>([]);

  // Add-row inputs
  const [newBan, setNewBan] = useState('');
  const [adminContactKey, setAdminContactKey] = useState('');
  const [adminPastedKey, setAdminPastedKey] = useState('');
  const [adminName, setAdminName] = useState('');

  const applyEngine = (status: BotEngineStatus) => {
    setEngine(status);
    const s = status.settings;
    setCommandPrefix(s.command_prefix);
    setRequirePrefix(s.require_prefix);
    setMentionMode(s.mention_mode);
    setGlobalReply(String(s.global_reply_seconds));
    setPerUser(String(s.per_user_seconds));
    setTxSpacing(String(s.tx_spacing_seconds));
    setMaxHops(String(s.max_response_hops));
    setDefaultLanguage(s.default_language);
    setAutoDetectLanguage(s.auto_detect_language);
    setBannedUsers([...s.banned_users]);
    setProfanityMode(s.profanity_mode);
    setAdminUsers([...s.admin_users]);
    setDirty(false);
  };

  useEffect(() => {
    api
      .getBotEngine()
      .then(applyEngine)
      .catch((err: unknown) =>
        setLoadError(err instanceof Error ? err.message : 'Failed to load engine settings')
      );
  }, []);

  const markDirty = () => setDirty(true);

  const keyedContacts = useMemo(
    () => contacts.filter((c) => c.public_key.length === 64),
    [contacts]
  );

  const handleSave = async () => {
    setSaving(true);
    try {
      const updated = await api.updateBotEngine({
        command_prefix: commandPrefix,
        require_prefix: requirePrefix,
        mention_mode: mentionMode,
        global_reply_seconds: parseFloat(globalReply) || 0,
        per_user_seconds: parseFloat(perUser) || 0,
        tx_spacing_seconds: parseFloat(txSpacing) || 0,
        max_response_hops: parseInt(maxHops, 10) || 0,
        default_language: defaultLanguage,
        auto_detect_language: autoDetectLanguage,
        banned_users: bannedUsers,
        profanity_mode: profanityMode,
        admin_users: adminUsers,
      });
      applyEngine(updated);
      toast.success('Engine settings saved');
      onChanged();
    } catch (err) {
      toast.error('Save failed', {
        description: err instanceof Error ? err.message : undefined,
      });
    } finally {
      setSaving(false);
    }
  };

  const handleAddBan = () => {
    const name = newBan.trim();
    if (!name || bannedUsers.includes(name)) return;
    setBannedUsers((prev) => [...prev, name]);
    setNewBan('');
    markDirty();
  };

  const handleAddAdmin = () => {
    const key = (adminPastedKey.trim() || adminContactKey).toLowerCase();
    if (!HEX_KEY_RE.test(key)) {
      toast.error('Enter a 64-character hex public key or pick a contact');
      return;
    }
    if (adminUsers.some((u) => u.public_key === key)) {
      toast.error('That key is already an admin');
      return;
    }
    const contact = keyedContacts.find((c) => c.public_key === key);
    const name = adminName.trim() || contact?.name || `${key.slice(0, 8)}…`;
    setAdminUsers((prev) => [...prev, { public_key: key, name }]);
    setAdminContactKey('');
    setAdminPastedKey('');
    setAdminName('');
    markDirty();
  };

  if (loadError) {
    return (
      <div className="flex-1 flex items-center justify-center text-muted-foreground text-sm">
        {loadError}
      </div>
    );
  }

  if (!engine) {
    return (
      <div className="flex-1 flex items-center justify-center text-muted-foreground">
        Loading engine settings…
      </div>
    );
  }

  return (
    <div className="flex-1 min-h-0 overflow-y-auto p-4 flex flex-col gap-4">
      <div className="flex items-center gap-3 max-w-[62rem]">
        {(engine.disabled_by_env || engine.disabled_until_restart) && (
          <span className="text-xs text-warning">
            {engine.disabled_by_env
              ? 'Bot engine is disabled by server configuration (MESHCORE_DISABLE_BOTS) — settings can be edited but nothing runs.'
              : 'Bot engine is disabled until the server restarts — settings can be edited but nothing runs.'}
          </span>
        )}
        <div className="flex-1" />
        {dirty && <span className="text-[0.6875rem] text-warning">unsaved changes</span>}
        <Button
          size="sm"
          className="h-7"
          onClick={() => void handleSave()}
          disabled={!dirty || saving}
        >
          {saving ? 'Saving…' : 'Save'}
        </Button>
      </div>

      <div className="flex flex-col md:flex-row gap-8">
        {/* ── Left column ── */}
        <div className="flex-1 max-w-md flex flex-col gap-5">
          <div>
            <SectionTitle
              title="Identity & triggers"
              hint="How the engine decides a message is meant for a bot. Applies to all keyword bots."
            />
            <div className="flex items-end gap-4">
              <div>
                <div className="text-xs text-muted-foreground mb-1">Command prefix</div>
                <Input
                  value={commandPrefix}
                  onChange={(e) => {
                    setCommandPrefix(e.target.value);
                    markDirty();
                  }}
                  className="h-8 w-28 font-mono text-[0.8125rem]"
                />
              </div>
              <label className="flex items-center gap-2.5 cursor-pointer h-8">
                <input
                  type="checkbox"
                  checked={requirePrefix}
                  onChange={(e) => {
                    setRequirePrefix(e.target.checked);
                    markDirty();
                  }}
                  className="w-4 h-4 rounded border-input accent-primary"
                />
                <span className="text-[0.8125rem]">Require prefix</span>
              </label>
            </div>
            <p className="text-[0.6875rem] text-muted-foreground mt-2">
              Off: bare keywords also trigger. Comma-separate for multiple prefixes.
            </p>
            <div className="mt-3">
              <div className="text-xs text-muted-foreground mb-1">
                Respond to @[BotName] mentions
              </div>
              <div className="inline-flex gap-0.5 bg-muted rounded-lg p-[3px]">
                {MENTION_MODES.map((mode) => (
                  <button
                    key={mode.value}
                    type="button"
                    onClick={() => {
                      setMentionMode(mode.value);
                      markDirty();
                    }}
                    className={cn(
                      'px-3 py-1 rounded-md text-xs transition-colors',
                      mentionMode === mode.value
                        ? 'bg-background text-foreground shadow-sm'
                        : 'text-muted-foreground hover:text-foreground'
                    )}
                  >
                    {mode.label}
                  </button>
                ))}
              </div>
            </div>
          </div>

          <div className="border-t border-border pt-4">
            <SectionTitle
              title="Rate limits"
              hint="Engine-wide caps; per-bot cooldowns stack on top."
            />
            <div className="flex gap-3">
              <div className="flex-1">
                <div className="text-xs text-muted-foreground mb-1">Global reply (s)</div>
                <Input
                  type="number"
                  min={0}
                  value={globalReply}
                  onChange={(e) => {
                    setGlobalReply(e.target.value);
                    markDirty();
                  }}
                  className="h-8 font-mono text-[0.8125rem]"
                />
              </div>
              <div className="flex-1">
                <div className="text-xs text-muted-foreground mb-1">Per-user (s)</div>
                <Input
                  type="number"
                  min={0}
                  value={perUser}
                  onChange={(e) => {
                    setPerUser(e.target.value);
                    markDirty();
                  }}
                  className="h-8 font-mono text-[0.8125rem]"
                />
              </div>
              <div className="flex-1">
                <div className="text-xs text-muted-foreground mb-1">TX spacing (s)</div>
                <Input
                  type="number"
                  min={0}
                  value={txSpacing}
                  onChange={(e) => {
                    setTxSpacing(e.target.value);
                    markDirty();
                  }}
                  className="h-8 font-mono text-[0.8125rem]"
                />
              </div>
              <div className="flex-1">
                <div className="text-xs text-muted-foreground mb-1">Max resp. hops</div>
                <Input
                  type="number"
                  min={0}
                  value={maxHops}
                  onChange={(e) => {
                    setMaxHops(e.target.value);
                    markDirty();
                  }}
                  className="h-8 font-mono text-[0.8125rem]"
                />
              </div>
            </div>
            <p className="text-[0.6875rem] text-muted-foreground mt-2">
              Scheduled sends skip global and per-user limits but always respect TX spacing.
            </p>
          </div>

          <div className="border-t border-border pt-4">
            <SectionTitle title="Language" />
            <div className="flex items-end gap-4">
              <div className="w-40">
                <div className="text-xs text-muted-foreground mb-1">Default language</div>
                <select
                  value={defaultLanguage}
                  onChange={(e) => {
                    setDefaultLanguage(e.target.value);
                    markDirty();
                  }}
                  className="h-8 w-full rounded-md border border-input bg-transparent px-2.5 text-[0.8125rem]"
                  aria-label="Default language"
                >
                  {LANGUAGES.map((lang) => (
                    <option key={lang.value} value={lang.value}>
                      {lang.label}
                    </option>
                  ))}
                </select>
              </div>
              <label className="flex items-center gap-2.5 cursor-pointer h-8">
                <input
                  type="checkbox"
                  checked={autoDetectLanguage}
                  onChange={(e) => {
                    setAutoDetectLanguage(e.target.checked);
                    markDirty();
                  }}
                  className="w-4 h-4 rounded border-input accent-primary"
                />
                <span className="text-[0.8125rem]">Detect sender language and reply in it</span>
              </label>
            </div>
            <p className="text-[0.6875rem] text-muted-foreground mt-2">
              Translations ported from meshcore-bot — seeded bots ship with all 10 locales.
            </p>
          </div>
        </div>

        {/* ── Right column ── */}
        <div className="flex-1 max-w-md flex flex-col gap-5">
          <div>
            <SectionTitle
              title="Moderation"
              hint="Applies before any bot runs, and to bridge forwards."
            />
            <div className="text-xs text-muted-foreground mb-1">Banned users</div>
            <div className="flex flex-wrap gap-1.5 mb-2">
              {bannedUsers.map((name) => (
                <span
                  key={name}
                  className="inline-flex items-center gap-1.5 text-xs bg-destructive/10 text-destructive rounded-md px-2 py-1"
                >
                  {name}
                  <button
                    type="button"
                    onClick={() => {
                      setBannedUsers((prev) => prev.filter((n) => n !== name));
                      markDirty();
                    }}
                    aria-label={`Unban ${name}`}
                  >
                    <X className="h-3 w-3 opacity-70" />
                  </button>
                </span>
              ))}
            </div>
            <div className="flex gap-2">
              <Input
                value={newBan}
                onChange={(e) => setNewBan(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === 'Enter') handleAddBan();
                }}
                placeholder="ban name prefix…"
                className="h-8 w-44 text-xs"
              />
              <Button variant="outline" size="sm" className="h-8" onClick={handleAddBan}>
                <Plus className="h-3.5 w-3.5 mr-1" aria-hidden="true" /> Add
              </Button>
            </div>
            <p className="text-[0.6875rem] text-muted-foreground mt-2">
              Prefix match — bans every name starting with the entry.
            </p>
            <div className="mt-3">
              <div className="text-xs text-muted-foreground mb-1">Profanity filter</div>
              <div className="inline-flex gap-0.5 bg-muted rounded-lg p-[3px]">
                {PROFANITY_MODES.map((mode) => (
                  <button
                    key={mode.value}
                    type="button"
                    onClick={() => {
                      setProfanityMode(mode.value);
                      markDirty();
                    }}
                    className={cn(
                      'px-3 py-1 rounded-md text-xs transition-colors',
                      profanityMode === mode.value
                        ? 'bg-background text-foreground shadow-sm'
                        : 'text-muted-foreground hover:text-foreground'
                    )}
                  >
                    {mode.label}
                  </button>
                ))}
              </div>
              <p className="text-[0.6875rem] text-muted-foreground mt-1.5">
                Censor masks matches, Drop discards. Applies to bot replies and bridge forwards.
              </p>
            </div>
          </div>

          <div className="border-t border-border pt-4">
            <SectionTitle title="Admin users" />
            <div className="border border-border rounded-lg overflow-hidden mb-2">
              {adminUsers.length === 0 && (
                <div className="px-3 py-3 text-xs text-muted-foreground">No admin users yet.</div>
              )}
              {adminUsers.map((user, index) => (
                <div
                  key={user.public_key}
                  className={cn(
                    'flex items-center gap-2.5 px-3 py-2',
                    index > 0 && 'border-t border-border/50'
                  )}
                >
                  <span className="text-[0.8125rem] font-medium">{user.name}</span>
                  <span className="flex-1 min-w-0 font-mono text-[0.6875rem] text-muted-foreground truncate">
                    {user.public_key}
                  </span>
                  <button
                    type="button"
                    onClick={() => {
                      setAdminUsers((prev) => prev.filter((u) => u.public_key !== user.public_key));
                      markDirty();
                    }}
                    aria-label={`Remove admin ${user.name}`}
                  >
                    <X className="h-3.5 w-3.5 opacity-70" />
                  </button>
                </div>
              ))}
            </div>
            <div className="flex flex-col gap-2">
              <select
                value={adminContactKey}
                onChange={(e) => setAdminContactKey(e.target.value)}
                className="h-8 w-full rounded-md border border-input bg-transparent px-2.5 text-[0.8125rem]"
                aria-label="Pick a contact"
              >
                <option value="">Pick a contact…</option>
                {keyedContacts.map((contact) => (
                  <option key={contact.public_key} value={contact.public_key}>
                    {contact.name || `${contact.public_key.slice(0, 8)}…`}
                  </option>
                ))}
              </select>
              <div className="flex gap-2">
                <Input
                  value={adminPastedKey}
                  onChange={(e) => setAdminPastedKey(e.target.value)}
                  placeholder="…or paste a 64-hex public key"
                  className="h-8 flex-1 font-mono text-xs"
                />
                <Input
                  value={adminName}
                  onChange={(e) => setAdminName(e.target.value)}
                  placeholder="name"
                  className="h-8 w-28 text-xs"
                />
                <Button variant="outline" size="sm" className="h-8" onClick={handleAddAdmin}>
                  <Plus className="h-3.5 w-3.5 mr-1" aria-hidden="true" /> Add
                </Button>
              </div>
            </div>
            <p className="text-[0.6875rem] text-muted-foreground mt-2">
              Bots flagged 'Admins only' answer only these senders, checked against the verified
              sender key. Admin status never bypasses rate limits.
            </p>
          </div>
        </div>
      </div>
    </div>
  );
}
