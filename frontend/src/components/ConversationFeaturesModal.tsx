import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from './ui/dialog';
import { Switch } from './ui/switch';

/**
 * Per-conversation MeshCore Open feature toggles (compression today; image
 * sharing and other interop features to come). Opened from the chat header.
 *
 * Each feature is one {@link FeatureRow}; toggles apply immediately. To add a
 * feature, add a prop pair (state + setter) and render another row.
 */

interface FeatureRowProps {
  title: string;
  description: string;
  checked: boolean;
  onCheckedChange: (checked: boolean) => void;
  ariaLabel: string;
}

function FeatureRow({ title, description, checked, onCheckedChange, ariaLabel }: FeatureRowProps) {
  return (
    <div className="flex items-start justify-between gap-3 rounded-md border border-border p-3">
      <div className="min-w-0">
        <div className="text-sm font-medium text-foreground">{title}</div>
        <div className="mt-0.5 text-xs leading-snug text-muted-foreground">{description}</div>
      </div>
      <Switch
        checked={checked}
        onCheckedChange={onCheckedChange}
        aria-label={ariaLabel}
        className="mt-0.5 flex-shrink-0"
      />
    </div>
  );
}

interface ConversationFeaturesModalProps {
  open: boolean;
  onClose: () => void;
  conversationType: 'contact' | 'channel';
  conversationId: string;
  conversationName: string;
  mcmpEnabled: boolean;
  onSetMcmpEnabled: (type: 'channel' | 'contact', id: string, enabled: boolean) => void;
}

export function ConversationFeaturesModal({
  open,
  onClose,
  conversationType,
  conversationId,
  conversationName,
  mcmpEnabled,
  onSetMcmpEnabled,
}: ConversationFeaturesModalProps) {
  return (
    <Dialog open={open} onOpenChange={(isOpen) => !isOpen && onClose()}>
      <DialogContent className="sm:max-w-[520px]">
        <DialogHeader>
          <DialogTitle>Conversation features</DialogTitle>
          <DialogDescription>
            Optional features for{' '}
            <span className="font-medium text-foreground">{conversationName}</span>. Both sides must
            support a feature for it to work, so turn one on only for a contact or channel you know
            can handle it.
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-2">
          <FeatureRow
            title="Compress messages (MCMP)"
            description="Pack more text into a single packet with MCMP compression. The recipient must also support MCMP (meshcore-open / RemoteTerm) to read it; the compose counter then shows the compressed size."
            checked={mcmpEnabled}
            onCheckedChange={(next) => onSetMcmpEnabled(conversationType, conversationId, next)}
            ariaLabel={mcmpEnabled ? 'Disable MCMP compression' : 'Enable MCMP compression'}
          />
          {/* Future MeshCore Open features (image sharing, etc.) add a row here. */}
        </div>
      </DialogContent>
    </Dialog>
  );
}
