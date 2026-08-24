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
import type { Contact } from '../types';

interface ContactMcmpSettingsModalProps {
  open: boolean;
  onClose: () => void;
  contact: Contact;
  onSave: (mcmpEnabled: boolean) => Promise<void>;
}

export function ContactMcmpSettingsModal({
  open,
  onClose,
  contact,
  onSave,
}: ContactMcmpSettingsModalProps) {
  const [mcmpEnabled, setMcmpEnabled] = useState<boolean>(contact.mcmp_enabled ?? false);
  const [saving, setSaving] = useState(false);

  useState(() => {
    setMcmpEnabled(contact.mcmp_enabled ?? false);
  });

  const handleSave = async () => {
    setSaving(true);
    try {
      await onSave(mcmpEnabled);
      onClose();
    } catch (err) {
      toast.error('Failed to save MCMP setting', {
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
          <DialogTitle>MCMP Compression for Direct Messages</DialogTitle>
          <DialogDescription>
            Enable MCMP v3 compression for direct messages with this contact. Incoming compressed
            messages are always decoded automatically.
          </DialogDescription>
        </DialogHeader>
        <div className="py-4">
          <label className="flex items-start gap-3">
            <Checkbox
              checked={mcmpEnabled}
              onCheckedChange={(checked) => setMcmpEnabled(checked === true)}
            />
            <div>
              <p className="text-sm font-medium">Enable MCMP Compression</p>
              <p className="text-xs text-muted-foreground">
                Compress outgoing direct messages. Signatures are not used for DMs.
              </p>
            </div>
          </label>
          <div className="mt-4 text-xs text-muted-foreground">
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