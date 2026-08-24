import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from './ui/dialog';
import { Switch } from './ui/switch';
import { cn } from '@/lib/utils';

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
    <div className="flex items-start justify-between gap-3">
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

const MCMP_VERSIONS: { value: number; label: string; description: string }[] = [
  { value: 2, label: 'v2', description: 'Smaller; widely compatible' },
  { value: 3, label: 'v3', description: 'Container (timestamp); matches the advanced fork' },
];

interface ConversationFeaturesModalProps {
  open: boolean;
  onClose: () => void;
  conversationType: 'contact' | 'channel';
  conversationId: string;
  conversationName: string;
  mcmpEnabled: boolean;
  mcmpVersion: number;
  onSetMcmpEnabled: (
    type: 'channel' | 'contact',
    id: string,
    enabled: boolean,
    version: number
  ) => void;
}

export function ConversationFeaturesModal({
  open,
  onClose,
  conversationType,
  conversationId,
  conversationName,
  mcmpEnabled,
  mcmpVersion,
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
          <div className="rounded-md border border-border p-3">
            <FeatureRow
              title="Compress messages (MCMP)"
              description="Pack more text into a single packet with MCMP compression. The recipient must also support MCMP (meshcore-open / RemoteTerm) to read it; the compose counter then shows the compressed size."
              checked={mcmpEnabled}
              onCheckedChange={(next) =>
                onSetMcmpEnabled(conversationType, conversationId, next, mcmpVersion)
              }
              ariaLabel={mcmpEnabled ? 'Disable MCMP compression' : 'Enable MCMP compression'}
            />

            {mcmpEnabled && (
              <div className="mt-3 border-t border-border pt-3">
                <div className="mb-1.5 text-xs font-medium text-foreground">Version</div>
                <div
                  className="grid grid-cols-2 gap-1.5"
                  role="radiogroup"
                  aria-label="MCMP version"
                >
                  {MCMP_VERSIONS.map((opt) => {
                    const selected = mcmpVersion === opt.value;
                    return (
                      <button
                        key={opt.value}
                        type="button"
                        role="radio"
                        aria-checked={selected}
                        aria-label={`MCMP ${opt.label}`}
                        onClick={() =>
                          onSetMcmpEnabled(conversationType, conversationId, true, opt.value)
                        }
                        className={cn(
                          'rounded-md border px-2.5 py-1.5 text-left text-sm transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring',
                          selected
                            ? 'border-primary bg-primary/10 text-foreground'
                            : 'border-border hover:bg-accent'
                        )}
                      >
                        <div className="font-medium">MCMP {opt.label}</div>
                        <div className="text-xs leading-snug text-muted-foreground">
                          {opt.description}
                        </div>
                      </button>
                    );
                  })}
                </div>
                <p className="mt-2 text-xs leading-snug text-muted-foreground">
                  v2 is smallest and universally readable. v3 adds a metadata container (a timestamp
                  now; signing/replies later) and is slightly larger. Both are decoded automatically
                  on the way in.
                </p>
              </div>
            )}
          </div>
          {/* Future MeshCore Open features (image sharing, etc.) add a row here. */}
        </div>
      </DialogContent>
    </Dialog>
  );
}
