import { useState, useEffect, useMemo, lazy, Suspense } from 'react';

const BotCodeEditor = lazy(() =>
  import('./BotCodeEditor').then((m) => ({ default: m.BotCodeEditor }))
);
import type {
  AppSettings,
  AppSettingsUpdate,
  BotConfig,
  HealthStatus,
  RadioConfig,
  RadioConfigUpdate,
} from '../types';
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogDescription } from './ui/dialog';
import { Tabs, TabsContent, TabsList, TabsTrigger } from './ui/tabs';
import { Input } from './ui/input';
import { Label } from './ui/label';
import { Button } from './ui/button';
import { Separator } from './ui/separator';
import { toast } from './ui/sonner';
import { api } from '../api';
import { formatTime } from '../utils/messageParser';

// Radio presets for common configurations
interface RadioPreset {
  name: string;
  freq: number;
  bw: number;
  sf: number;
  cr: number;
}

const RADIO_PRESETS: RadioPreset[] = [
  { name: 'USA/Canada', freq: 910.525, bw: 62.5, sf: 7, cr: 5 },
  { name: 'Australia', freq: 915.8, bw: 250, sf: 10, cr: 5 },
  { name: 'Australia (narrow)', freq: 916.575, bw: 62.5, sf: 7, cr: 8 },
  { name: 'Australia SA, WA', freq: 923.125, bw: 62.5, sf: 8, cr: 8 },
  { name: 'Australia QLD', freq: 923.125, bw: 62.5, sf: 8, cr: 5 },
  { name: 'New Zealand', freq: 917.375, bw: 250, sf: 11, cr: 5 },
  { name: 'New Zealand (narrow)', freq: 917.375, bw: 62.5, sf: 7, cr: 5 },
  { name: 'EU/UK/Switzerland Long Range', freq: 869.525, bw: 250, sf: 11, cr: 5 },
  { name: 'EU/UK/Switzerland Medium Range', freq: 869.525, bw: 250, sf: 10, cr: 5 },
  { name: 'EU/UK/Switzerland Narrow', freq: 869.618, bw: 62.5, sf: 8, cr: 8 },
  { name: 'Czech Republic (Narrow)', freq: 869.432, bw: 62.5, sf: 7, cr: 5 },
  { name: 'EU 433MHz Long Range', freq: 433.65, bw: 250, sf: 11, cr: 5 },
  { name: 'Portugal 433MHz', freq: 433.375, bw: 62.5, sf: 9, cr: 6 },
  { name: 'Portugal 868MHz', freq: 869.618, bw: 62.5, sf: 7, cr: 6 },
  { name: 'Vietnam', freq: 920.25, bw: 250, sf: 11, cr: 5 },
];

interface SettingsModalProps {
  open: boolean;
  config: RadioConfig | null;
  health: HealthStatus | null;
  appSettings: AppSettings | null;
  onClose: () => void;
  onSave: (update: RadioConfigUpdate) => Promise<void>;
  onSaveAppSettings: (update: AppSettingsUpdate) => Promise<void>;
  onSetPrivateKey: (key: string) => Promise<void>;
  onReboot: () => Promise<void>;
  onAdvertise: () => Promise<void>;
  onHealthRefresh: () => Promise<void>;
  onRefreshAppSettings: () => Promise<void>;
}

