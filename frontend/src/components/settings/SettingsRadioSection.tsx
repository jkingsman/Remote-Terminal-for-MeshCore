import { useState, useEffect, useMemo, useRef } from 'react';
import { ChevronDown, Download, MapPinned, Upload } from 'lucide-react';
import { Input } from '../ui/input';
import { Label } from '../ui/label';
import { Button } from '../ui/button';
import { Separator } from '../ui/separator';
import { toast } from '../ui/sonner';
import { Checkbox } from '../ui/checkbox';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '../ui/dialog';
import { api } from '../../api';
import { RADIO_PRESETS } from '../../utils/radioPresets';
import { stripRegionScopePrefix } from '../../utils/regionScope';
import type {
  AppSettings,
  AppSettingsUpdate,
  FreqRange,
  HealthStatus,
  RadioAdvertMode,
  RadioConfig,
  RadioConfigUpdate,
  RadioDiscoveryResponse,
  RadioDiscoveryTarget,
  RadioRegionDiscoveryResponse,
  RadioStatsSnapshot,
} from '../../types';

function formatUptime(secs: number): string {
  const days = Math.floor(secs / 86400);
  const hours = Math.floor((secs % 86400) / 3600);
  const minutes = Math.floor((secs % 3600) / 60);
  if (days > 0) return `${days}d ${hours}h ${minutes}m`;
  if (hours > 0) return `${hours}h ${minutes}m`;
  return `${minutes}m`;
}

function formatAirtime(secs: number): string {
  if (secs < 60) return `${secs}s`;
  const hours = Math.floor(secs / 3600);
  const minutes = Math.floor((secs % 3600) / 60);
  if (hours > 0) return `${hours}h ${minutes}m`;
  return `${minutes}m`;
}

function formatFreqRange({ min_mhz, max_mhz }: FreqRange): string {
  return Math.round(min_mhz * 1000) === Math.round(max_mhz * 1000)
    ? min_mhz.toFixed(3)
    : `${min_mhz.toFixed(3)}–${max_mhz.toFixed(3)}`;
}

function StatRow({ label, value, warn }: { label: string; value: string; warn?: boolean }) {
  return (
    <div className="flex items-center justify-between gap-2 py-0.5">
      <span className="text-xs text-muted-foreground">{label}</span>
      <span
        className={`text-xs font-mono tabular-nums ${warn ? 'text-warning font-semibold' : ''}`}
      >
        {value}
      </span>
    </div>
  );
}

function RadioDetailsCollapsible({ stats }: { stats: RadioStatsSnapshot }) {
  const age = stats.timestamp ? Math.max(0, Math.floor(Date.now() / 1000) - stats.timestamp) : null;
  const packets = {
    recv: stats.packets_recv,
    sent: stats.packets_sent,
    flood_tx: stats.flood_tx,
    direct_tx: stats.direct_tx,
    flood_rx: stats.flood_rx,
    direct_rx: stats.direct_rx,
  };

  return (
    <details className="group">
      <summary className="text-sm font-medium text-foreground cursor-pointer select-none flex items-center gap-1">
        <ChevronDown className="h-3 w-3 transition-transform group-open:rotate-0 -rotate-90" />
        Radio Details
      </summary>
      <div className="mt-2 space-y-2 rounded-md border border-input bg-muted/20 p-3">
        {age !== null && (
          <p className="text-[0.625rem] uppercase tracking-wider text-muted-foreground font-medium">
            Updated {age < 5 ? 'just now' : `${age}s ago`}
          </p>
        )}

        {/* Core */}
        {stats.uptime_secs != null && (
          <StatRow label="Uptime" value={formatUptime(stats.uptime_secs)} />
        )}
        {stats.battery_mv != null && stats.battery_mv > 0 && (
          <StatRow label="Battery" value={`${(stats.battery_mv / 1000).toFixed(2)}V`} />
        )}
        {stats.queue_len != null && (
          <StatRow
            label="TX Queue"
            value={`${stats.queue_len} / 16`}
            warn={stats.queue_len >= 14}
          />
        )}
        {stats.errors != null && (
          <StatRow label="Errors" value={String(stats.errors)} warn={stats.errors > 0} />
        )}

        {/* RF */}
        {stats.noise_floor != null && (
          <StatRow label="Noise Floor" value={`${stats.noise_floor} dBm`} />
        )}
        {stats.last_rssi != null && <StatRow label="Last RSSI" value={`${stats.last_rssi} dBm`} />}
        {stats.last_snr != null && <StatRow label="Last SNR" value={`${stats.last_snr} dB`} />}

        {/* Airtime */}
        {(stats.tx_air_secs != null || stats.rx_air_secs != null) && (
          <>
            {stats.tx_air_secs != null && (
              <StatRow label="TX Airtime" value={formatAirtime(stats.tx_air_secs)} />
            )}
            {stats.rx_air_secs != null && (
              <StatRow label="RX Airtime" value={formatAirtime(stats.rx_air_secs)} />
            )}
          </>
        )}

        {/* Packets */}
        {packets.recv != null && <StatRow label="Packets Received" value={String(packets.recv)} />}
        {packets.sent != null && <StatRow label="Packets Sent" value={String(packets.sent)} />}
        {packets.flood_tx != null && <StatRow label="Flood TX" value={String(packets.flood_tx)} />}
        {packets.flood_rx != null && <StatRow label="Flood RX" value={String(packets.flood_rx)} />}
        {packets.direct_tx != null && (
          <StatRow label="Direct TX" value={String(packets.direct_tx)} />
        )}
        {packets.direct_rx != null && (
          <StatRow label="Direct RX" value={String(packets.direct_rx)} />
        )}
      </div>
    </details>
  );
}

