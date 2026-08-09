import { useState, useEffect } from 'react';
import { ChevronRight, Logs, MessageSquare, Send, Settings, X } from 'lucide-react';
import { toast } from '../ui/sonner';
import { usePush } from '../../contexts/PushSubscriptionContext';
import type { AppSettings, AppSettingsUpdate, Channel, Contact } from '../../types';
import { getContactDisplayName } from '../../utils/pubkey';
import { Button } from '../ui/button';
import { Checkbox } from '../ui/checkbox';
import { Input } from '../ui/input';
import { Label } from '../ui/label';
import { Separator } from '../ui/separator';
import { cn } from '../../lib/utils';
import { ContactAvatar } from '../ContactAvatar';
import {
  captureLastViewedConversationFromHash,
  getReopenLastConversationEnabled,
  setReopenLastConversationEnabled,
} from '../../utils/lastViewedConversation';
import { ThemeSelector } from './ThemeSelector';
import { getLocalLabel, setLocalLabel, type LocalLabel } from '../../utils/localLabel';
import {
  DISTANCE_UNIT_LABELS,
  DISTANCE_UNITS,
  setSavedDistanceUnit,
} from '../../utils/distanceUnits';
import { useDistanceUnit } from '../../contexts/DistanceUnitContext';
import { useRichPayloads } from '../../contexts/RichPayloadContext';
import { setSavedRenderRichPayloads } from '../../utils/richPayloadPreference';
import { usePathHopWidth } from '../../contexts/PathHopWidthContext';
import { setSavedShowPathHopWidth } from '../../utils/pathHopWidthPreference';
import {
  DEFAULT_FONT_SCALE,
  FONT_SCALE_SLIDER_STEP,
  MAX_FONT_SCALE,
  MIN_FONT_SCALE,
  getSavedFontScale,
  setSavedFontScale,
} from '../../utils/fontScale';
import { getAutoFocusInputEnabled, setAutoFocusInputEnabled } from '../../utils/autoFocusInput';
import {
  getTextReplaceEnabled,
  setTextReplaceEnabled as saveTextReplaceEnabled,
  getTextReplaceMapJson,
  setTextReplaceMapJson,
  DEFAULT_MAP_JSON,
} from '../../utils/textReplace';
import {
  BATTERY_DISPLAY_CHANGE_EVENT,
  getShowBatteryPercent,
  setShowBatteryPercent as saveBatteryPercent,
  getShowBatteryVoltage,
  setShowBatteryVoltage as saveBatteryVoltage,
} from '../../utils/batteryDisplay';
import {
  STATUS_DOT_PULSE_CHANGE_EVENT,
  getStatusDotPulseEnabled,
  setStatusDotPulseEnabled as saveStatusDotPulse,
} from '../../utils/statusDotPulse';

/** Resolve a state key like "contact-abc123" or "channel-def456" to a display name. */
function resolveConversationName(
  stateKey: string,
  contacts: Contact[],
  channels: Channel[]
): string {
  if (stateKey.startsWith('contact-')) {
    const pubkey = stateKey.slice('contact-'.length);
    const contact = contacts.find((c) => c.public_key === pubkey);
    return contact ? getContactDisplayName(contact.name, contact.public_key) : pubkey.slice(0, 12);
  }
  if (stateKey.startsWith('channel-')) {
    const key = stateKey.slice('channel-'.length);
    const channel = channels.find((c) => c.key === key);
    if (channel?.name) return channel.name.startsWith('#') ? channel.name : `#${channel.name}`;
    return `#${key.slice(0, 12)}`;
  }
  return stateKey;
}

