import { useState, useRef } from 'react';
import type { Contact, Conversation } from '../types';
import { getContactDisplayName } from '../utils/pubkey';
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogFooter,
} from './ui/dialog';
import { Tabs, TabsList, TabsTrigger, TabsContent } from './ui/tabs';
import { Input } from './ui/input';
import { Label } from './ui/label';
import { Checkbox } from './ui/checkbox';
import { Button } from './ui/button';

type Tab = 'existing' | 'new-contact' | 'new-room' | 'hashtag';

interface NewMessageModalProps {
  open: boolean;
  contacts: Contact[];
  undecryptedCount: number;
  onClose: () => void;
  onSelectConversation: (conversation: Conversation) => void;
  onCreateContact: (name: string, publicKey: string, tryHistorical: boolean) => Promise<void>;
  onCreateChannel: (name: string, key: string, tryHistorical: boolean) => Promise<void>;
  onCreateHashtagChannel: (name: string, tryHistorical: boolean) => Promise<void>;
}

export function NewMessageModal({
  open,
  contacts,
  undecryptedCount,
  onClose,
  onSelectConversation,
  onCreateContact,
  onCreateChannel,
  onCreateHashtagChannel,
}: NewMessageModalProps) {
  const [tab, setTab] = useState<Tab>('existing');
  const [name, setName] = useState('');
  const [key, setKey] = useState('');
  const [tryHistorical, setTryHistorical] = useState(false);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);
  const hashtagInputRef = useRef<HTMLInputElement>(null);

  const handleCreate = async () => {
    setError('');
    setLoading(true);

    try {
      if (tab === 'new-contact') {
        if (!name.trim() || !key.trim()) {
          setError('Name and public key are required');
          return;
        }
        await onCreateContact(name.trim(), key.trim(), tryHistorical);
        onSelectConversation({
          type: 'contact',
          id: key.trim(),
          name: name.trim(),
        });
      } else if (tab === 'new-room') {
        if (!name.trim() || !key.trim()) {
          setError('Room name and key are required');
          return;
        }
        await onCreateChannel(name.trim(), key.trim(), tryHistorical);
      } else if (tab === 'hashtag') {
        const channelName = name.trim();
        const validationError = validateHashtagName(channelName);
        if (validationError) {
          setError(validationError);
          return;
        }
        await onCreateHashtagChannel(`#${channelName}`, tryHistorical);
      }
      onClose();
    } catch (err) {
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
      await onCreateHashtagChannel(`#${channelName}`, tryHistorical);
      setName('');
      hashtagInputRef.current?.focus();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to create');
    } finally {
      setLoading(false);
    }
  };

  const showHistoricalOption = tab !== 'existing' && undecryptedCount > 0;

  return (
    <Dialog open={open} onOpenChange={(isOpen) => !isOpen && onClose()}>
      <DialogContent className="sm:max-w-[500px]">
        <DialogHeader>
          <DialogTitle>New Conversation</DialogTitle>
        </DialogHeader>

        <Tabs value={tab} onValueChange={(v) => setTab(v as Tab)} className="w-full">
          <TabsList className="grid w-full grid-cols-4">
            <TabsTrigger value="existing">Existing</TabsTrigger>
            <TabsTrigger value="new-contact">Contact</TabsTrigger>
            <TabsTrigger value="new-room">Room</TabsTrigger>
            <TabsTrigger value="hashtag">Hashtag</TabsTrigger>
          </TabsList>

          <TabsContent value="existing" className="mt-4">
            <div className="max-h-[300px] overflow-y-auto rounded-md border">
              {contacts.length === 0 ? (
                <div className="p-4 text-center text-muted-foreground">
                  No contacts available
                </div>
              ) : (
                contacts.map((contact) => (
                  <div
                    key={contact.public_key}
                    className="cursor-pointer px-4 py-2 hover:bg-accent"
                    onClick={() => {
                      onSelectConversation({
                        type: 'contact',
                        id: contact.public_key,
                        name: getContactDisplayName(contact.name, contact.public_key),
                      });
                      onClose();
                    }}
                  >
                    {getContactDisplayName(contact.name, contact.public_key)}
                  </div>
                ))
              )}
            </div>
          </TabsContent>

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
                value={key}
                onChange={(e) => setKey(e.target.value)}
                placeholder="64-character hex public key"
              />
            </div>
          </TabsContent>

          <TabsContent value="new-room" className="mt-4 space-y-4">
            <div className="space-y-2">
              <Label htmlFor="room-name">Room Name</Label>
              <Input
                id="room-name"
                value={name}
                onChange={(e) => setName(e.target.value)}
                placeholder="Room name"
              />
            </div>
            <div className="space-y-2">
              <Label htmlFor="room-key">Room Key</Label>
              <Input
                id="room-key"
                value={key}
                onChange={(e) => setKey(e.target.value)}
                placeholder="Pre-shared key (hex)"
              />
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
          </TabsContent>
        </Tabs>

        {showHistoricalOption && (
          <div className="flex items-center justify-end space-x-2">
            <Label
              htmlFor="try-historical"
              className="text-sm text-muted-foreground cursor-pointer"
            >
              Try decrypting {undecryptedCount.toLocaleString()} stored packet{undecryptedCount !== 1 ? 's' : ''}
            </Label>
            <Checkbox
              id="try-historical"
              checked={tryHistorical}
              onCheckedChange={(checked) => setTryHistorical(checked === true)}
            />
          </div>
        )}

        {error && (
          <div className="text-sm text-destructive">{error}</div>
        )}

        <DialogFooter>
          <Button variant="outline" onClick={onClose}>
            Cancel
          </Button>
          {tab === 'hashtag' && (
            <Button variant="secondary" onClick={handleCreateAndAddAnother} disabled={loading}>
              {loading ? 'Creating...' : 'Create & Add Another'}
            </Button>
          )}
          {tab !== 'existing' && (
            <Button onClick={handleCreate} disabled={loading}>
              {loading ? 'Creating...' : 'Create'}
            </Button>
          )}
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