export function SettingsRadioSection({
  config,
  health,
  appSettings,
  pageMode,
  onSave,
  onSaveAppSettings,
  onSetPrivateKey,
  onReboot,
  onDisconnect,
  onReconnect,
  onAdvertise,
  meshDiscovery,
  meshDiscoveryLoadingTarget,
  onDiscoverMesh,
  regionDiscovery,
  regionDiscoveryLoading,
  onDiscoverRegions,
  onClose,
  className,
}: {
  config: RadioConfig;
  health: HealthStatus | null;
  appSettings: AppSettings;
  pageMode: boolean;
  onSave: (update: RadioConfigUpdate) => Promise<void>;
  onSaveAppSettings: (update: AppSettingsUpdate) => Promise<void>;
  onSetPrivateKey: (key: string) => Promise<void>;
  onReboot: () => Promise<void>;
  onDisconnect: () => Promise<void>;
  onReconnect: () => Promise<void>;
  onAdvertise: (mode: RadioAdvertMode) => Promise<void>;
  meshDiscovery: RadioDiscoveryResponse | null;
  meshDiscoveryLoadingTarget: RadioDiscoveryTarget | null;
  onDiscoverMesh: (target: RadioDiscoveryTarget) => Promise<void>;
  regionDiscovery: RadioRegionDiscoveryResponse | null;
  regionDiscoveryLoading: boolean;
  onDiscoverRegions: (publicKeys?: string[]) => Promise<void>;
  onClose: () => void;
  className?: string;
}) {
  // Radio config state
  const [name, setName] = useState('');
  const [lat, setLat] = useState('');
  const [lon, setLon] = useState('');
  const [txPower, setTxPower] = useState('');
  const [freq, setFreq] = useState('');
  const [bw, setBw] = useState('');
  const [sf, setSf] = useState('');
  const [cr, setCr] = useState('');
  const [pathHashMode, setPathHashMode] = useState('0');
  const [advertLocationSource, setAdvertLocationSource] = useState<'off' | 'current'>('current');
  const [multiAcksEnabled, setMultiAcksEnabled] = useState(false);
  const [repeatEnabled, setRepeatEnabled] = useState(false);
  const [telemetryModeBase, setTelemetryModeBase] = useState(0);
  const [telemetryModeLoc, setTelemetryModeLoc] = useState(0);
  const [telemetryModeEnv, setTelemetryModeEnv] = useState(0);
  const [gettingLocation, setGettingLocation] = useState(false);
  const [busy, setBusy] = useState(false);
  const [rebooting, setRebooting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Identity state
  const [privateKey, setPrivateKey] = useState('');
  const [identityBusy, setIdentityBusy] = useState(false);
  const [identityRebooting, setIdentityRebooting] = useState(false);
  const [identityError, setIdentityError] = useState<string | null>(null);

  // Flood & advert control state
  const [advertIntervalHours, setAdvertIntervalHours] = useState('0');
  const [floodScope, setFloodScope] = useState('');
  const [knownRegions, setKnownRegions] = useState('');
  const [maxRadioContacts, setMaxRadioContacts] = useState('');
  const [floodBusy, setFloodBusy] = useState(false);
  const [floodError, setFloodError] = useState<string | null>(null);

  // Advertise state
  const [advertisingMode, setAdvertisingMode] = useState<RadioAdvertMode | null>(null);
  const [discoverError, setDiscoverError] = useState<string | null>(null);
  const [connectionBusy, setConnectionBusy] = useState(false);

  useEffect(() => {
    setName(config.name);
    setLat(String(config.lat));
    setLon(String(config.lon));
    setTxPower(String(config.tx_power));
    setFreq(String(config.radio.freq));
    setBw(String(config.radio.bw));
    setSf(String(config.radio.sf));
    setCr(String(config.radio.cr));
    setPathHashMode(String(config.path_hash_mode));
    setAdvertLocationSource(config.advert_location_source ?? 'current');
    setMultiAcksEnabled(config.multi_acks_enabled ?? false);
    setRepeatEnabled(config.repeat_enabled ?? false);
    setTelemetryModeBase(config.telemetry_mode_base ?? 0);
    setTelemetryModeLoc(config.telemetry_mode_loc ?? 0);
    setTelemetryModeEnv(config.telemetry_mode_env ?? 0);
  }, [config]);

  useEffect(() => {
    setAdvertIntervalHours(String(Math.round(appSettings.advert_interval / 3600)));
    setFloodScope(stripRegionScopePrefix(appSettings.flood_scope));
    setKnownRegions((appSettings.known_regions ?? []).join('\n'));
    setMaxRadioContacts(String(appSettings.max_radio_contacts));
  }, [appSettings]);

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

  const allowedRepeatFreqs = config.allowed_repeat_freqs ?? [];
  const enteredFreqKhz = Math.round(parseFloat(freq) * 1000);
  const repeatFreqAllowed =
    allowedRepeatFreqs.length === 0 ||
    allowedRepeatFreqs.some(
      (r) =>
        enteredFreqKhz >= Math.round(r.min_mhz * 1000) &&
        enteredFreqKhz <= Math.round(r.max_mhz * 1000)
    );

  const buildUpdate = (): RadioConfigUpdate | null => {
    const parsedLat = parseFloat(lat);
    const parsedLon = parseFloat(lon);
    const parsedTxPower = parseInt(txPower, 10);
    const parsedFreq = parseFloat(freq);
    const parsedBw = parseFloat(bw);
    const parsedSf = parseInt(sf, 10);
    const parsedCr = parseInt(cr, 10);

    if (
      [parsedLat, parsedLon, parsedTxPower, parsedFreq, parsedBw, parsedSf, parsedCr].some((v) =>
        isNaN(v)
      )
    ) {
      setError('All numeric fields must have valid values');
      return null;
    }

    const parsedPathHashMode = parseInt(pathHashMode, 10);

    return {
      name,
      lat: parsedLat,
      lon: parsedLon,
      tx_power: parsedTxPower,
      ...(advertLocationSource !== (config.advert_location_source ?? 'current')
        ? { advert_location_source: advertLocationSource }
        : {}),
      ...(multiAcksEnabled !== (config.multi_acks_enabled ?? false)
        ? { multi_acks_enabled: multiAcksEnabled }
        : {}),
      ...(config.repeat_supported && repeatEnabled !== (config.repeat_enabled ?? false)
        ? { repeat_enabled: repeatEnabled }
        : {}),
      ...(telemetryModeBase !== (config.telemetry_mode_base ?? 0)
        ? { telemetry_mode_base: telemetryModeBase }
        : {}),
      ...(telemetryModeLoc !== (config.telemetry_mode_loc ?? 0)
        ? { telemetry_mode_loc: telemetryModeLoc }
        : {}),
      ...(telemetryModeEnv !== (config.telemetry_mode_env ?? 0)
        ? { telemetry_mode_env: telemetryModeEnv }
        : {}),
      radio: {
        freq: parsedFreq,
        bw: parsedBw,
        sf: parsedSf,
        cr: parsedCr,
      },
      ...(config.path_hash_mode_supported &&
      !isNaN(parsedPathHashMode) &&
      parsedPathHashMode !== config.path_hash_mode
        ? { path_hash_mode: parsedPathHashMode }
        : {}),
    };
  };

  const handleSave = async () => {
    setError(null);
    const update = buildUpdate();
    if (!update) return;

    setBusy(true);
    try {
      await onSave(update);
      toast.success('Radio config saved');
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to save');
    } finally {
      setBusy(false);
    }
  };

  const handleSaveAndReboot = async () => {
    setError(null);
    const update = buildUpdate();
    if (!update) return;

    setBusy(true);
    try {
      await onSave(update);
      toast.success('Radio config saved, rebooting...');
      setRebooting(true);
      await onReboot();
      if (!pageMode) {
        onClose();
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to save');
    } finally {
      setRebooting(false);
      setBusy(false);
    }
  };

  const handleSetPrivateKey = async () => {
    if (!privateKey.trim()) {
      setIdentityError('Private key is required');
      return;
    }
    setIdentityError(null);
    setIdentityBusy(true);

    try {
      await onSetPrivateKey(privateKey.trim());
      setPrivateKey('');
      toast.success('Private key set, rebooting...');
      setIdentityRebooting(true);
      await onReboot();
      if (!pageMode) {
        onClose();
      }
    } catch (err) {
      setIdentityError(err instanceof Error ? err.message : 'Failed to set private key');
    } finally {
      setIdentityRebooting(false);
      setIdentityBusy(false);
    }
  };

  const handleSaveFloodSettings = async () => {
    setFloodError(null);
    setFloodBusy(true);

    try {
      const update: AppSettingsUpdate = {};
      if (floodScope !== stripRegionScopePrefix(appSettings.flood_scope)) {
        update.flood_scope = floodScope;
      }
      // Known regions: one per line (commas also accepted), trimmed, blanks dropped.
      const parsedRegions = knownRegions
        .split(/[\n,]/)
        .map((r) => r.trim())
        .filter((r) => r.length > 0);
      if (JSON.stringify(parsedRegions) !== JSON.stringify(appSettings.known_regions ?? [])) {
        update.known_regions = parsedRegions;
      }
      const newMaxRadioContacts = parseInt(maxRadioContacts, 10);
      if (!isNaN(newMaxRadioContacts) && newMaxRadioContacts !== appSettings.max_radio_contacts) {
        update.max_radio_contacts = newMaxRadioContacts;
      }
      if (Object.keys(update).length > 0) {
        await onSaveAppSettings(update);
      }
      toast.success('Settings saved');
    } catch (err) {
      setFloodError(err instanceof Error ? err.message : 'Failed to save');
    } finally {
      setFloodBusy(false);
    }
  };

  const [advertIntervalBusy, setAdvertIntervalBusy] = useState(false);
  const [advertIntervalError, setAdvertIntervalError] = useState<string | null>(null);

  const handleSaveAdvertInterval = async () => {
    setAdvertIntervalError(null);
    setAdvertIntervalBusy(true);

    try {
      const hours = parseInt(advertIntervalHours, 10);
      const newAdvertInterval = isNaN(hours) ? 0 : hours * 3600;
      if (newAdvertInterval !== appSettings.advert_interval) {
        await onSaveAppSettings({ advert_interval: newAdvertInterval });
      }
      toast.success('Advertising interval saved');
    } catch (err) {
      setAdvertIntervalError(err instanceof Error ? err.message : 'Failed to save');
    } finally {
      setAdvertIntervalBusy(false);
    }
  };

  const handleAdvertise = async (mode: RadioAdvertMode) => {
    setAdvertisingMode(mode);
    try {
      await onAdvertise(mode);
    } finally {
      setAdvertisingMode(null);
    }
  };

  const handleDiscover = async (target: RadioDiscoveryTarget) => {
    setDiscoverError(null);
    try {
      await onDiscoverMesh(target);
    } catch (err) {
      setDiscoverError(err instanceof Error ? err.message : 'Failed to run mesh discovery');
    }
  };

  const handleDiscoverRegions = async () => {
    // Prefer repeaters from the most recent mesh-discovery sweep (they just
    // answered, so they're likely in range for the direct-routed regions
    // request); otherwise let the backend pick recent repeater contacts.
    const discoveredRepeaterKeys = (meshDiscovery?.results ?? [])
      .filter((r) => r.node_type === 'repeater')
      .map((r) => r.public_key);
    await onDiscoverRegions(discoveredRepeaterKeys);
  };

  const handleAddDiscoveredRegions = () => {
    if (!regionDiscovery || regionDiscovery.regions.length === 0) return;
    const existing = knownRegions
      .split('\n')
      .map((s) => s.trim())
      .filter(Boolean);
    const seen = new Set(existing.map((s) => s.toLowerCase()));
    const additions = regionDiscovery.regions.filter((r) => !seen.has(r.toLowerCase()));
    if (additions.length === 0) {
      toast.info('All discovered regions are already listed');
      return;
    }
    setKnownRegions([...existing, ...additions].join('\n'));
    toast.success(
      `Added ${additions.length} region${additions.length === 1 ? '' : 's'} — review and Save Messaging Settings`
    );
  };

  const importInputRef = useRef<HTMLInputElement>(null);
  const [keyImportDialogOpen, setKeyImportDialogOpen] = useState(false);
  const pendingImportRef = useRef<Record<string, unknown> | null>(null);

  const buildConfigProfile = () => ({
    version: 1,
    exported_at: new Date().toISOString(),
    name: config.name,
    lat: config.lat,
    lon: config.lon,
    tx_power: config.tx_power,
    radio: { ...config.radio },
    path_hash_mode: config.path_hash_mode,
    advert_location_source: config.advert_location_source ?? 'current',
    multi_acks_enabled: config.multi_acks_enabled ?? false,
    repeat_enabled: config.repeat_enabled ?? false,
    telemetry_mode_base: config.telemetry_mode_base ?? 0,
    telemetry_mode_loc: config.telemetry_mode_loc ?? 0,
    telemetry_mode_env: config.telemetry_mode_env ?? 0,
  });

  const downloadJson = (profile: object, suffix: string) => {
    const blob = new Blob([JSON.stringify(profile, null, 2)], { type: 'application/json' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    const safeName = (config.name || 'radio').replace(/[^a-zA-Z0-9_-]/g, '_');
    const timestamp = new Date()
      .toLocaleString(undefined, {
        year: 'numeric',
        month: '2-digit',
        day: '2-digit',
        hour: '2-digit',
        minute: '2-digit',
        second: '2-digit',
        hour12: false,
      })
      .replace(/[/:, ]+/g, '-');
    a.download = `${safeName}-${suffix}-${timestamp}.json`;
    a.click();
    URL.revokeObjectURL(url);
  };

  const handleExportConfig = async () => {
    const profile = buildConfigProfile();
    try {
      const { private_key } = await api.getPrivateKey();
      downloadJson({ ...profile, private_key }, 'config');
      toast.success('Export generated with private key');
    } catch {
      downloadJson(profile, 'config');
      toast.info('Export generated without private key', {
        description: 'See README_ADVANCED.md for private key export enable',
      });
    }
  };

  const validateImportData = (
    data: unknown
  ): data is {
    name: string;
    radio: { freq: number; bw: number; sf: number; cr: number };
    [k: string]: unknown;
  } =>
    typeof data === 'object' &&
    data !== null &&
    'name' in data &&
    typeof (data as Record<string, unknown>).name === 'string' &&
    'radio' in data &&
    typeof (data as Record<string, unknown>).radio === 'object' &&
    (data as Record<string, unknown>).radio !== null &&
    typeof (data as Record<string, Record<string, unknown>>).radio.freq === 'number' &&
    typeof (data as Record<string, Record<string, unknown>>).radio.bw === 'number' &&
    typeof (data as Record<string, Record<string, unknown>>).radio.sf === 'number' &&
    typeof (data as Record<string, Record<string, unknown>>).radio.cr === 'number';

  const populateFormFromImport = (data: Record<string, unknown>) => {
    const radio = data.radio as { freq: number; bw: number; sf: number; cr: number };
    setName(data.name as string);
    if (typeof data.lat === 'number') setLat(String(data.lat));
    if (typeof data.lon === 'number') setLon(String(data.lon));
    if (typeof data.tx_power === 'number') setTxPower(String(data.tx_power));
    setFreq(String(radio.freq));
    setBw(String(radio.bw));
    setSf(String(radio.sf));
    setCr(String(radio.cr));
    if (typeof data.path_hash_mode === 'number') setPathHashMode(String(data.path_hash_mode));
    if (data.advert_location_source === 'off' || data.advert_location_source === 'current')
      setAdvertLocationSource(data.advert_location_source);
    if (typeof data.multi_acks_enabled === 'boolean') setMultiAcksEnabled(data.multi_acks_enabled);
    if (typeof data.repeat_enabled === 'boolean') setRepeatEnabled(data.repeat_enabled);
    if (typeof data.telemetry_mode_base === 'number')
      setTelemetryModeBase(data.telemetry_mode_base);
    if (typeof data.telemetry_mode_loc === 'number') setTelemetryModeLoc(data.telemetry_mode_loc);
    if (typeof data.telemetry_mode_env === 'number') setTelemetryModeEnv(data.telemetry_mode_env);
  };

  const buildUpdateFromImport = (data: Record<string, unknown>): RadioConfigUpdate => {
    const radio = data.radio as { freq: number; bw: number; sf: number; cr: number };
    const update: RadioConfigUpdate = {
      name: data.name as string,
      lat: typeof data.lat === 'number' ? data.lat : config.lat,
      lon: typeof data.lon === 'number' ? data.lon : config.lon,
      tx_power: typeof data.tx_power === 'number' ? (data.tx_power as number) : config.tx_power,
      radio,
    };
    if (data.advert_location_source === 'off' || data.advert_location_source === 'current')
      update.advert_location_source = data.advert_location_source;
    if (typeof data.multi_acks_enabled === 'boolean')
      update.multi_acks_enabled = data.multi_acks_enabled;
    if (typeof data.telemetry_mode_base === 'number')
      update.telemetry_mode_base = data.telemetry_mode_base as number;
    if (typeof data.telemetry_mode_loc === 'number')
      update.telemetry_mode_loc = data.telemetry_mode_loc as number;
    if (typeof data.telemetry_mode_env === 'number')
      update.telemetry_mode_env = data.telemetry_mode_env as number;
    if (config.path_hash_mode_supported && typeof data.path_hash_mode === 'number')
      update.path_hash_mode = data.path_hash_mode as number;
    if (config.repeat_supported && typeof data.repeat_enabled === 'boolean')
      update.repeat_enabled = data.repeat_enabled;
    return update;
  };

  const applyImport = async (data: Record<string, unknown>) => {
    populateFormFromImport(data);
    const update = buildUpdateFromImport(data);

    setBusy(true);
    setRebooting(true);
    try {
      if (typeof data.private_key === 'string' && data.private_key) {
        await onSetPrivateKey(data.private_key);
        toast.success('Config + private key imported, saving & rebooting...');
      } else {
        toast.success('Config imported, saving & rebooting...');
      }
      await onSave(update);
      await onReboot();
      if (!pageMode) onClose();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to import config');
    } finally {
      setRebooting(false);
      setBusy(false);
    }
  };

  const handleImportConfig = async (file: File) => {
    try {
      const text = await file.text();
      const data = JSON.parse(text);

      if (!validateImportData(data)) {
        toast.error('Invalid config file', {
          description: 'File must contain name and radio parameters (freq, bw, sf, cr)',
        });
        return;
      }

      if (typeof data.private_key === 'string' && data.private_key) {
        // Private key present — show warning dialog before applying
        pendingImportRef.current = data;
        setKeyImportDialogOpen(true);
      } else {
        await applyImport(data);
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to import config');
    } finally {
      if (importInputRef.current) importInputRef.current.value = '';
    }
  };

  const handleConfirmKeyImport = async () => {
    setKeyImportDialogOpen(false);
    const data = pendingImportRef.current;
    pendingImportRef.current = null;
    if (data) await applyImport(data);
  };

  const radioState =
    health?.radio_state ?? (health?.radio_initializing ? 'initializing' : 'disconnected');
  const connectionActionLabel =
    radioState === 'paused'
      ? 'Reconnect'
      : radioState === 'connected' || radioState === 'initializing'
        ? 'Disconnect'
        : 'Stop Trying';

  const connectionStatusLabel =
    radioState === 'connected'
      ? health?.connection_info || 'Connected'
      : radioState === 'initializing'
        ? `Initializing ${health?.connection_info || 'radio'}`
        : radioState === 'connecting'
          ? `Attempting to connect${health?.connection_info ? ` to ${health.connection_info}` : ''}`
          : radioState === 'paused'
            ? `Connection paused${health?.connection_info ? ` (${health.connection_info})` : ''}`
            : 'Not connected';

  const deviceInfoLabel = useMemo(() => {
    const info = health?.radio_device_info;
    if (!info) {
      return null;
    }

    const model = info.model?.trim() || null;
    const firmwareParts = [info.firmware_build?.trim(), info.firmware_version?.trim()].filter(
      (value): value is string => Boolean(value)
    );
    const capacityParts = [
      typeof info.max_contacts === 'number' ? `${info.max_contacts} contacts` : null,
      typeof info.max_channels === 'number' ? `${info.max_channels} channels` : null,
    ].filter((value): value is string => value !== null);

    if (!model && firmwareParts.length === 0 && capacityParts.length === 0) {
      return null;
    }

    let label = model ?? 'Radio';
    if (firmwareParts.length > 0) {
      label += ` running ${firmwareParts.join('/')}`;
    }
    if (capacityParts.length > 0) {
      label += ` (max: ${capacityParts.join(', ')})`;
    }
    return label;
  }, [health?.radio_device_info]);

  const handleConnectionAction = async () => {
    setConnectionBusy(true);
    try {
      if (radioState === 'paused') {
        await onReconnect();
        toast.success('Reconnect requested');
      } else {
        await onDisconnect();
        toast.success('Radio connection paused');
      }
    } catch (err) {
      toast.error('Failed to change radio connection state', {
        description: err instanceof Error ? err.message : 'Check radio connection and try again',
      });
    } finally {
      setConnectionBusy(false);
    }
  };

  return (
    <div className={className}>
      {/* ── Connection ── */}
      <div className="space-y-3">
        <h3 className="text-base font-semibold tracking-tight">Connection</h3>
        <div className="flex items-center gap-2">
          <div
            className={`w-2 h-2 rounded-full ${
              radioState === 'connected'
                ? 'bg-status-connected'
                : radioState === 'initializing' || radioState === 'connecting'
                  ? 'bg-warning'
                  : 'bg-status-disconnected'
            }`}
          />
          <span
            className={
              radioState === 'paused' || radioState === 'disconnected'
                ? 'text-muted-foreground'
                : ''
            }
          >
            {connectionStatusLabel}
          </span>
        </div>
        {deviceInfoLabel && <p className="text-sm text-muted-foreground">{deviceInfoLabel}</p>}

        {health?.radio_stats && <RadioDetailsCollapsible stats={health.radio_stats} />}

        <Button
          type="button"
          variant="outline"
          onClick={handleConnectionAction}
          disabled={connectionBusy}
          className="w-full"
        >
          {connectionBusy ? `${connectionActionLabel}...` : connectionActionLabel}
        </Button>
        <p className="text-[0.8125rem] text-muted-foreground">
          Disconnect pauses automatic reconnect attempts so another device can use the radio.
        </p>
      </div>

      <Separator />

      {/* ── Identity ── */}
      <div className="space-y-2">
        <h3 className="text-base font-semibold tracking-tight">Identity</h3>
      </div>

      <div className="space-y-2">
        <Label htmlFor="name">Radio Name</Label>
        <Input id="name" value={name} onChange={(e) => setName(e.target.value)} />
      </div>

      <div className="space-y-2">
        <Label htmlFor="public-key">Public Key</Label>
        <Input id="public-key" value={config.public_key} disabled className="font-mono text-xs" />
      </div>

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
          disabled={identityBusy || identityRebooting || !privateKey.trim()}
          className="w-full border-destructive/50 text-destructive hover:bg-destructive/10"
          variant="outline"
        >
          {identityBusy || identityRebooting
            ? 'Setting & Rebooting...'
            : 'Set Private Key & Reboot'}
        </Button>
      </div>

      {identityError && (
        <div className="text-sm text-destructive" role="alert">
          {identityError}
        </div>
      )}

      <Separator />

      {/* ── Radio Parameters ── */}
      <div className="space-y-2">
        <h3 className="text-base font-semibold tracking-tight">Radio Parameters</h3>
      </div>

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

      {config.path_hash_mode_supported && (
        <div className="space-y-2">
          <Label htmlFor="path-hash-mode">Path Hash Mode</Label>
          <select
            id="path-hash-mode"
            value={pathHashMode}
            onChange={(e) => setPathHashMode(e.target.value)}
            className="w-full h-10 px-3 rounded-md border border-input bg-background text-sm ring-offset-background focus:outline-none focus:ring-2 focus:ring-ring focus:ring-offset-2"
          >
            <option value="0">1 byte — up to 63 hops (default)</option>
            <option value="1">2 bytes — up to 32 hops</option>
            <option value="2">3 bytes — up to 21 hops</option>
          </select>
          <div className="rounded-md border border-warning/50 bg-warning/10 p-3 text-xs text-warning">
            <p className="font-semibold mb-1">Compatibility Warning</p>
            <p>
              ALL nodes along a message&apos;s route &mdash; your radio, every repeater, and the
              recipient &mdash; must be running firmware that supports the selected mode. Messages
              sent with 2-byte or 3-byte hops will be dropped by any node on older firmware.
            </p>
          </div>
        </div>
      )}

      {config.repeat_supported && (
        <div className="space-y-2">
          <div className="flex items-start gap-3 rounded-md border border-border/60 p-3">
            <Checkbox
              id="repeat-enabled"
              checked={repeatEnabled}
              onCheckedChange={(checked) => setRepeatEnabled(checked === true)}
              disabled={!repeatEnabled && !repeatFreqAllowed}
              className="mt-0.5"
            />
            <div className="space-y-1">
              <Label htmlFor="repeat-enabled">Repeater Mode</Label>
              <p className="text-[0.8125rem] text-muted-foreground">
                When enabled, this node rebroadcasts other nodes&apos; packets. Firmware only allows
                it on specific frequencies and rejects the change otherwise.
              </p>
            </div>
          </div>
          {allowedRepeatFreqs.length > 0 && (
            <div className="rounded-md border border-warning/50 bg-warning/10 p-3 text-xs text-warning">
              <p className="font-semibold mb-1">Compatibility Warning</p>
              <p>
                Repeat mode only works on the frequencies this node&apos;s firmware was built to
                allow: {allowedRepeatFreqs.map(formatFreqRange).join(', ')} MHz. This list is
                compiled into the firmware and can&apos;t be changed from RemoteTerm.
              </p>
            </div>
          )}
        </div>
      )}

      <Separator />

      {/* ── Location ── */}
      <div className="space-y-2">
        <div className="flex items-center justify-between">
          <h3 className="text-base font-semibold tracking-tight">Location</h3>
          <Button
            type="button"
            variant="outline"
            size="sm"
            onClick={handleGetLocation}
            disabled={gettingLocation}
          >
            {gettingLocation ? (
              'Getting...'
            ) : (
              <>
                <MapPinned className="mr-1.5 h-4 w-4" aria-hidden="true" />
                Use My Location
              </>
            )}
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
        <div className="space-y-2">
          <Label htmlFor="advert-location-source">Advert Location Source</Label>
          <select
            id="advert-location-source"
            value={advertLocationSource}
            onChange={(e) => setAdvertLocationSource(e.target.value as 'off' | 'current')}
            className="w-full h-10 px-3 rounded-md border border-input bg-background text-sm ring-offset-background focus:outline-none focus:ring-2 focus:ring-ring focus:ring-offset-2"
          >
            <option value="off">Off</option>
            <option value="current">Include Node Location</option>
          </select>
          <p className="text-[0.8125rem] text-muted-foreground">
            Companion-radio firmware does not distinguish between saved coordinates and live GPS
            here. When enabled, adverts include the node&apos;s current location state. That may be
            the last coordinates you set from RemoteTerm or live GPS coordinates if the node itself
            is already updating them. RemoteTerm cannot enable GPS on the node through the interface
            library.
          </p>
        </div>
      </div>

      <Separator />

      {/* ── Telemetry Sharing ── */}
      <div className="space-y-3">
        <h3 className="text-base font-semibold tracking-tight">Telemetry Sharing</h3>
        <p className="text-[0.8125rem] text-muted-foreground">
          Controls what this radio shares when other nodes request its telemetry. &ldquo;Deny&rdquo;
          blocks all requests, &ldquo;Per-Contact&rdquo; uses per-contact permission flags on the
          radio, and &ldquo;Allow All&rdquo; shares with any requester.
        </p>

        <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
          <div className="space-y-1.5">
            <Label htmlFor="telemetry-mode-base" className="text-sm">
              Battery &amp; Base
            </Label>
            <select
              id="telemetry-mode-base"
              value={telemetryModeBase}
              onChange={(e) => setTelemetryModeBase(Number(e.target.value))}
              className="w-full h-9 px-3 rounded-md border border-input bg-background text-sm ring-offset-background focus:outline-none focus:ring-2 focus:ring-ring focus:ring-offset-2"
            >
              <option value={0}>Deny</option>
              <option value={1}>Per-Contact</option>
              <option value={2}>Allow All</option>
            </select>
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="telemetry-mode-loc" className="text-sm">
              Location
            </Label>
            <select
              id="telemetry-mode-loc"
              value={telemetryModeLoc}
              onChange={(e) => setTelemetryModeLoc(Number(e.target.value))}
              className="w-full h-9 px-3 rounded-md border border-input bg-background text-sm ring-offset-background focus:outline-none focus:ring-2 focus:ring-ring focus:ring-offset-2"
            >
              <option value={0}>Deny</option>
              <option value={1}>Per-Contact</option>
              <option value={2}>Allow All</option>
            </select>
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="telemetry-mode-env" className="text-sm">
              Environment Sensors
            </Label>
            <select
              id="telemetry-mode-env"
              value={telemetryModeEnv}
              onChange={(e) => setTelemetryModeEnv(Number(e.target.value))}
              className="w-full h-9 px-3 rounded-md border border-input bg-background text-sm ring-offset-background focus:outline-none focus:ring-2 focus:ring-ring focus:ring-offset-2"
            >
              <option value={0}>Deny</option>
              <option value={1}>Per-Contact</option>
              <option value={2}>Allow All</option>
            </select>
          </div>
        </div>
      </div>

      {error && (
        <div className="text-sm text-destructive" role="alert">
          {error}
        </div>
      )}

      <div className="flex gap-2">
        <Button
          onClick={handleSave}
          disabled={busy || rebooting}
          variant="outline"
          className="flex-1"
        >
          {busy && !rebooting ? 'Saving...' : 'Save Radio Config'}
        </Button>
        <Button onClick={handleSaveAndReboot} disabled={busy || rebooting} className="flex-1">
          {rebooting ? 'Rebooting...' : 'Save Radio Config & Reboot'}
        </Button>
      </div>
      <p className="text-[0.8125rem] text-muted-foreground">
        Some settings may require a reboot to take effect on some radios.
      </p>

      <div className="flex gap-2">
        <Button variant="outline" size="sm" onClick={handleExportConfig} className="flex-1">
          <Download className="mr-1.5 h-4 w-4" aria-hidden="true" />
          Export Config
        </Button>
        <Button
          variant="outline"
          size="sm"
          onClick={() => importInputRef.current?.click()}
          disabled={busy || rebooting}
          className="flex-1"
        >
          <Upload className="mr-1.5 h-4 w-4" aria-hidden="true" />
          Import &amp; Reboot
        </Button>
        <input
          ref={importInputRef}
          type="file"
          accept=".json"
          className="hidden"
          onChange={(e) => {
            const file = e.target.files?.[0];
            if (file) handleImportConfig(file);
          }}
        />
      </div>
      <p className="text-[0.8125rem] text-muted-foreground">
        Export saves the current server config to a JSON file. Import loads a config file, applies
        it, and reboots the radio.
      </p>

      <Separator />

      {/* ── Messaging ── */}
      <div className="space-y-2">
        <h3 className="text-base font-semibold tracking-tight">Messaging</h3>
      </div>

      <div className="space-y-2">
        <div className="flex items-start gap-3 rounded-md border border-border/60 p-3">
          <Checkbox
            id="multi-acks-enabled"
            checked={multiAcksEnabled}
            onCheckedChange={(checked) => setMultiAcksEnabled(checked === true)}
            className="mt-0.5"
          />
          <div className="space-y-1">
            <Label htmlFor="multi-acks-enabled">Extra Direct ACK Transmission</Label>
            <p className="text-[0.8125rem] text-muted-foreground">
              When enabled, the radio sends one extra direct ACK transmission before the normal ACK
              for received direct messages. This is a firmware-level receive behavior, not a
              RemoteTerm retry setting.
            </p>
          </div>
        </div>

        <div className="flex items-start gap-3 rounded-md border border-border/60 p-3">
          <Checkbox
            id="auto-resend-channel"
            checked={appSettings.auto_resend_channel}
            onCheckedChange={(checked) =>
              onSaveAppSettings({ auto_resend_channel: checked === true })
            }
            className="mt-0.5"
          />
          <div className="space-y-1">
            <Label htmlFor="auto-resend-channel">Auto-Resend Unheard Channel Messages</Label>
            <p className="text-[0.8125rem] text-muted-foreground">
              When enabled, outgoing channel messages that receive no echo within 2 seconds are
              automatically resent once (byte-perfect, within the 30-second dedup window). Repeaters
              that already heard the original will ignore the duplicate. This functionality will NOT
              create double-sent/duplicate messages.
            </p>
          </div>
        </div>
      </div>

      <div className="space-y-2">
        <Label htmlFor="flood-scope">Flood Scope / Region</Label>
        <Input
          id="flood-scope"
          value={floodScope}
          onChange={(e) => setFloodScope(e.target.value)}
          placeholder="MyRegion"
        />
        <p className="text-[0.8125rem] text-muted-foreground">
          Tag outgoing messages with a region name (e.g. MyRegion). Repeaters configured for that
          region can forward the traffic, while repeaters configured to deny other regions may drop
          it. Leave empty to send unscoped (plain flood). Forcing unscoped when a default scope is
          configured on the radio requires firmware v12 or newer. Individual channels can override
          this with their own region or an "always unscoped" setting.
        </p>
      </div>

      <div className="space-y-2">
        <Label htmlFor="known-regions">Known Regions (for decoding)</Label>
        <textarea
          id="known-regions"
          value={knownRegions}
          onChange={(e) => setKnownRegions(e.target.value)}
          rows={4}
          placeholder={'nl-gr\nde-by\nMyRegion'}
          spellCheck={false}
          className="flex w-full rounded-md border border-input bg-background px-3 py-2 text-sm font-mono ring-offset-background placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 disabled:cursor-not-allowed disabled:opacity-50"
        />
        <p className="text-[0.8125rem] text-muted-foreground">
          One region name per line. Incoming region-scoped (TransportFlood/TransportDirect) packets
          are matched against this list so messages and the packet inspector show a readable region
          label instead of a raw transport code. Saving a change re-tags existing messages whose
          original packet is still stored.
        </p>

        <div className="space-y-2 rounded-md border border-input bg-muted/20 p-3">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <span className="text-[0.625rem] uppercase tracking-wider text-muted-foreground font-medium">
              Discover regions from repeaters
            </span>
            <Button
              type="button"
              variant="outline"
              size="sm"
              onClick={handleDiscoverRegions}
              disabled={regionDiscoveryLoading || !health?.radio_connected}
            >
              {regionDiscoveryLoading ? 'Asking repeaters...' : 'Discover Regions'}
            </Button>
          </div>
          <p className="text-[0.8125rem] text-muted-foreground">
            Asks nearby repeaters which regions they flood, so you can populate the list above. Uses
            repeaters from your last mesh discovery sweep when available, otherwise your most
            recently seen repeaters. Only repeaters in direct range answer, and this only reveals
            flood-allowed regions (not blocked ones).
          </p>
          {!health?.radio_connected && (
            <p className="text-sm text-destructive">Radio not connected</p>
          )}
          {regionDiscovery && (
            <div className="space-y-2">
              <p className="text-sm font-medium">
                {regionDiscovery.repeaters_answered}/{regionDiscovery.repeaters_queried} repeater
                {regionDiscovery.repeaters_queried === 1 ? '' : 's'} answered
                {regionDiscovery.regions.length > 0
                  ? ` — ${regionDiscovery.regions.length} region${regionDiscovery.regions.length === 1 ? '' : 's'} found`
                  : ''}
              </p>
              {regionDiscovery.regions.length > 0 ? (
                <>
                  <div className="flex flex-wrap gap-1.5">
                    {regionDiscovery.regions.map((region) => (
                      <span
                        key={region}
                        className="text-[0.625rem] uppercase tracking-wider px-1.5 py-0.5 rounded bg-primary/10 font-mono"
                      >
                        {region}
                      </span>
                    ))}
                  </div>
                  <Button
                    type="button"
                    variant="outline"
                    size="sm"
                    onClick={handleAddDiscoveredRegions}
                    className="border-success/50 text-success hover:bg-success/10"
                  >
                    Add to Known Regions
                  </Button>
                </>
              ) : (
                regionDiscovery.repeaters_queried > 0 && (
                  <p className="text-sm text-muted-foreground">
                    No flood-allowed regions were reported.
                  </p>
                )
              )}
            </div>
          )}
        </div>
      </div>

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
        <p className="text-[0.8125rem] text-muted-foreground">
          Configured radio contact capacity. Favorites reload first, then background maintenance
          refills to about 80% of this value and offloads once occupancy reaches about 95%.
        </p>
        {health?.radio_device_info?.max_contacts != null &&
          Number(maxRadioContacts) > health.radio_device_info.max_contacts && (
            <p className="text-xs text-warning">
              Your radio reports a hardware limit of {health.radio_device_info.max_contacts}{' '}
              contacts. The effective cap will be limited to what the radio supports.
            </p>
          )}
      </div>

      {floodError && (
        <div className="text-sm text-destructive" role="alert">
          {floodError}
        </div>
      )}

      <Button onClick={handleSaveFloodSettings} disabled={floodBusy} className="w-full">
        {floodBusy ? 'Saving...' : 'Save Messaging Settings'}
      </Button>

      <Separator />

      {/* ── Advertising & Discovery ── */}
      <div className="space-y-5">
        <h3 className="text-base font-semibold tracking-tight">Advertising &amp; Discovery</h3>

        <div className="space-y-2">
          <Label htmlFor="advert-interval">Periodic Advertising Interval</Label>
          <div className="flex items-center gap-2">
            <Input
              id="advert-interval"
              type="number"
              min="0"
              value={advertIntervalHours}
              onChange={(e) => setAdvertIntervalHours(e.target.value)}
              className="w-28"
            />
            <span className="text-sm text-muted-foreground">hours (0 = off)</span>
          </div>
          <p className="text-[0.8125rem] text-muted-foreground">
            How often to automatically advertise presence. Set to 0 to disable. Minimum: 1 hour.
            Recommended: 24 hours or higher.
          </p>
          {advertIntervalError && (
            <div className="text-sm text-destructive" role="alert">
              {advertIntervalError}
            </div>
          )}
          <Button
            onClick={handleSaveAdvertInterval}
            disabled={advertIntervalBusy}
            className="w-full"
          >
            {advertIntervalBusy ? 'Saving...' : 'Save Advertising Interval'}
          </Button>
        </div>

        <div className="space-y-2">
          <h4 className="text-sm font-semibold">Send Advertisement</h4>
          <p className="text-[0.8125rem] text-muted-foreground">
            Flood adverts propagate through repeaters. Zero-hop adverts are local-only and use less
            airtime.
          </p>
          <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
            <Button
              onClick={() => handleAdvertise('flood')}
              disabled={advertisingMode !== null || !health?.radio_connected}
              className="w-full bg-warning hover:bg-warning/90 text-warning-foreground"
            >
              {advertisingMode === 'flood' ? 'Sending...' : 'Send Flood Advertisement'}
            </Button>
            <Button
              onClick={() => handleAdvertise('zero_hop')}
              disabled={advertisingMode !== null || !health?.radio_connected}
              className="w-full"
            >
              {advertisingMode === 'zero_hop' ? 'Sending...' : 'Send Zero-Hop Advertisement'}
            </Button>
          </div>
          {!health?.radio_connected && (
            <p className="text-sm text-destructive">Radio not connected</p>
          )}
        </div>

        <div className="space-y-3">
          <h4 className="text-sm font-semibold">Mesh Discovery</h4>
          <p className="text-[0.8125rem] text-muted-foreground">
            Discover nearby node types that currently respond to mesh discovery requests: repeaters
            and sensors.
          </p>
          <div className="grid grid-cols-1 gap-2 sm:grid-cols-3">
            {[
              { target: 'repeaters', label: 'Discover Repeaters' },
              { target: 'sensors', label: 'Discover Sensors' },
              { target: 'all', label: 'Discover Both' },
            ].map(({ target, label }) => (
              <Button
                key={target}
                type="button"
                variant="outline"
                onClick={() => handleDiscover(target as RadioDiscoveryTarget)}
                disabled={meshDiscoveryLoadingTarget !== null || !health?.radio_connected}
                className="w-full"
              >
                {meshDiscoveryLoadingTarget === target ? 'Listening...' : label}
              </Button>
            ))}
          </div>
          {!health?.radio_connected && (
            <p className="text-sm text-destructive">Radio not connected</p>
          )}
          {discoverError && (
            <p className="text-sm text-destructive" role="alert">
              {discoverError}
            </p>
          )}
          {meshDiscovery && (
            <div className="space-y-2 rounded-md border border-input bg-muted/20 p-3">
              <div className="flex items-center justify-between gap-4">
                <p className="text-sm font-medium">
                  Last sweep: {meshDiscovery.results.length} node
                  {meshDiscovery.results.length === 1 ? '' : 's'}
                </p>
                <p className="text-xs text-muted-foreground">
                  {meshDiscovery.duration_seconds.toFixed(0)}s listen window
                </p>
              </div>
              {meshDiscovery.results.length === 0 ? (
                <p className="text-sm text-muted-foreground">
                  No supported nodes responded during the last discovery sweep.
                </p>
              ) : (
                <div className="space-y-2">
                  {meshDiscovery.results.map((result) => (
                    <div
                      key={result.public_key}
                      className="rounded-md border border-input bg-background px-3 py-2"
                    >
                      <div className="flex items-center justify-between gap-3">
                        <span className="text-sm font-medium">
                          {result.name ?? <span className="capitalize">{result.node_type}</span>}
                        </span>
                        <span className="text-xs text-muted-foreground">
                          heard {result.heard_count} time{result.heard_count === 1 ? '' : 's'}
                        </span>
                      </div>
                      {result.name && (
                        <p className="text-xs capitalize text-muted-foreground">
                          {result.node_type}
                        </p>
                      )}
                      <p className="mt-1 break-all font-mono text-xs text-muted-foreground">
                        {result.public_key}
                      </p>
                      <p className="mt-1 text-xs text-muted-foreground">
                        Heard here: {result.local_snr ?? 'n/a'} dB SNR /{' '}
                        {result.local_rssi ?? 'n/a'} dBm RSSI. Remote heard us:{' '}
                        {result.remote_snr ?? 'n/a'} dB SNR.
                      </p>
                    </div>
                  ))}
                </div>
              )}
            </div>
          )}
        </div>
      </div>

      {/* ── Private Key Import Warning ── */}
      <Dialog
        open={keyImportDialogOpen}
        onOpenChange={(open) => {
          setKeyImportDialogOpen(open);
          if (!open) pendingImportRef.current = null;
        }}
      >
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Import includes Private Key</DialogTitle>
            <DialogDescription>
              This config file contains a private key. Importing it will change your radio&apos;s
              identity &mdash; your radio will have a new public key and other nodes will see it as
              a different device. This cannot be undone without the original key.
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button
              variant="outline"
              onClick={() => {
                setKeyImportDialogOpen(false);
                pendingImportRef.current = null;
              }}
            >
              Cancel
            </Button>
            <Button
              onClick={handleConfirmKeyImport}
              className="border-destructive/50 text-destructive hover:bg-destructive/10"
              variant="outline"
            >
              Import Config &amp; Key
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
