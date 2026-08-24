import { useState } from 'react';
import { Checkbox } from './ui/checkbox';
import { Button } from './ui/button';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from './ui/dialog';
import { toast } from './ui/sonner';
import type { Channel } from '../types';

interface ChannelMcmpSettingsModalProps {
  open: boolean;
  onClose: () => void;
  channel: Channel;
  onSave: (mcmpEnabled: boolean, mcmpSignEnabled: boolean) => Promise<void>;
}

export function ChannelMcmpSettingsModal({
  open,
  onClose,
  channel,
  onSave,
}: ChannelMcmpSettingsModalProps) {
  const [mcmpEnabled, setMcmpEnabled] = useState(channel.mcmp_enabled);
  const [mcmpSignEnabled, setMcmpSignEnabled] = useState(channel.mcmp_sign_enabled);
  const [saving, setSaving] = useState(false);

  // Reset when opening or channel changes
  useState(() => {
    setMcmpEnabled(channel.mcmp_enabled);
    setMcmpSignEnabled(channel.mcmp_sign_enabled);
  });

  const handleSave = async () => {
    setSaving(true);
    try {
      await onSave(mcmpEnabled, mcmpSignEnabled);
      onClose();
    } catch (err) {
      toast.error('Failed to save MCMP settings', {
        description: err instanceof Error ? err.message : 'Unknown error',
      });
    } finally {
      setSaving(false);
    }
  };

  return (
    <Dialog open={open} onOpenChange={(open) => !open && onClose()}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>MCMP Compression Settings</DialogTitle>
          <DialogDescription>
            Mesh-Compressor (MCMP) v3 compresses text for LoRa. Signed messages carry an Ed25519
            signature verified by receiving clients.
          </DialogDescription>
        </DialogHeader>
        <div className="space-y-4 py-4">
          <label className="flex items-start gap-3">
            <Checkbox
              checked={mcmpEnabled}
              onCheckedChange={(checked) => {
                const enabled = checked === true;
                setMcmpEnabled(enabled);
                if (!enabled) setMcmpSignEnabled(false);
              }}
            />
            <div>
              <p className="text-sm font-medium">Enable MCMP Compression</p>
              <p className="text-xs text-muted-foreground">
                Compress outgoing messages for this channel. Received MCMP messages are always
                decoded automatically.
              </p>
            </div>
          </label>
          <label className={`flex items-start gap-3 ${!mcmpEnabled ? 'opacity-50' : ''}`}>
            <Checkbox
              checked={mcmpSignEnabled}
              disabled={!mcmpEnabled}
              onCheckedChange={(checked) => setMcmpSignEnabled(checked === true)}
            />
            <div>
              <p className="text-sm font-medium">Sign outgoing messages</p>
              <p className="text-xs text-muted-foreground">
                Adds an Ed25519 signature to MCMP messages. Only available when compression is on.
              </p>
            </div>
          </label>
          <div className="text-xs text-muted-foreground">
            <a
              href="https://github.com/HDDen/meshcore-open/tree/rename-mco-advanced"
              target="_blank"
              rel="noopener noreferrer"
              className="text-primary hover:underline"
            >
              Learn more about MCMP
            </a>
          </div>
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={onClose}>
            Cancel
          </Button>
          <Button onClick={handleSave} disabled={saving}>
            {saving ? 'Saving...' : 'Save'}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}