function PushDeviceManagement({
  contacts = [],
  channels = [],
}: {
  contacts?: Contact[];
  channels?: Channel[];
}) {
  const {
    isSupported,
    allSubscriptions,
    pushConversations,
    loading,
    subscribe,
    currentSubscriptionId,
    toggleConversation,
    deleteSubscription,
    testPush,
    refreshSubscriptions,
  } = usePush();

  useEffect(() => {
    refreshSubscriptions();
  }, [refreshSubscriptions]);

  if (!isSupported) {
    return (
      <div className="space-y-3">
        <h3 className="text-base font-semibold tracking-tight">Web Push Notifications</h3>
        <p className="text-[0.8125rem] text-muted-foreground">
          {window.isSecureContext
            ? 'Push notifications are not supported by this browser.'
            : 'Web Push requires HTTPS. Access RemoteTerm over HTTPS (self-signed certificates work) to enable push notifications.'}
        </p>
      </div>
    );
  }

  return (
    <div className="space-y-4">
      <div className="space-y-1">
        <h3 className="text-base font-semibold tracking-tight">Web Push Notifications</h3>
        <p className="text-[0.8125rem] text-muted-foreground">
          Receive notifications even when the browser is closed. Use the bell icon in any
          conversation header to enable push for that contact or channel, or subscribe this browser
          to receive notifications for all push-enabled conversations.
        </p>
        <p className="text-[0.8125rem] text-muted-foreground">
          The set of channels or DMs that trigger push notifications are global per-install (i.e.
          all devices that register for Web Push will have the same set of channels/DMs that trigger
          notifications). Subscribing or unsubscribing a particular browser only controls whether
          that browser receives notifications for the configured set of channels/DMs.
        </p>
      </div>

      {!currentSubscriptionId && (
        <Button variant="outline" size="sm" onClick={() => void subscribe()} disabled={loading}>
          {loading ? 'Subscribing...' : 'Subscribe This Browser'}
        </Button>
      )}

      {pushConversations.length > 0 && (
        <div className="space-y-2">
          <span className="text-[0.625rem] uppercase tracking-wider text-muted-foreground font-medium">
            Push-enabled conversations
          </span>
          <div className="flex flex-wrap gap-1.5">
            {pushConversations.map((key) => (
              <span
                key={key}
                className="inline-flex items-center gap-1 rounded-full bg-muted px-2.5 py-1 text-sm"
              >
                {resolveConversationName(key, contacts, channels)}
                <button
                  type="button"
                  onClick={() => void toggleConversation(key)}
                  className="rounded-full p-0.5 hover:bg-accent transition-colors"
                  title="Remove"
                  aria-label={`Remove ${resolveConversationName(key, contacts, channels)} from push`}
                >
                  <X className="h-3.5 w-3.5" />
                </button>
              </span>
            ))}
          </div>
        </div>
      )}

      {allSubscriptions.length > 0 && (
        <div className="space-y-2">
          <span className="text-[0.625rem] uppercase tracking-wider text-muted-foreground font-medium">
            Registered Devices
          </span>
          <div className="mt-2 space-y-2">
            {allSubscriptions.map((sub) => (
              <div
                key={sub.id}
                className="flex items-center justify-between gap-3 rounded-md border border-border px-3 py-2"
              >
                <div className="min-w-0 flex-1">
                  <div className="flex items-center gap-2 overflow-hidden">
                    <span className="truncate text-sm font-medium">
                      {sub.label || 'Unknown device'}
                    </span>
                    {sub.id === currentSubscriptionId && (
                      <span className="shrink-0 rounded bg-primary/10 px-1.5 py-0.5 text-[0.625rem] font-medium text-primary">
                        Current device
                      </span>
                    )}
                  </div>
                  <span className="text-xs text-muted-foreground">
                    {sub.last_success_at
                      ? `Last push: ${new Date(sub.last_success_at * 1000).toLocaleDateString()}`
                      : 'Never pushed'}
                    {sub.failure_count > 0 && ` · ${sub.failure_count} failures`}
                  </span>
                </div>
                <div className="flex gap-1">
                  <Button
                    variant="ghost"
                    size="sm"
                    className="h-8 text-sm"
                    onClick={() => void testPush(sub.id)}
                  >
                    Test
                  </Button>
                  <Button
                    variant="ghost"
                    size="sm"
                    className="h-8 text-sm text-destructive hover:text-destructive"
                    onClick={() => {
                      void deleteSubscription(sub.id).then(() => toast.success('Device removed'));
                    }}
                  >
                    Unsubscribe this device
                  </Button>
                </div>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

export function SettingsLocalSection({
  onLocalLabelChange,
  contacts,
  channels,
  appSettings,
  onSaveAppSettings,
  className,
}: {
  onLocalLabelChange?: (label: LocalLabel) => void;
  contacts?: Contact[];
  channels?: Channel[];
  appSettings?: AppSettings | null;
  onSaveAppSettings?: (update: AppSettingsUpdate) => Promise<void>;
  className?: string;
}) {
  const { distanceUnit, setDistanceUnit } = useDistanceUnit();
  const { renderRichPayloads, setRenderRichPayloads } = useRichPayloads();
  const { showPathHopWidth, setShowPathHopWidth } = usePathHopWidth();
  const [reopenLastConversation, setReopenLastConversation] = useState(
    getReopenLastConversationEnabled
  );
  const [localLabelText, setLocalLabelText] = useState(() => getLocalLabel().text);
  const [localLabelColor, setLocalLabelColor] = useState(() => getLocalLabel().color);
  const [autoFocusInput, setAutoFocusInput] = useState(getAutoFocusInputEnabled);
  const [batteryPercent, setBatteryPercent] = useState(getShowBatteryPercent);
  const [batteryVoltage, setBatteryVoltage] = useState(getShowBatteryVoltage);
  const [statusDotPulse, setStatusDotPulse] = useState(getStatusDotPulseEnabled);
  const [textReplaceEnabled, setTextReplaceEnabled] = useState(getTextReplaceEnabled);
  const [textReplaceJson, setTextReplaceJson] = useState(getTextReplaceMapJson);
  const [textReplaceError, setTextReplaceError] = useState<string | null>(null);
  const [fontScale, setFontScale] = useState(getSavedFontScale);
  const [fontScaleSlider, setFontScaleSlider] = useState(getSavedFontScale);
  const [fontScaleInput, setFontScaleInput] = useState(() => String(getSavedFontScale()));
  const [ollamaBaseUrl, setOllamaBaseUrl] = useState('http://localhost:11434');
  const [ollamaModel, setOllamaModel] = useState('phi3:mini');
  const [ollamaBusy, setOllamaBusy] = useState(false);
  const [ollamaError, setOllamaError] = useState<string | null>(null);

  useEffect(() => {
    if (!appSettings) return;
    setOllamaBaseUrl(appSettings.ollama_base_url || 'http://localhost:11434');
    setOllamaModel(appSettings.ollama_model ?? 'phi3:mini');
  }, [appSettings]);

  const commitFontScale = (nextScale: number) => {
    const normalized = setSavedFontScale(nextScale);
    setFontScale(normalized);
    setFontScaleSlider(normalized);
    setFontScaleInput(String(normalized));
  };

  const restoreFontScaleInput = () => {
    setFontScaleInput(String(fontScale));
  };

  const handleSliderChange = (nextScale: number) => {
    setFontScaleSlider(nextScale);
    setFontScaleInput(String(nextScale));
  };

  const handleSliderCommit = (nextScale: number) => {
    commitFontScale(nextScale);
  };

  const handleToggleReopenLastConversation = (enabled: boolean) => {
    setReopenLastConversation(enabled);
    setReopenLastConversationEnabled(enabled);
    if (enabled) {
      captureLastViewedConversationFromHash();
    }
  };

  const handleSaveOllamaSettings = async () => {
    if (!appSettings || !onSaveAppSettings) return;
    setOllamaError(null);
    setOllamaBusy(true);
    try {
      const nextUrl = ollamaBaseUrl.trim() || 'http://localhost:11434';
      const nextModel = ollamaModel.trim();
      const update: AppSettingsUpdate = {};
      if (nextUrl !== (appSettings.ollama_base_url || 'http://localhost:11434')) {
        update.ollama_base_url = nextUrl;
      }
      if (nextModel !== (appSettings.ollama_model ?? 'phi3:mini')) {
        update.ollama_model = nextModel;
      }
      if (Object.keys(update).length > 0) {
        await onSaveAppSettings(update);
      }
      toast.success('Ollama settings saved');
    } catch (err) {
      setOllamaError(err instanceof Error ? err.message : 'Failed to save');
    } finally {
      setOllamaBusy(false);
    }
  };

  return (
    <div className={className}>
      <p className="text-[0.8125rem] text-muted-foreground">
        These settings apply only to this device/browser.
      </p>

      <div className="space-y-1">
        <h3 className="text-base font-semibold tracking-tight">Color Scheme</h3>
        <ThemeSelector />
        <ThemePreview className="mt-6" />
      </div>

      <Separator />

      <div className="space-y-3">
        <h3 className="text-base font-semibold tracking-tight">Local Label</h3>
        <div className="flex items-center gap-2">
          <Input
            value={localLabelText}
            onChange={(e) => {
              const text = e.target.value;
              setLocalLabelText(text);
              setLocalLabel(text, localLabelColor);
              onLocalLabelChange?.({ text, color: localLabelColor });
            }}
            placeholder="e.g. Home Base, Field Radio 2"
            aria-label="Local label text"
            className="flex-1"
          />
          <input
            type="color"
            value={localLabelColor}
            onChange={(e) => {
              const color = e.target.value;
              setLocalLabelColor(color);
              setLocalLabel(localLabelText, color);
              onLocalLabelChange?.({ text: localLabelText, color });
            }}
            aria-label="Local label color"
            className="w-10 h-9 rounded border border-input cursor-pointer bg-transparent p-0.5"
          />
        </div>
        <p className="text-[0.8125rem] text-muted-foreground">
          Display a colored banner at the top of the page to identify this instance.
        </p>
      </div>

      <Separator />

      <div className="space-y-3">
        <Label htmlFor="distance-units">Distance Units</Label>
        <select
          id="distance-units"
          value={distanceUnit}
          onChange={(event) => {
            const nextUnit = event.target.value as (typeof DISTANCE_UNITS)[number];
            setSavedDistanceUnit(nextUnit);
            setDistanceUnit(nextUnit);
          }}
          className="w-full rounded-md border border-input bg-background px-3 py-2 text-sm"
        >
          {DISTANCE_UNITS.map((unit) => (
            <option key={unit} value={unit}>
              {DISTANCE_UNIT_LABELS[unit]}
            </option>
          ))}
        </select>
        <p className="text-[0.8125rem] text-muted-foreground">
          Controls how distances are shown throughout the app.
        </p>
      </div>

      <Separator />

      <div className="space-y-3">
        <h3 className="text-base font-semibold tracking-tight">UI Tweaks</h3>

        <div className="space-y-2">
          <div className="flex items-start gap-3 rounded-md border border-border/60 p-3">
            <Checkbox
              id="reopen-last"
              checked={reopenLastConversation}
              onCheckedChange={(checked) => handleToggleReopenLastConversation(checked === true)}
              className="mt-0.5"
            />
            <div className="space-y-1">
              <Label htmlFor="reopen-last">Reopen Last Conversation</Label>
              <p className="text-[0.8125rem] text-muted-foreground">
                Automatically reopen to the last-open channel or contact when the app loads to the
                bare URL.
              </p>
            </div>
          </div>

          <div className="flex items-start gap-3 rounded-md border border-border/60 p-3">
            <Checkbox
              id="auto-focus-input"
              checked={autoFocusInput}
              onCheckedChange={(checked) => {
                const v = checked === true;
                setAutoFocusInput(v);
                setAutoFocusInputEnabled(v);
              }}
              className="mt-0.5"
            />
            <div className="space-y-1">
              <Label htmlFor="auto-focus-input">Auto-Focus Message Input</Label>
              <p className="text-[0.8125rem] text-muted-foreground">
                Place the cursor in the message input when switching conversations. Desktop only.
              </p>
            </div>
          </div>

          <div className="flex items-start gap-3 rounded-md border border-border/60 p-3">
            <Checkbox
              id="battery-percent"
              checked={batteryPercent}
              onCheckedChange={(checked) => {
                const v = checked === true;
                setBatteryPercent(v);
                saveBatteryPercent(v);
                window.dispatchEvent(new Event(BATTERY_DISPLAY_CHANGE_EVENT));
              }}
              className="mt-0.5"
            />
            <div className="space-y-1">
              <Label htmlFor="battery-percent">Show Battery Percentage</Label>
              <p className="text-[0.8125rem] text-muted-foreground">
                Display the radio&apos;s battery percentage in the status bar. Data updates every 60
                seconds and may take up to a minute to appear after connecting.
              </p>
            </div>
          </div>

          <div className="flex items-start gap-3 rounded-md border border-border/60 p-3">
            <Checkbox
              id="battery-voltage"
              checked={batteryVoltage}
              onCheckedChange={(checked) => {
                const v = checked === true;
                setBatteryVoltage(v);
                saveBatteryVoltage(v);
                window.dispatchEvent(new Event(BATTERY_DISPLAY_CHANGE_EVENT));
              }}
              className="mt-0.5"
            />
            <div className="space-y-1">
              <Label htmlFor="battery-voltage">Show Battery Voltage</Label>
              <p className="text-[0.8125rem] text-muted-foreground">
                Display the radio&apos;s battery voltage in the status bar (in mV). Data updates
                every 60 seconds and may take up to a minute to appear after connecting.
              </p>
            </div>
          </div>

          <div className="flex items-start gap-3 rounded-md border border-border/60 p-3">
            <Checkbox
              id="status-dot-pulse"
              checked={statusDotPulse}
              onCheckedChange={(checked) => {
                const v = checked === true;
                setStatusDotPulse(v);
                saveStatusDotPulse(v);
                window.dispatchEvent(new Event(STATUS_DOT_PULSE_CHANGE_EVENT));
              }}
              className="mt-0.5"
            />
            <div className="space-y-1">
              <Label htmlFor="status-dot-pulse">Status Dot Glitters</Label>
              <p className="text-[0.8125rem] text-muted-foreground">
                Flash the connection status dot in color as packets arrive: blue for channel, purple
                for DM, cyan for advert, dark green for other.
              </p>
            </div>
          </div>

          <div className="flex items-start gap-3 rounded-md border border-border/60 p-3">
            <Checkbox
              id="render-rich-payloads"
              checked={renderRichPayloads}
              onCheckedChange={(checked) => {
                const v = checked === true;
                setRenderRichPayloads(v);
                setSavedRenderRichPayloads(v);
              }}
              className="mt-0.5"
            />
            <div className="space-y-1">
              <Label htmlFor="render-rich-payloads">
                Render MeshCore Open GIFs &amp; Reactions
              </Label>
              <p className="text-[0.8125rem] text-muted-foreground">
                MeshCore Open clients send GIFs and emoji reactions as encoded text (e.g.{' '}
                <code className="text-[0.75rem]">g:abc123</code> or{' '}
                <code className="text-[0.75rem]">r:1a2b:05</code>). When enabled, these render as
                the GIF image or reaction emoji instead of the raw text. Reactions show generically
                (the emoji is not tied to a specific message). GIFs load from media.giphy.com, which
                reaches outside your local network and exposes your IP to Giphy — so this is off by
                default.
              </p>
            </div>
          </div>

          <div className="flex items-start gap-3 rounded-md border border-border/60 p-3">
            <Checkbox
              id="show-path-hop-width"
              checked={showPathHopWidth}
              onCheckedChange={(checked) => {
                const v = checked === true;
                setShowPathHopWidth(v);
                setSavedShowPathHopWidth(v);
              }}
              className="mt-0.5"
            />
            <div className="space-y-1">
              <Label htmlFor="show-path-hop-width">Show Path Hop Width</Label>
              <p className="text-[0.8125rem] text-muted-foreground">
                Append the per-hop identifier width to the hop-count badge on received messages —
                e.g. <code className="text-[0.75rem]">(2 · 2B)</code> for a 2-hop path with 2-byte
                hops. Direct (0-hop) messages show no width. Off by default.
              </p>
            </div>
          </div>

          <div className="rounded-md border border-border/60 p-3 space-y-2">
            <div className="flex items-start gap-3">
              <Checkbox
                id="text-replace"
                checked={textReplaceEnabled}
                onCheckedChange={(checked) => {
                  const v = checked === true;
                  setTextReplaceEnabled(v);
                  saveTextReplaceEnabled(v);
                }}
                className="mt-0.5"
              />
              <div className="space-y-1">
                <Label htmlFor="text-replace">Replace as you Type</Label>
                <p className="text-[0.8125rem] text-muted-foreground">
                  Automatically replace characters as you type in the message input. Define
                  replacements as a JSON object mapping source strings to their replacements.
                </p>
              </div>
            </div>
            {textReplaceEnabled && (
              <div className="space-y-2 pl-7">
                <textarea
                  value={textReplaceJson}
                  onChange={(e) => {
                    const val = e.target.value;
                    setTextReplaceJson(val);
                    setTextReplaceError(setTextReplaceMapJson(val));
                  }}
                  spellCheck={false}
                  rows={10}
                  className={cn(
                    'w-full rounded-md border bg-background px-3 py-2 text-sm font-mono',
                    textReplaceError ? 'border-destructive' : 'border-input'
                  )}
                  aria-label="Text replacement map (JSON)"
                />
                {textReplaceError && (
                  <p className="text-xs text-destructive">
                    {textReplaceError} Changes are not saved until this is resolved.
                  </p>
                )}
                <button
                  type="button"
                  onClick={() => {
                    setTextReplaceJson(DEFAULT_MAP_JSON);
                    setTextReplaceMapJson(DEFAULT_MAP_JSON);
                    setTextReplaceError(null);
                  }}
                  className="inline-flex h-8 items-center justify-center rounded-md border border-input px-3 text-sm font-medium transition-colors hover:bg-accent focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                >
                  Reset to Default
                </button>
              </div>
            )}
          </div>
        </div>

        <div className="space-y-3">
          <Label htmlFor="font-scale-input">Relative Font Size</Label>
          <div className="flex flex-col gap-3 sm:flex-row sm:items-center">
            <input
              type="range"
              min={MIN_FONT_SCALE}
              max={MAX_FONT_SCALE}
              step={FONT_SCALE_SLIDER_STEP}
              value={fontScaleSlider}
              onChange={(event) => handleSliderChange(Number(event.target.value))}
              onMouseUp={(event) => handleSliderCommit(Number(event.currentTarget.value))}
              onTouchEnd={(event) => handleSliderCommit(Number(event.currentTarget.value))}
              onKeyUp={(event) => handleSliderCommit(Number(event.currentTarget.value))}
              onBlur={(event) => handleSliderCommit(Number(event.currentTarget.value))}
              aria-label="Relative font size slider"
              className="w-full accent-primary sm:flex-1"
            />
            <div className="flex items-center gap-2 sm:w-40">
              <Input
                id="font-scale-input"
                type="number"
                inputMode="decimal"
                min={MIN_FONT_SCALE}
                max={MAX_FONT_SCALE}
                step="any"
                value={fontScaleInput}
                onChange={(event) => {
                  const nextValue = event.target.value;
                  setFontScaleInput(nextValue);

                  if (nextValue === '') {
                    return;
                  }

                  if (event.target.validity.valid && Number.isFinite(event.target.valueAsNumber)) {
                    commitFontScale(event.target.valueAsNumber);
                  }
                }}
                onBlur={() => {
                  const parsed = Number.parseFloat(fontScaleInput);
                  if (!Number.isFinite(parsed)) {
                    restoreFontScaleInput();
                    return;
                  }
                  commitFontScale(parsed);
                }}
                onKeyDown={(event) => {
                  if (event.key !== 'Enter') {
                    return;
                  }
                  event.preventDefault();
                  const parsed = Number.parseFloat(fontScaleInput);
                  if (!Number.isFinite(parsed)) {
                    restoreFontScaleInput();
                    return;
                  }
                  commitFontScale(parsed);
                }}
                aria-label="Relative font size percentage"
              />
              <span className="text-sm text-muted-foreground">%</span>
            </div>
            <button
              type="button"
              onClick={() => commitFontScale(DEFAULT_FONT_SCALE)}
              className="inline-flex h-9 items-center justify-center rounded-md border border-input px-3 text-sm font-medium transition-colors hover:bg-accent focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:cursor-not-allowed disabled:opacity-50"
              disabled={fontScale === DEFAULT_FONT_SCALE}
            >
              Reset
            </button>
          </div>
          <p className="text-[0.8125rem] text-muted-foreground">
            Scales the app&apos;s typography for this browser only. The slider moves in 5% steps;
            the number field accepts any value from 25% to 400%.
          </p>
        </div>
      </div>

      <Separator />

      {appSettings && onSaveAppSettings ? (
        <div className="space-y-3">
          <div className="space-y-2">
            <h3 className="text-base font-semibold tracking-tight">Ollama Unread Summaries</h3>
            <p className="text-[0.8125rem] text-muted-foreground">
              When enabled, opening a channel with unread messages asks your local Ollama server for
              a short catch-up summary shown as a banner above the chat. Stored on the RemoteTerm
              server (not browser-local).
            </p>
          </div>

          <div className="flex items-start gap-3 rounded-md border border-border/60 p-3">
            <Checkbox
              id="ollama-enabled"
              checked={appSettings.ollama_enabled}
              onCheckedChange={(checked) => {
                void onSaveAppSettings({ ollama_enabled: checked === true });
              }}
              className="mt-0.5"
            />
            <div className="space-y-1">
              <Label htmlFor="ollama-enabled">Enable unread summaries</Label>
              <p className="text-[0.8125rem] text-muted-foreground">
                Turn off to hide the summary banner when opening channels. URL and model settings
                are kept.
              </p>
            </div>
          </div>

          <div className="space-y-2">
            <Label htmlFor="ollama-base-url">Ollama path / URL</Label>
            <Input
              id="ollama-base-url"
              value={ollamaBaseUrl}
              onChange={(e) => setOllamaBaseUrl(e.target.value)}
              placeholder="http://localhost:11434"
              spellCheck={false}
              disabled={!appSettings.ollama_enabled}
            />
            <p className="text-[0.8125rem] text-muted-foreground">
              Base URL of the Ollama HTTP API reachable from the RemoteTerm server (not your
              browser).
            </p>
          </div>

          <div className="space-y-2">
            <Label htmlFor="ollama-model">Ollama model</Label>
            <Input
              id="ollama-model"
              value={ollamaModel}
              onChange={(e) => setOllamaModel(e.target.value)}
              placeholder="phi3:mini"
              spellCheck={false}
              disabled={!appSettings.ollama_enabled}
            />
            <p className="text-[0.8125rem] text-muted-foreground">
              Model name as shown by <code className="text-xs">ollama list</code>. Defaults to{' '}
              <code className="text-xs">phi3:mini</code>.
            </p>
          </div>

          {ollamaError && <p className="text-sm text-destructive">{ollamaError}</p>}
          <Button
            type="button"
            onClick={handleSaveOllamaSettings}
            disabled={ollamaBusy || !appSettings.ollama_enabled}
          >
            {ollamaBusy ? 'Saving…' : 'Save Ollama Settings'}
          </Button>
        </div>
      ) : null}

      <Separator />

      <PushDeviceManagement contacts={contacts} channels={channels} />
    </div>
  );
}

function ThemePreview({ className }: { className?: string }) {
  const [showStyleRef, setShowStyleRef] = useState(false);

  return (
    <div className={`rounded-lg border border-border bg-card p-3 ${className ?? ''}`}>
      <p className="text-xs text-muted-foreground mb-3">
        Preview alert, message, sidebar, and badge contrast for the selected theme.
      </p>

      <div className="space-y-2">
        <PreviewBanner className="border border-status-connected/30 bg-status-connected/15 text-status-connected">
          Connected preview: radio link healthy and syncing.
        </PreviewBanner>
        <PreviewBanner className="border border-warning/50 bg-warning/10 text-warning">
          Warning preview: packet audit suggests missing history.
        </PreviewBanner>
        <PreviewBanner className="border border-destructive/30 bg-destructive/10 text-destructive">
          Error preview: radio reconnect failed.
        </PreviewBanner>
      </div>

      <div className="mt-4 space-y-2">
        <PreviewMessage
          sender="Alice"
          bubbleClassName="bg-msg-incoming text-foreground"
          text="Hello, mesh!"
        />
        <PreviewMessage
          sender="You"
          alignRight
          bubbleClassName="bg-msg-outgoing text-foreground"
          text="Hi there! I'm using RemoteTerm."
        />
      </div>

      <div className="mt-4 rounded-md border border-border bg-background p-2">
        <p className="mb-2 text-[0.6875rem] font-medium text-muted-foreground">Sidebar preview</p>
        <div className="space-y-1">
          <PreviewSidebarRow
            active
            leading={
              <span
                className="flex h-6 w-6 items-center justify-center rounded-md bg-primary/10 text-primary"
                aria-hidden="true"
              >
                <Logs className="h-3.5 w-3.5" />
              </span>
            }
            label="Packet Feed"
          />
          <PreviewSidebarRow
            leading={<ContactAvatar name="Alice" publicKey={'ab'.repeat(32)} size={24} />}
            label="Alice"
            badge={
              <span className="rounded-full bg-badge-unread/90 px-1.5 py-0.5 text-[0.625rem] font-semibold text-badge-unread-foreground">
                3
              </span>
            }
          />
          <PreviewSidebarRow
            leading={<ContactAvatar name="Mesh Ops" publicKey={'cd'.repeat(32)} size={24} />}
            label="Mesh Ops"
            badge={
              <span className="rounded-full bg-badge-mention px-1.5 py-0.5 text-[0.625rem] font-semibold text-badge-mention-foreground">
                @2
              </span>
            }
          />
        </div>
      </div>

      {/* ── Style Reference (collapsible) ── */}
      <button
        type="button"
        onClick={() => setShowStyleRef((v) => !v)}
        className="mt-4 flex w-full items-center gap-1.5 text-[0.6875rem] font-medium text-muted-foreground hover:text-foreground transition-colors"
      >
        <ChevronRight
          className={cn('h-3.5 w-3.5 transition-transform', showStyleRef && 'rotate-90')}
        />
        Canonical style reference
      </button>

      {showStyleRef && (
        <>
          {/* ── Text Hierarchy ── */}
          <PreviewSection title="Text hierarchy">
            <div className="space-y-2">
              <PreviewTextRow
                classes="text-xl font-semibold"
                label="text-xl font-semibold"
                desc="Hero / large data"
              />
              <PreviewTextRow
                classes="text-lg font-semibold"
                label="text-lg font-semibold"
                desc="Sheet / dialog title"
              />
              <PreviewTextRow
                classes="text-base font-semibold tracking-tight"
                label="text-base font-semibold tracking-tight"
                desc="Section / group title"
              />
              <PreviewTextRow classes="text-sm" label="text-sm" desc="Body text, form labels" />
              <PreviewTextRow
                classes="text-[0.8125rem] text-muted-foreground"
                label="text-[0.8125rem] text-muted-foreground"
                desc="Helper / description text"
              />
              <PreviewTextRow
                classes="text-[0.6875rem] text-muted-foreground"
                label="text-[0.6875rem] text-muted-foreground"
                desc="Metadata, timestamps"
              />
              <div>
                <p className="text-[0.625rem] uppercase tracking-wider text-muted-foreground font-medium">
                  Metadata Label
                </p>
                <p className="text-[0.625rem] text-muted-foreground/60 mt-0.5">
                  text-[0.625rem] uppercase tracking-wider text-muted-foreground font-medium
                </p>
              </div>
            </div>
          </PreviewSection>

          {/* ── Mono Text ── */}
          <PreviewSection title="Mono text">
            <div className="space-y-1.5">
              <div>
                <p className="text-xs font-mono text-muted-foreground">
                  a1b2c3d4e5f6...7890abcdef01
                </p>
                <p className="text-[0.625rem] text-muted-foreground/60">
                  text-xs font-mono — keys, identifiers
                </p>
              </div>
              <div>
                <p className="text-[0.6875rem] font-mono">1h 23m 45s uptime</p>
                <p className="text-[0.625rem] text-muted-foreground/60">
                  text-[0.6875rem] font-mono — metadata mono
                </p>
              </div>
              <div>
                <p className="text-sm font-mono">$ req_status_sync 0xA1B2...</p>
                <p className="text-[0.625rem] text-muted-foreground/60">
                  text-sm font-mono — console / code
                </p>
              </div>
            </div>
          </PreviewSection>

          {/* ── Badges ── */}
          <PreviewSection title="Badges and tags">
            <div className="flex flex-wrap items-center gap-2">
              <span className="text-[0.625rem] uppercase tracking-wider px-1.5 py-0.5 rounded bg-muted text-muted-foreground font-medium">
                Hashtag
              </span>
              <span className="text-[0.625rem] uppercase tracking-wider px-1.5 py-0.5 rounded bg-muted text-muted-foreground font-medium">
                Repeater
              </span>
              <span className="text-[0.625rem] uppercase tracking-wider px-1.5 py-0.5 rounded bg-primary/10 text-primary font-medium">
                On Radio
              </span>
              <span className="rounded-full bg-badge-unread/90 px-1.5 py-0.5 text-[0.625rem] font-semibold text-badge-unread-foreground">
                3
              </span>
              <span className="rounded-full bg-badge-mention px-1.5 py-0.5 text-[0.625rem] font-semibold text-badge-mention-foreground">
                @2
              </span>
            </div>
            <p className="text-[0.625rem] text-muted-foreground/60 mt-1.5">
              Muted: bg-muted &middot; Primary: bg-primary/10 &middot; Unread/Mention: bg-badge-*
            </p>
          </PreviewSection>

          {/* ── Buttons ── */}
          <PreviewSection title="Buttons">
            <div className="space-y-3">
              <div>
                <p className="text-[0.625rem] text-muted-foreground/60 mb-1.5">
                  Standard variants (size sm)
                </p>
                <div className="flex flex-wrap gap-1.5">
                  <Button size="sm">Default</Button>
                  <Button size="sm" variant="outline">
                    Outline
                  </Button>
                  <Button size="sm" variant="secondary">
                    Secondary
                  </Button>
                  <Button size="sm" variant="destructive">
                    Destructive
                  </Button>
                  <Button size="sm" variant="ghost">
                    Ghost
                  </Button>
                  <Button size="icon" variant="outline">
                    <Settings className="h-4 w-4" />
                  </Button>
                  <Button size="icon" variant="outline">
                    <Send className="h-4 w-4" />
                  </Button>
                </div>
              </div>
              <div>
                <p className="text-[0.625rem] text-muted-foreground/60 mb-1.5">
                  Semantic outline variants
                </p>
                <div className="flex flex-wrap gap-1.5">
                  <Button
                    size="sm"
                    variant="outline"
                    className="border-destructive/50 text-destructive hover:bg-destructive/10"
                  >
                    Danger
                  </Button>
                  <Button
                    size="sm"
                    variant="outline"
                    className="border-warning/50 text-warning hover:bg-warning/10"
                  >
                    Warning
                  </Button>
                  <Button
                    size="sm"
                    variant="outline"
                    className="border-green-600/50 text-green-600 hover:bg-green-600/10"
                  >
                    Success
                  </Button>
                </div>
              </div>
              <div>
                <p className="text-[0.625rem] text-muted-foreground/60 mb-1.5">
                  Metric selector pills
                </p>
                <div className="flex gap-1">
                  {['Voltage', 'Noise Floor', 'Packets'].map((label, i) => (
                    <button
                      key={label}
                      type="button"
                      className={cn(
                        'text-[0.6875rem] px-2 py-0.5 rounded transition-colors',
                        i === 0
                          ? 'bg-primary text-primary-foreground'
                          : 'text-muted-foreground hover:text-foreground hover:bg-accent'
                      )}
                    >
                      {label}
                    </button>
                  ))}
                </div>
              </div>
            </div>
          </PreviewSection>

          {/* ── Clickable Text ── */}
          <PreviewSection title="Clickable text">
            <div className="space-y-1.5">
              <span
                role="button"
                tabIndex={0}
                className="text-xs font-mono text-muted-foreground cursor-pointer hover:text-primary transition-colors block"
              >
                a1b2c3d4e5f6 (click to copy)
              </span>
              <span
                role="button"
                tabIndex={0}
                className="text-sm cursor-pointer underline underline-offset-2 decoration-muted-foreground/50 hover:text-primary transition-colors"
              >
                Underlined navigational link
              </span>
            </div>
            <p className="text-[0.625rem] text-muted-foreground/60 mt-1.5">
              cursor-pointer hover:text-primary transition-colors — use role=&quot;button&quot; +
              tabIndex
            </p>
          </PreviewSection>

          {/* ── Inline Alerts ── */}
          <PreviewSection title="Inline alerts">
            <div className="space-y-1.5">
              <div className="rounded-md border border-info/30 bg-info/10 px-3 py-2 text-xs text-info">
                Info: channel slot cache refreshed from radio.
              </div>
              <div className="rounded-md border border-warning/30 bg-warning/10 px-3 py-2 text-xs text-warning">
                Warning: radio clock skew detected.
              </div>
              <div className="rounded-md border border-destructive/30 bg-destructive/10 px-3 py-2 text-xs text-destructive">
                Error: post-connect setup timed out. Reboot the radio and restart.
              </div>
            </div>
          </PreviewSection>
        </>
      )}
    </div>
  );
}

function PreviewSection({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="mt-4 rounded-md border border-border bg-background p-2">
      <p className="mb-2 text-[0.6875rem] font-medium text-muted-foreground">{title}</p>
      {children}
    </div>
  );
}

function PreviewTextRow({
  classes,
  label,
  desc,
}: {
  classes: string;
  label: string;
  desc: string;
}) {
  return (
    <div>
      <p className={classes}>Sample text at this size</p>
      <p className="text-[0.625rem] text-muted-foreground/60">
        {label} — {desc}
      </p>
    </div>
  );
}

function PreviewBanner({ children, className }: { children: React.ReactNode; className: string }) {
  return <div className={`rounded-md px-3 py-2 text-xs ${className}`}>{children}</div>;
}

function PreviewMessage({
  sender,
  text,
  bubbleClassName,
  alignRight = false,
}: {
  sender: string;
  text: string;
  bubbleClassName: string;
  alignRight?: boolean;
}) {
  return (
    <div className={`flex ${alignRight ? 'justify-end' : 'justify-start'}`}>
      <div className={`max-w-[85%] ${alignRight ? 'items-end' : 'items-start'} flex flex-col`}>
        <span className="mb-1 text-[0.6875rem] text-muted-foreground">{sender}</span>
        <div className={`rounded-2xl px-3 py-2 text-sm break-words ${bubbleClassName}`}>{text}</div>
      </div>
    </div>
  );
}

function PreviewSidebarRow({
  leading,
  label,
  badge,
  active = false,
}: {
  leading: React.ReactNode;
  label: string;
  badge?: React.ReactNode;
  active?: boolean;
}) {
  return (
    <div
      data-active={active ? 'true' : undefined}
      className={`sidebar-action-row flex items-center gap-2 rounded-md border-l-2 px-3 py-2 text-[0.8125rem] ${
        active ? 'border-l-primary bg-accent text-foreground' : 'border-l-transparent'
      }`}
    >
      <span className="sidebar-tool-icon" aria-hidden="true">
        {leading}
      </span>
      <span className={`sidebar-tool-label min-w-0 flex-1 truncate ${active ? 'font-medium' : ''}`}>
        {label}
      </span>
      {badge}
      {!badge && (
        <span className="sidebar-tool-icon" aria-hidden="true">
          <MessageSquare className="h-3.5 w-3.5" />
        </span>
      )}
    </div>
  );
}