export function SettingsModal({
  open,
  config,
  health,
  appSettings,
  onClose,
  onSave,
  onSaveAppSettings,
  onSetPrivateKey,
  onReboot,
  onAdvertise,
  onHealthRefresh,
  onRefreshAppSettings,
}: SettingsModalProps) {
  // Tab state
  type SettingsTab = 'radio' | 'identity' | 'serial' | 'database' | 'bot';
  const [activeTab, setActiveTab] = useState<SettingsTab>('radio');

  // Radio config state
  const [name, setName] = useState('');
  const [lat, setLat] = useState('');
  const [lon, setLon] = useState('');
  const [txPower, setTxPower] = useState('');
  const [freq, setFreq] = useState('');
  const [bw, setBw] = useState('');
  const [sf, setSf] = useState('');
  const [cr, setCr] = useState('');
  const [privateKey, setPrivateKey] = useState('');
  const [maxRadioContacts, setMaxRadioContacts] = useState('');

  // Loading states
  const [loading, setLoading] = useState(false);
  const [rebooting, setRebooting] = useState(false);
  const [advertising, setAdvertising] = useState(false);
  const [gettingLocation, setGettingLocation] = useState(false);
  const [error, setError] = useState('');

  // Database maintenance state
  const [retentionDays, setRetentionDays] = useState('14');
  const [cleaning, setCleaning] = useState(false);
  const [autoDecryptOnAdvert, setAutoDecryptOnAdvert] = useState(false);

  // Advertisement interval state
  const [advertInterval, setAdvertInterval] = useState('0');

  // Bot state
  const DEFAULT_BOT_CODE = `def bot(
    sender_name: str | None,
    sender_key: str | None,
    message_text: str,
    is_dm: bool,
    channel_key: str | None,
    channel_name: str | None,
    sender_timestamp: int | None,
    path: str | None,
) -> str | list[str] | None:
    """
    Process incoming messages and optionally return a reply.

    Args:
        sender_name: Display name of sender (may be None)
        sender_key: 64-char hex public key (None for channel msgs)
        message_text: The message content
        is_dm: True for direct messages, False for channel
        channel_key: 32-char hex key for channels, None for DMs
        channel_name: Channel name with hash (e.g. "#bot"), None for DMs
        sender_timestamp: Sender's timestamp (unix seconds, may be None)
        path: Hex-encoded routing path (may be None)

    Returns:
        None for no reply, a string for a single reply,
        or a list of strings to send multiple messages in order
    """
    # Example: Only respond in #bot channel to "!pling" command
    if channel_name == "#bot" and "!pling" in message_text.lower():
        return "[BOT] Plong!"
    return None`;
  const [bots, setBots] = useState<BotConfig[]>([]);
  const [expandedBotId, setExpandedBotId] = useState<string | null>(null);
  const [editingNameId, setEditingNameId] = useState<string | null>(null);
  const [editingNameValue, setEditingNameValue] = useState('');

  useEffect(() => {
    if (config) {
      setName(config.name);
      setLat(String(config.lat));
      setLon(String(config.lon));
      setTxPower(String(config.tx_power));
      setFreq(String(config.radio.freq));
      setBw(String(config.radio.bw));
      setSf(String(config.radio.sf));
      setCr(String(config.radio.cr));
    }
  }, [config]);

  useEffect(() => {
    if (appSettings) {
      setMaxRadioContacts(String(appSettings.max_radio_contacts));
      setAutoDecryptOnAdvert(appSettings.auto_decrypt_dm_on_advert);
      setAdvertInterval(String(appSettings.advert_interval));
      setBots(appSettings.bots || []);
    }
  }, [appSettings]);

  // Refresh settings from server when modal opens
  // This ensures UI reflects actual server state (prevents stale UI after checkbox toggle without save)
  useEffect(() => {
    if (open) {
      onRefreshAppSettings();
    }
  }, [open, onRefreshAppSettings]);

  // Detect current preset from form values
  const currentPreset = useMemo(() => {
    const freqNum = parseFloat(freq);
    const bwNum = parseFloat(bw);
    const sfNum = parseInt(sf, 10);
    const crNum = parseInt(cr, 10);

    for (const preset of RADIO_PRESETS) {
      if (
        preset.freq === freqNum &&
        preset.bw === bwNum &&
        preset.sf === sfNum &&
        preset.cr === crNum
      ) {
        return preset.name;
      }
    }
    return 'custom';
  }, [freq, bw, sf, cr]);

  const handlePresetChange = (presetName: string) => {
    if (presetName === 'custom') return;
    const preset = RADIO_PRESETS.find((p) => p.name === presetName);
    if (preset) {
      setFreq(String(preset.freq));
      setBw(String(preset.bw));
      setSf(String(preset.sf));
      setCr(String(preset.cr));
    }
  };

  const handleGetLocation = () => {
    if (!navigator.geolocation) {
      toast.error('Geolocation not supported', {
        description: 'Your browser does not support geolocation',
      });
      return;
    }

    setGettingLocation(true);
    navigator.geolocation.getCurrentPosition(
      (position) => {
        setLat(position.coords.latitude.toFixed(6));
        setLon(position.coords.longitude.toFixed(6));
        setGettingLocation(false);
        toast.success('Location updated');
      },
      (err) => {
        setGettingLocation(false);
        toast.error('Failed to get location', {
          description: err.message,
        });
      },
      { enableHighAccuracy: true, timeout: 10000 }
    );
  };

  const handleSaveRadioConfig = async () => {
    setError('');
    setLoading(true);

    try {
      const update: RadioConfigUpdate = {
        lat: parseFloat(lat),
        lon: parseFloat(lon),
        tx_power: parseInt(txPower, 10),
        radio: {
          freq: parseFloat(freq),
          bw: parseFloat(bw),
          sf: parseInt(sf, 10),
          cr: parseInt(cr, 10),
        },
      };
      await onSave(update);
      toast.success('Radio config saved, rebooting...');
      setLoading(false);
      setRebooting(true);
      await onReboot();
      onClose();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to save');
      setLoading(false);
    } finally {
      setRebooting(false);
    }
  };

  const handleSaveIdentity = async () => {
    setError('');
    setLoading(true);

    try {
      // Save radio name
      const update: RadioConfigUpdate = { name };
      await onSave(update);

      // Save advert interval to app settings
      const newAdvertInterval = parseInt(advertInterval, 10);
      if (!isNaN(newAdvertInterval) && newAdvertInterval !== appSettings?.advert_interval) {
        await onSaveAppSettings({ advert_interval: newAdvertInterval });
      }

      toast.success('Identity settings saved');
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to save');
    } finally {
      setLoading(false);
    }
  };

  const handleSaveSerial = async () => {
    setError('');
    setLoading(true);

    try {
      const newMaxRadioContacts = parseInt(maxRadioContacts, 10);
      if (!isNaN(newMaxRadioContacts) && newMaxRadioContacts !== appSettings?.max_radio_contacts) {
        await onSaveAppSettings({ max_radio_contacts: newMaxRadioContacts });
      }
      toast.success('Serial settings saved');
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to save');
    } finally {
      setLoading(false);
    }
  };

  const handleSetPrivateKey = async () => {
    if (!privateKey.trim()) {
      setError('Private key is required');
      return;
    }
    setError('');
    setLoading(true);

    try {
      await onSetPrivateKey(privateKey.trim());
      setPrivateKey('');
      toast.success('Private key set, rebooting...');
      setLoading(false);
      setRebooting(true);
      await onReboot();
      onClose();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to set private key');
      setLoading(false);
    } finally {
      setRebooting(false);
    }
  };

  const handleReboot = async () => {
    if (
      !confirm('Are you sure you want to reboot the radio? The connection will drop temporarily.')
    ) {
      return;
    }
    setError('');
    setRebooting(true);

    try {
      await onReboot();
      onClose();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to reboot radio');
    } finally {
      setRebooting(false);
    }
  };

  const handleAdvertise = async () => {
    setAdvertising(true);
    try {
      await onAdvertise();
    } finally {
      setAdvertising(false);
    }
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
      const result = await api.runMaintenance(days);
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

  const handleSaveDatabaseSettings = async () => {
    setLoading(true);
    setError('');

    try {
      await onSaveAppSettings({ auto_decrypt_dm_on_advert: autoDecryptOnAdvert });
      toast.success('Database settings saved');
    } catch (err) {
      console.error('Failed to save database settings:', err);
      setError(err instanceof Error ? err.message : 'Failed to save');
      toast.error('Failed to save settings');
    } finally {
      setLoading(false);
    }
  };

  const handleSaveBotSettings = async () => {
    setLoading(true);
    setError('');

    try {
      await onSaveAppSettings({ bots });
      toast.success('Bot settings saved');
    } catch (err) {
      console.error('Failed to save bot settings:', err);
      const errorMsg = err instanceof Error ? err.message : 'Failed to save';
      setError(errorMsg);
      toast.error(errorMsg);
    } finally {
      setLoading(false);
    }
  };

  const handleAddBot = () => {
    const newBot: BotConfig = {
      id: crypto.randomUUID(),
      name: `Bot ${bots.length + 1}`,
      enabled: false,
      code: DEFAULT_BOT_CODE,
    };
    setBots([...bots, newBot]);
    setExpandedBotId(newBot.id);
  };

  const handleDeleteBot = (botId: string) => {
    const bot = bots.find((b) => b.id === botId);
    if (bot && bot.code.trim() && bot.code !== DEFAULT_BOT_CODE) {
      if (!confirm(`Delete "${bot.name}"? This will remove all its code.`)) {
        return;
      }
    }
    setBots(bots.filter((b) => b.id !== botId));
    if (expandedBotId === botId) {
      setExpandedBotId(null);
    }
  };

  const handleToggleBotEnabled = (botId: string) => {
    setBots(bots.map((b) => (b.id === botId ? { ...b, enabled: !b.enabled } : b)));
  };

  const handleBotCodeChange = (botId: string, code: string) => {
    setBots(bots.map((b) => (b.id === botId ? { ...b, code } : b)));
  };

  const handleStartEditingName = (bot: BotConfig) => {
    setEditingNameId(bot.id);
    setEditingNameValue(bot.name);
  };

  const handleFinishEditingName = () => {
    if (editingNameId && editingNameValue.trim()) {
      setBots(
        bots.map((b) => (b.id === editingNameId ? { ...b, name: editingNameValue.trim() } : b))
      );
    }
    setEditingNameId(null);
    setEditingNameValue('');
  };

  const handleResetBotCode = (botId: string) => {
    setBots(bots.map((b) => (b.id === botId ? { ...b, code: DEFAULT_BOT_CODE } : b)));
  };

  return (
    <Dialog open={open} onOpenChange={(isOpen) => !isOpen && onClose()}>
      <DialogContent className="sm:max-w-[50vw] sm:min-w-[500px] max-h-[90vh] overflow-y-auto">
        <DialogHeader>
          <DialogTitle>Radio & Settings</DialogTitle>
          <DialogDescription className="sr-only">
            {activeTab === 'radio' && 'Configure radio frequency, power, and location settings'}
            {activeTab === 'identity' &&
              'Manage radio name, public key, private key, and advertising settings'}
            {activeTab === 'serial' && 'View serial port connection and configure contact sync'}
            {activeTab === 'database' && 'View database statistics and clean up old packets'}
            {activeTab === 'bot' && 'Configure automatic message bot with Python code'}
          </DialogDescription>
        </DialogHeader>

        {!config ? (
          <div className="py-8 text-center text-muted-foreground">Loading configuration...</div>
        ) : (
          <Tabs
            value={activeTab}
            onValueChange={(v) => setActiveTab(v as SettingsTab)}
            className="w-full"
          >
            <TabsList className="grid w-full grid-cols-5">
              <TabsTrigger value="radio">Radio</TabsTrigger>
              <TabsTrigger value="identity">Identity</TabsTrigger>
              <TabsTrigger value="serial">Serial</TabsTrigger>
              <TabsTrigger value="database">Database</TabsTrigger>
              <TabsTrigger value="bot">Bot</TabsTrigger>
            </TabsList>

            {/* Radio Config Tab */}
            <TabsContent value="radio" className="space-y-4 mt-4">
              <div className="space-y-2">
                <Label htmlFor="preset">Preset</Label>
                <select
                  id="preset"
                  value={currentPreset}
                  onChange={(e) => handlePresetChange(e.target.value)}
                  className="w-full h-10 px-3 rounded-md border border-input bg-background text-sm ring-offset-background focus:outline-none focus:ring-2 focus:ring-ring focus:ring-offset-2"
                >
                  <option value="custom">Custom</option>
                  {RADIO_PRESETS.map((preset) => (
                    <option key={preset.name} value={preset.name}>
                      {preset.name}
                    </option>
                  ))}
                </select>
              </div>

              <div className="grid grid-cols-2 gap-4">
                <div className="space-y-2">
                  <Label htmlFor="freq">Frequency (MHz)</Label>
                  <Input
                    id="freq"
                    type="number"
                    step="any"
                    value={freq}
                    onChange={(e) => setFreq(e.target.value)}
                  />
                </div>
                <div className="space-y-2">
                  <Label htmlFor="bw">Bandwidth (kHz)</Label>
                  <Input
                    id="bw"
                    type="number"
                    step="any"
                    value={bw}
                    onChange={(e) => setBw(e.target.value)}
                  />
                </div>
              </div>

              <div className="grid grid-cols-2 gap-4">
                <div className="space-y-2">
                  <Label htmlFor="sf">Spreading Factor</Label>
                  <Input
                    id="sf"
                    type="number"
                    min="7"
                    max="12"
                    value={sf}
                    onChange={(e) => setSf(e.target.value)}
                  />
                </div>
                <div className="space-y-2">
                  <Label htmlFor="cr">Coding Rate</Label>
                  <Input
                    id="cr"
                    type="number"
                    min="5"
                    max="8"
                    value={cr}
                    onChange={(e) => setCr(e.target.value)}
                  />
                </div>
              </div>

              <div className="grid grid-cols-2 gap-4">
                <div className="space-y-2">
                  <Label htmlFor="tx-power">TX Power (dBm)</Label>
                  <Input
                    id="tx-power"
                    type="number"
                    value={txPower}
                    onChange={(e) => setTxPower(e.target.value)}
                  />
                </div>
                <div className="space-y-2">
                  <Label htmlFor="max-tx">Max TX Power</Label>
                  <Input id="max-tx" type="number" value={config.max_tx_power} disabled />
                </div>
              </div>

              <Separator />

              <div className="space-y-2">
                <div className="flex items-center justify-between">
                  <Label>Location</Label>
                  <Button
                    type="button"
                    variant="outline"
                    size="sm"
                    onClick={handleGetLocation}
                    disabled={gettingLocation}
                  >
                    {gettingLocation ? 'Getting...' : '📍 Use My Location'}
                  </Button>
                </div>
                <div className="grid grid-cols-2 gap-4">
                  <div className="space-y-2">
                    <Label htmlFor="lat" className="text-xs text-muted-foreground">
                      Latitude
                    </Label>
                    <Input
                      id="lat"
                      type="number"
                      step="any"
                      value={lat}
                      onChange={(e) => setLat(e.target.value)}
                    />
                  </div>
                  <div className="space-y-2">
                    <Label htmlFor="lon" className="text-xs text-muted-foreground">
                      Longitude
                    </Label>
                    <Input
                      id="lon"
                      type="number"
                      step="any"
                      value={lon}
                      onChange={(e) => setLon(e.target.value)}
                    />
                  </div>
                </div>
              </div>

              {error && <div className="text-sm text-destructive">{error}</div>}

              <Button
                onClick={handleSaveRadioConfig}
                disabled={loading || rebooting}
                className="w-full"
              >
                {loading || rebooting ? 'Saving & Rebooting...' : 'Save Radio Config & Reboot'}
              </Button>
            </TabsContent>

            {/* Identity Tab */}
            <TabsContent value="identity" className="space-y-4 mt-4">
              <div className="space-y-2">
                <Label htmlFor="public-key">Public Key</Label>
                <Input
                  id="public-key"
                  value={config.public_key}
                  disabled
                  className="font-mono text-xs"
                />
              </div>

              <div className="space-y-2">
                <Label htmlFor="name">Radio Name</Label>
                <Input id="name" value={name} onChange={(e) => setName(e.target.value)} />
              </div>

              <div className="space-y-2">
                <Label htmlFor="advert-interval">Periodic Advertising Interval</Label>
                <div className="flex items-center gap-2">
                  <Input
                    id="advert-interval"
                    type="number"
                    min="0"
                    value={advertInterval}
                    onChange={(e) => setAdvertInterval(e.target.value)}
                    className="w-28"
                  />
                  <span className="text-sm text-muted-foreground">seconds (0 = off)</span>
                </div>
                <p className="text-xs text-muted-foreground">
                  How often to automatically advertise presence. Set to 0 to disable. Recommended:
                  86400 (24 hours) or higher.
                </p>
              </div>

              <Button onClick={handleSaveIdentity} disabled={loading} className="w-full">
                {loading ? 'Saving...' : 'Save Identity Settings'}
              </Button>

              <Separator />

              <div className="space-y-2">
                <Label htmlFor="private-key">Set Private Key (write-only)</Label>
                <Input
                  id="private-key"
                  type="password"
                  autoComplete="off"
                  value={privateKey}
                  onChange={(e) => setPrivateKey(e.target.value)}
                  placeholder="64-character hex private key"
                />
                <Button
                  onClick={handleSetPrivateKey}
                  disabled={loading || rebooting || !privateKey.trim()}
                  className="w-full"
                >
                  {loading || rebooting ? 'Setting & Rebooting...' : 'Set Private Key & Reboot'}
                </Button>
              </div>

              <Separator />

              <div className="space-y-2">
                <Label>Send Advertisement</Label>
                <p className="text-xs text-muted-foreground">
                  Send a flood advertisement to announce your presence on the mesh network.
                </p>
                <Button
                  onClick={handleAdvertise}
                  disabled={advertising || !health?.radio_connected}
                  className="w-full bg-yellow-600 hover:bg-yellow-700 text-white"
                >
                  {advertising ? 'Sending...' : 'Send Advertisement'}
                </Button>
                {!health?.radio_connected && (
                  <p className="text-sm text-destructive">Radio not connected</p>
                )}
              </div>

              {error && <div className="text-sm text-destructive">{error}</div>}
            </TabsContent>

            {/* Serial Tab */}
            <TabsContent value="serial" className="space-y-4 mt-4">
              <div className="space-y-2">
                <Label>Serial Port</Label>
                {health?.serial_port ? (
                  <div className="flex items-center gap-2">
                    <div className="w-2 h-2 rounded-full bg-green-500" />
                    <code className="px-2 py-1 bg-muted rounded text-foreground text-sm">
                      {health.serial_port}
                    </code>
                  </div>
                ) : (
                  <div className="flex items-center gap-2 text-muted-foreground">
                    <div className="w-2 h-2 rounded-full bg-gray-500" />
                    <span>Not connected</span>
                  </div>
                )}
              </div>

              <Separator />

              <div className="space-y-2">
                <Label htmlFor="max-contacts">Max Contacts on Radio</Label>
                <Input
                  id="max-contacts"
                  type="number"
                  min="1"
                  max="1000"
                  value={maxRadioContacts}
                  onChange={(e) => setMaxRadioContacts(e.target.value)}
                />
                <p className="text-xs text-muted-foreground">
                  Recent non-repeater contacts loaded to radio for DM auto-ACK (1-1000)
                </p>
              </div>

              <Button onClick={handleSaveSerial} disabled={loading} className="w-full">
                {loading ? 'Saving...' : 'Save Settings'}
              </Button>

              <Separator />

              <Button
                variant="outline"
                onClick={handleReboot}
                disabled={rebooting || loading}
                className="w-full border-red-500/50 text-red-400 hover:bg-red-500/10"
              >
                {rebooting ? 'Rebooting...' : 'Reboot Radio'}
              </Button>

              {error && <div className="text-sm text-destructive">{error}</div>}
            </TabsContent>

            {/* Database Tab */}
            <TabsContent value="database" className="space-y-4 mt-4">
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
                        (
                        {Math.floor(
                          (Date.now() / 1000 - health.oldest_undecrypted_timestamp) / 86400
                        )}{' '}
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
                <Label>Cleanup Old Packets</Label>
                <p className="text-xs text-muted-foreground">
                  Delete undecrypted packets older than the specified days. This helps manage
                  storage for packets that couldn't be decrypted (unknown channel keys).
                </p>
                <div className="flex gap-2 items-end">
                  <div className="space-y-1">
                    <Label htmlFor="retention-days" className="text-xs">
                      Days to retain
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
                  <Button variant="outline" onClick={handleCleanup} disabled={cleaning}>
                    {cleaning ? 'Cleaning...' : 'Cleanup'}
                  </Button>
                </div>
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
                  <span className="text-sm">
                    Auto-decrypt historical DMs when new contact advertises
                  </span>
                </label>
                <p className="text-xs text-muted-foreground">
                  When enabled, the server will automatically try to decrypt stored DM packets when
                  a new contact sends an advertisement. This may cause brief delays on large packet
                  backlogs.
                </p>
              </div>

              {error && <div className="text-sm text-destructive">{error}</div>}

              <Button onClick={handleSaveDatabaseSettings} disabled={loading} className="w-full">
                {loading ? 'Saving...' : 'Save Settings'}
              </Button>
            </TabsContent>

            {/* Bot Tab */}
            <TabsContent value="bot" className="space-y-4 mt-4">
              <div className="p-3 bg-red-500/10 border border-red-500/30 rounded-md">
                <p className="text-sm text-red-500">
                  <strong>Experimental:</strong> This is an alpha feature and introduces automated
                  message sending to your radio; unexpected behavior may occur. Use with caution,
                  and please report any bugs!
                </p>
              </div>

              <div className="p-3 bg-yellow-500/10 border border-yellow-500/30 rounded-md">
                <p className="text-sm text-yellow-500">
                  <strong>Security Warning:</strong> This feature executes arbitrary Python code on
                  the server. Only run trusted code, and be cautious of arbitrary usage of message
                  parameters.
                </p>
              </div>

              <div className="p-3 bg-yellow-500/10 border border-yellow-500/30 rounded-md">
                <p className="text-sm text-yellow-500">
                  <strong>Don&apos;t wreck the mesh!</strong> Bots process ALL messages, including
                  their own. Be careful of creating infinite loops!
                </p>
              </div>

              <div className="flex justify-between items-center">
                <Label>Bots</Label>
                <Button type="button" variant="outline" size="sm" onClick={handleAddBot}>
                  + New Bot
                </Button>
              </div>

              {bots.length === 0 ? (
                <div className="text-center py-8 border border-dashed border-input rounded-md">
                  <p className="text-muted-foreground mb-4">No bots configured</p>
                  <Button type="button" variant="outline" onClick={handleAddBot}>
                    Create your first bot
                  </Button>
                </div>
              ) : (
                <div className="space-y-2">
                  {bots.map((bot) => (
                    <div key={bot.id} className="border border-input rounded-md overflow-hidden">
                      {/* Bot header row */}
                      <div
                        className="flex items-center gap-2 px-3 py-2 bg-muted/50 cursor-pointer hover:bg-muted/80"
                        onClick={(e) => {
                          // Don't toggle if clicking on interactive elements
                          if ((e.target as HTMLElement).closest('input, button')) return;
                          setExpandedBotId(expandedBotId === bot.id ? null : bot.id);
                        }}
                      >
                        <span className="text-muted-foreground">
                          {expandedBotId === bot.id ? '▼' : '▶'}
                        </span>

                        {/* Bot name (click to edit) */}
                        {editingNameId === bot.id ? (
                          <input
                            type="text"
                            value={editingNameValue}
                            onChange={(e) => setEditingNameValue(e.target.value)}
                            onBlur={handleFinishEditingName}
                            onKeyDown={(e) => {
                              if (e.key === 'Enter') handleFinishEditingName();
                              if (e.key === 'Escape') {
                                setEditingNameId(null);
                                setEditingNameValue('');
                              }
                            }}
                            autoFocus
                            className="px-2 py-0.5 text-sm bg-background border border-input rounded flex-1 max-w-[200px]"
                            onClick={(e) => e.stopPropagation()}
                          />
                        ) : (
                          <span
                            className="text-sm font-medium flex-1 hover:text-primary cursor-text"
                            onClick={(e) => {
                              e.stopPropagation();
                              handleStartEditingName(bot);
                            }}
                            title="Click to rename"
                          >
                            {bot.name}
                          </span>
                        )}

                        {/* Enabled checkbox */}
                        <label
                          className="flex items-center gap-1.5 cursor-pointer"
                          onClick={(e) => e.stopPropagation()}
                        >
                          <input
                            type="checkbox"
                            checked={bot.enabled}
                            onChange={() => handleToggleBotEnabled(bot.id)}
                            className="w-4 h-4 rounded border-input accent-primary"
                          />
                          <span className="text-xs text-muted-foreground">Enabled</span>
                        </label>

                        {/* Delete button */}
                        <Button
                          type="button"
                          variant="ghost"
                          size="sm"
                          className="h-6 w-6 p-0 text-muted-foreground hover:text-destructive"
                          onClick={(e) => {
                            e.stopPropagation();
                            handleDeleteBot(bot.id);
                          }}
                          title="Delete bot"
                        >
                          🗑
                        </Button>
                      </div>

                      {/* Bot expanded content */}
                      {expandedBotId === bot.id && (
                        <div className="p-3 space-y-3 border-t border-input">
                          <div className="flex items-center justify-between">
                            <p className="text-xs text-muted-foreground">
                              Define a <code className="bg-muted px-1 rounded">bot()</code> function
                              that receives message data and optionally returns a reply.
                            </p>
                            <Button
                              type="button"
                              variant="outline"
                              size="sm"
                              onClick={() => handleResetBotCode(bot.id)}
                            >
                              Reset to Example
                            </Button>
                          </div>
                          <Suspense
                            fallback={
                              <div className="h-64 rounded-md border border-input bg-[#282c34] flex items-center justify-center text-muted-foreground">
                                Loading editor...
                              </div>
                            }
                          >
                            <BotCodeEditor
                              value={bot.code}
                              onChange={(code) => handleBotCodeChange(bot.id, code)}
                              id={`bot-code-${bot.id}`}
                            />
                          </Suspense>
                        </div>
                      )}
                    </div>
                  ))}
                </div>
              )}

              <Separator />

              <div className="text-xs text-muted-foreground space-y-1">
                <p>
                  <strong>Available:</strong> Standard Python libraries and any modules installed in
                  the server environment.
                </p>
                <p>
                  <strong>Limits:</strong> 10 second timeout per bot.
                </p>
                <p>
                  <strong>Note:</strong> Bots respond to all messages, including your own. For
                  channel messages, <code>sender_key</code> is <code>None</code>. Multiple enabled
                  bots run serially, with a two-second delay between messages to prevent repeater
                  collision.
                </p>
              </div>

              {error && <div className="text-sm text-destructive">{error}</div>}

              <Button onClick={handleSaveBotSettings} disabled={loading} className="w-full">
                {loading ? 'Saving...' : 'Save Bot Settings'}
              </Button>
            </TabsContent>
          </Tabs>
        )}
      </DialogContent>
    </Dialog>
  );
}
