import { useState, useRef } from 'react';
import { Dice5 } from 'lucide-react';
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
  DialogFooter,
} from './ui/dialog';
import { Tabs, TabsList, TabsTrigger, TabsContent } from './ui/tabs';
import { Input } from './ui/input';
import { Label } from './ui/label';
import { Checkbox } from './ui/checkbox';
import { Button } from './ui/button';
import { toast } from './ui/sonner';

type Tab = 'new-contact' | 'new-room' | 'hashtag';

interface NewMessageModalProps {
  open: boolean;
  undecryptedCount: number;
  onClose: () => void;
  onCreateContact: (name: string, publicKey: string, tryHistorical: boolean) => Promise<void>;
  onCreateChannel: (name: string, key: string, tryHistorical: boolean) => Promise<void>;
  onCreateHashtagChannel: (name: string, tryHistorical: boolean) => Promise<void>;
}

export function NewMessageModal({
  open,
  undecryptedCount,
  onClose,
  onCreateContact,
  onCreateChannel,
  onCreateHashtagChannel,
}: NewMessageModalProps) {
  const [tab, setTab] = useState<Tab>('new-contact');
  const [name, setName] = useState('');
  const [contactKey, setContactKey] = useState('');
  const [roomKey, setRoomKey] = useState('');
  const [tryHistorical, setTryHistorical] = useState(false);
  const [permitCapitals, setPermitCapitals] = useState(false);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);
  const hashtagInputRef = useRef<HTMLInputElement>(null);

  const resetForm = () => {
    setName('');
    setContactKey('');
    setRoomKey('');
    setTryHistorical(false);
    setPermitCapitals(false);
    setError('');
  };

  const handleCreate = async () => {
    setError('');
    setLoading(true);

    try {
      if (tab === 'new-contact') {
        if (!name.trim() || !contactKey.trim()) {
          setError('Name and public key are required');
          return;
        }
        // handleCreateContact sets activeConversation with the backend-normalized key
        await onCreateContact(name.trim(), contactKey.trim(), tryHistorical);
      } else if (tab === 'new-room') {
        if (!name.trim() || !roomKey.trim()) {
          setError('Channel name and key are required');
          return;
        }
        await onCreateChannel(name.trim(), roomKey.trim(), tryHistorical);
      } else if (tab === 'hashtag') {
        const channelName = name.trim();
        const validationError = validateHashtagName(channelName);
        if (validationError) {
          setError(validationError);
          return;
        }
        // Normalize to lowercase unless user explicitly permits capitals
        const normalizedName = permitCapitals ? channelName : channelName.toLowerCase();
        await onCreateHashtagChannel(`#${normalizedName}`, tryHistorical);
      }
      resetForm();
      onClose();
    } catch (err) {
      toast.error('Failed to create conversation', {
        description: err instanceof Error ? err.message : undefined,
      });
      setError(err instanceof Error ? err.message : 'Failed to create');
    } finally {
      setLoading(false);
    }
  };

  const validateHashtagName = (channelName: string): string | null => {
    if (!channelName) {
      return 'Channel name is required';
    }
    if (!/^[a-zA-Z0-9]+(-[a-zA-Z0-9]+)*$/.test(channelName)) {
      return 'Use letters, numbers, and single dashes (no leading/trailing dashes)';
    }
    return null;
  };

  const handleCreateAndAddAnother = async () => {
    setError('');
    const channelName = name.trim();
    const validationError = validateHashtagName(channelName);
    if (validationError) {
      setError(validationError);
      return;
    }

    setLoading(true);
    try {
      // Normalize to lowercase unless user explicitly permits capitals
      const normalizedName = permitCapitals ? channelName : channelName.toLowerCase();
      await onCreateHashtagChannel(`#${normalizedName}`, tryHistorical);
      setName('');
      hashtagInputRef.current?.focus();
    } catch (err) {
      toast.error('Failed to create conversation', {
        description: err instanceof Error ? err.message : undefined,
      });
      setError(err instanceof Error ? err.message : 'Failed to create');
    } finally {
      setLoading(false);
    }
  };

  const showHistoricalOption = undecryptedCount > 0;

  return (
    <Dialog
      open={open}
      onOpenChange={(isOpen) => {
        if (!isOpen) {
          resetForm();
          onClose();
        }
      }}
    >
      <DialogContent className="sm:max-w-[500px]">
        <DialogHeader>
          <DialogTitle>New Conversation</DialogTitle>
          <DialogDescription className="sr-only">
            {tab === 'new-contact' && 'Add a new contact by entering their name and public key'}
            {tab === 'new-room' && 'Create a private channel with a shared encryption key'}
            {tab === 'hashtag' && 'Join a public hashtag channel'}
          </DialogDescription>
        </DialogHeader>

        <Tabs
          value={tab}
          onValueChange={(v) => {
            setTab(v as Tab);
            resetForm();
          }}
          className="w-full"
        >
          <TabsList className="grid w-full grid-cols-3">
            <TabsTrigger value="new-contact">Contact</TabsTrigger>
            <TabsTrigger value="new-room">Private Channel</TabsTrigger>
            <TabsTrigger value="hashtag">Hashtag Channel</TabsTrigger>
          </TabsList>

          <TabsContent value="new-contact" className="mt-4 space-y-4">
            <div className="space-y-2">
              <Label htmlFor="contact-name">Name</Label>
              <Input
                id="contact-name"
                value={name}
                onChange={(e) => setName(e.target.value)}
                placeholder="Contact name"
              />
            </div>
            <div className="space-y-2">
              <Label htmlFor="contact-key">Public Key</Label>
              <Input
                id="contact-key"
                value={contactKey}
                onChange={(e) => setContactKey(e.target.value)}
                placeholder="64-character hex public key"
              />
            </div>
          </TabsContent>

          <TabsContent value="new-room" className="mt-4 space-y-4">
            <div className="space-y-2">
              <Label htmlFor="room-name">Channel Name</Label>
              <Input
                id="room-name"
                value={name}
                onChange={(e) => setName(e.target.value)}
                placeholder="Room name"
              />
            </div>
            <div className="space-y-2">
              <Label htmlFor="room-key">Channel Key</Label>
              <div className="flex gap-2">
                <Input
                  id="room-key"
                  value={roomKey}
                  onChange={(e) => setRoomKey(e.target.value)}
                  placeholder="Pre-shared key (hex)"
                  className="flex-1"
                />
                <Button
                  type="button"
                  variant="outline"
                  size="icon"
                  onClick={() => {
                    const bytes = new Uint8Array(16);
                    crypto.getRandomValues(bytes);
                    const hex = Array.from(bytes)
                      .map((b) => b.toString(16).padStart(2, '0'))
                      .join('');
                    setRoomKey(hex);
                  }}
                  title="Generate random key"
                  aria-label="Generate random key"
                >
                  <Dice5 className="h-4 w-4" aria-hidden="true" />
                </Button>
              </div>
            </div>
          </TabsContent>

          <TabsContent value="hashtag" className="mt-4">
            <div className="space-y-2">
              <Label htmlFor="hashtag-name">Hashtag Channel</Label>
              <div className="flex items-center gap-1">
                <span className="text-sm text-muted-foreground">#</span>
                <Input
                  ref={hashtagInputRef}
                  id="hashtag-name"
                  value={name}
                  onChange={(e) => setName(e.target.value)}
                  placeholder="channel-name"
                  className="flex-1"
                />
              </div>
            </div>
            <div className="mt-3 space-y-1">
              <label className="flex items-center gap-3 cursor-pointer">
                <input
                  type="checkbox"
                  checked={permitCapitals}
                  onChange={(e) => setPermitCapitals(e.target.checked)}
                  className="w-4 h-4 rounded border-input accent-primary"
                />
                <span className="text-sm">Permit capitals in channel key derivation</span>
              </label>
              <p className="text-xs text-muted-foreground pl-7">
                Not recommended; most companions normalize to lowercase
              </p>
            </div>
          </TabsContent>
        </Tabs>

        {showHistoricalOption && (
          <div className="space-y-1">
            <div className="flex items-center justify-end space-x-2">
              <Label
                htmlFor="try-historical"
                className="text-sm text-muted-foreground cursor-pointer"
              >
                Try decrypting {undecryptedCount.toLocaleString()} stored packet
                {undecryptedCount !== 1 ? 's' : ''}
              </Label>
              <Checkbox
                id="try-historical"
                checked={tryHistorical}
                onCheckedChange={(checked) => setTryHistorical(checked === true)}
              />
            </div>
            {tryHistorical && (
              <p className="text-xs text-muted-foreground text-right">
                Messages will stream in as they decrypt in the background
              </p>
            )}
          </div>
        )}

        {error && (
          <div className="text-sm text-destructive" role="alert">
            {error}
          </div>
        )}

        <DialogFooter>
          <Button
            variant="outline"
            onClick={() => {
              resetForm();
              onClose();
            }}
          >
            Cancel
          </Button>
          {tab === 'hashtag' && (
            <Button variant="secondary" onClick={handleCreateAndAddAnother} disabled={loading}>
              {loading ? 'Creating...' : 'Create & Add Another'}
            </Button>
          )}
          <Button onClick={handleCreate} disabled={loading}>
            {loading ? 'Creating...' : 'Create'}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
