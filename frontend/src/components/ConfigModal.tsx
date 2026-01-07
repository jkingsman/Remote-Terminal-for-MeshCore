import { useState, useEffect } from 'react';
import type { AppSettings, AppSettingsUpdate, RadioConfig, RadioConfigUpdate } from '../types';
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogFooter,
} from './ui/dialog';
import { Input } from './ui/input';
import { Label } from './ui/label';
import { Button } from './ui/button';
import { Separator } from './ui/separator';
import { Alert, AlertDescription } from './ui/alert';

interface ConfigModalProps {
  open: boolean;
  config: RadioConfig | null;
  appSettings: AppSettings | null;
  onClose: () => void;
  onSave: (update: RadioConfigUpdate) => Promise<void>;
  onSaveAppSettings: (update: AppSettingsUpdate) => Promise<void>;
  onSetPrivateKey: (key: string) => Promise<void>;
  onReboot: () => Promise<void>;
}

export function ConfigModal({
  open,
  config,
  appSettings,
  onClose,
  onSave,
  onSaveAppSettings,
  onSetPrivateKey,
  onReboot,
}: ConfigModalProps) {
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
  const [loading, setLoading] = useState(false);
  const [rebooting, setRebooting] = useState(false);
  const [error, setError] = useState('');

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
    }
  }, [appSettings]);

  const handleSave = async () => {
    setError('');
    setLoading(true);

    try {
      const update: RadioConfigUpdate = {
        name,
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

      const newMaxRadioContacts = parseInt(maxRadioContacts, 10);
      if (!isNaN(newMaxRadioContacts) && newMaxRadioContacts !== appSettings?.max_radio_contacts) {
        await onSaveAppSettings({ max_radio_contacts: newMaxRadioContacts });
      }

      onClose();
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
      onClose();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to set private key');
    } finally {
      setLoading(false);
    }
  };

  const handleReboot = async () => {
    if (!confirm('Are you sure you want to reboot the radio? The connection will drop temporarily.')) {
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

  return (
    <Dialog open={open} onOpenChange={(isOpen) => !isOpen && onClose()}>
      <DialogContent className="sm:max-w-[500px] max-h-[90vh] overflow-y-auto">
        <DialogHeader>
          <DialogTitle>Radio Configuration</DialogTitle>
        </DialogHeader>

        {!config ? (
          <div className="py-8 text-center text-muted-foreground">
            Loading configuration...
          </div>
        ) : (
          <div className="space-y-4">
            <div className="space-y-2">
              <Label htmlFor="public-key">Public Key</Label>
              <Input id="public-key" value={config.public_key} disabled />
            </div>

            <div className="space-y-2">
              <Label htmlFor="name">Name</Label>
              <Input
                id="name"
                value={name}
                onChange={(e) => setName(e.target.value)}
              />
            </div>

            <div className="grid grid-cols-2 gap-4">
              <div className="space-y-2">
                <Label htmlFor="lat">Latitude</Label>
                <Input
                  id="lat"
                  type="number"
                  step="any"
                  value={lat}
                  onChange={(e) => setLat(e.target.value)}
                />
              </div>
              <div className="space-y-2">
                <Label htmlFor="lon">Longitude</Label>
                <Input
                  id="lon"
                  type="number"
                  step="any"
                  value={lon}
                  onChange={(e) => setLon(e.target.value)}
                />
              </div>
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
                  min="1"
                  max="4"
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

            <Separator className="my-4" />

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

            <Separator className="my-4" />

            <div className="space-y-2">
              <Label htmlFor="private-key">Set Private Key (write-only)</Label>
              <div className="flex gap-2">
                <Input
                  id="private-key"
                  type="password"
                  value={privateKey}
                  onChange={(e) => setPrivateKey(e.target.value)}
                  placeholder="64-character hex private key"
                  className="flex-1"
                />
                <Button
                  onClick={handleSetPrivateKey}
                  disabled={loading || !privateKey.trim()}
                >
                  Set
                </Button>
              </div>
            </div>

            <Separator className="my-4" />

            <div className="space-y-3">
              <Label>Reboot Radio</Label>
              <Alert variant="warning">
                <AlertDescription>
                  Some configuration changes (like name) require a radio reboot to take effect.
                  The connection will temporarily drop and automatically reconnect.
                </AlertDescription>
              </Alert>
              <Button
                variant="outline"
                onClick={handleReboot}
                disabled={rebooting || loading}
                className="border-yellow-500/50 text-yellow-200 hover:bg-yellow-500/10"
              >
                {rebooting ? 'Rebooting...' : 'Reboot Radio'}
              </Button>
            </div>

            {error && (
              <div className="text-sm text-destructive">{error}</div>
            )}
          </div>
        )}

        <DialogFooter>
          <Button variant="outline" onClick={onClose}>
            Cancel
          </Button>
          <Button onClick={handleSave} disabled={loading || !config}>
            {loading ? 'Saving...' : 'Save Config'}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
