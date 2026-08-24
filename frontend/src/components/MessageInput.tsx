import {
  useState,
  useCallback,
  useImperativeHandle,
  forwardRef,
  useRef,
  useEffect,
  useMemo,
  type ChangeEvent,
  type FormEvent,
  type KeyboardEvent,
} from 'react';
import { Shrink } from 'lucide-react';
import { Button } from './ui/button';
import { toast } from './ui/sonner';
import { cn } from '@/lib/utils';
import { api } from '../api';
import {
  getTextReplaceEnabled,
  getTextReplaceMapJson,
  applyTextReplacements,
} from '../utils/textReplace';

// MeshCore message size limits (empirically determined from LoRa packet constraints)
// Direct delivery allows ~156 bytes; multi-hop requires buffer for path growth.
// Channels include "sender: " prefix in the encrypted payload.
// All limits are in bytes (UTF-8), not characters, since LoRa packets are byte-constrained.
const DM_HARD_LIMIT = 156; // Max bytes for direct delivery
const DM_WARNING_THRESHOLD = 140; // Conservative for multi-hop
const CHANNEL_HARD_LIMIT = 156; // Base byte limit before sender overhead
const CHANNEL_WARNING_THRESHOLD = 120; // Conservative for multi-hop
const CHANNEL_DANGER_BUFFER = 8; // Red zone starts this many bytes before hard limit

const textEncoder = new TextEncoder();
const RADIO_NO_RESPONSE_SNIPPET = 'no response was heard back';
/** Get UTF-8 byte length of a string (LoRa packets are byte-constrained, not character-constrained). */
function byteLen(s: string): number {
  return textEncoder.encode(s).length;
}

interface MessageInputProps {
  onSend: (text: string) => Promise<void>;
  disabled: boolean;
  placeholder?: string;
  /** Conversation type for character limit calculation */
  conversationType?: 'contact' | 'channel' | 'raw';
  /** Sender name (radio name) for channel message limit calculation */
  senderName?: string;
  /** When the conversation compresses outbound messages (MCMP), the counter
   *  reflects the compressed wire size instead of the raw byte length. */
  mcmpEnabled?: boolean;
  /** MCMP transport version (2 or 3) the estimate should size for. */
  mcmpVersion?: number;
}

type LimitState = 'normal' | 'warning' | 'danger' | 'error';

export interface MessageInputHandle {
  appendText: (text: string) => void;
  focus: () => void;
}

export const MessageInput = forwardRef<MessageInputHandle, MessageInputProps>(function MessageInput(
  { onSend, disabled, placeholder, conversationType, senderName, mcmpEnabled, mcmpVersion },
  ref
) {
  const [text, setText] = useState('');
  const [sending, setSending] = useState(false);
  // Compressed wire size fetched from the backend (the MCMP codec is
  // server-side), tagged with the exact draft it was computed for so a stale
  // result is never shown for different text. null until the first estimate
  // resolves, or when compression is off for this conversation.
  const [compressed, setCompressed] = useState<{ bytes: number; forText: string } | null>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  /** Resize textarea to fit content, clamped between 1 row and ~6 rows. */
  const autoResize = useCallback(() => {
    const el = textareaRef.current;
    if (!el) return;
    el.style.height = 'auto';
    // Clamp: min 40px (≈1 row), max 160px (≈6 rows)
    el.style.height = `${Math.min(el.scrollHeight, 160)}px`;
  }, []);

  useImperativeHandle(ref, () => ({
    appendText: (appendedText: string) => {
      setText((prev) => prev + appendedText);
      textareaRef.current?.focus();
    },
    focus: () => {
      textareaRef.current?.focus();
    },
  }));

  // Re-measure height whenever text changes (covers programmatic updates like appendText)
  useEffect(() => {
    autoResize();
  }, [text, autoResize]);

  // Calculate character limits based on conversation type
  const limits = useMemo(() => {
    if (conversationType === 'contact') {
      return {
        warningAt: DM_WARNING_THRESHOLD,
        dangerAt: DM_HARD_LIMIT, // Same as hard limit for DMs (no intermediate red zone)
        hardLimit: DM_HARD_LIMIT,
      };
    } else if (conversationType === 'channel') {
      // Channel hard limit = 156 bytes - senderName bytes - 2 (for ": " separator)
      const nameByteLen = senderName ? byteLen(senderName) : 10;
      const hardLimit = Math.max(1, CHANNEL_HARD_LIMIT - nameByteLen - 2);
      return {
        warningAt: CHANNEL_WARNING_THRESHOLD,
        dangerAt: Math.max(1, hardLimit - CHANNEL_DANGER_BUFFER),
        hardLimit,
      };
    }
    return null; // Raw/other - no limits
  }, [conversationType, senderName]);

  // UTF-8 byte length of the current text (LoRa packets are byte-constrained)
  const textByteLen = useMemo(() => byteLen(text), [text]);

  // When MCMP is on, poll the backend (which owns the codec) for the compressed
  // wire size, debounced. That size is what actually rides the packet, so the
  // effective character capacity grows as compressible text is typed.
  useEffect(() => {
    if (!mcmpEnabled || !limits || text.trim().length === 0) {
      setCompressed(null);
      return;
    }
    let cancelled = false;
    const handle = setTimeout(() => {
      api
        .estimateMcmp(text, mcmpVersion ?? 2)
        .then((res) => {
          if (!cancelled) setCompressed({ bytes: res.wire_bytes, forText: text });
        })
        .catch(() => {
          if (!cancelled) setCompressed(null);
        });
    }, 150);
    return () => {
      cancelled = true;
      clearTimeout(handle);
    };
  }, [text, mcmpEnabled, mcmpVersion, limits]);

  // The byte count the counter shows: the compressed size when we have one (the
  // exact one for this draft, or the last one while a fresh estimate is in
  // flight), otherwise the raw length (and always for uncompressed
  // conversations).
  const showCompressed = mcmpEnabled && compressed !== null;
  const effectiveByteLen = compressed !== null ? compressed.bytes : textByteLen;
  // True while MCMP is on but the compressed size for the *current* text hasn't
  // resolved yet (the estimate is debounced). We must not judge the draft "too
  // long" from the raw length during this window — that caused a red flash on
  // long-but-compressible messages before the estimate came back.
  const estimatePending =
    mcmpEnabled &&
    !!limits &&
    text.trim().length > 0 &&
    !(compressed !== null && compressed.forText === text);

  // Determine current limit state
  const { limitState, warningMessage } = useMemo((): {
    limitState: LimitState;
    warningMessage: string | null;
  } => {
    if (!limits) return { limitState: 'normal', warningMessage: null };
    // Stay neutral until the compressed size is known — don't alarm on the raw
    // length while compression is being computed.
    if (estimatePending) return { limitState: 'normal', warningMessage: null };

    if (effectiveByteLen >= limits.hardLimit) {
      return { limitState: 'error', warningMessage: 'likely truncated by radio' };
    }
    if (effectiveByteLen >= limits.dangerAt) {
      return { limitState: 'danger', warningMessage: 'may impact multi-repeater hop delivery' };
    }
    if (effectiveByteLen >= limits.warningAt) {
      return { limitState: 'warning', warningMessage: 'may impact multi-repeater hop delivery' };
    }
    return { limitState: 'normal', warningMessage: null };
  }, [effectiveByteLen, limits, estimatePending]);

  const remaining = limits ? limits.hardLimit - effectiveByteLen : 0;

  const handleSubmit = useCallback(
    async (e: FormEvent) => {
      e.preventDefault();
      const trimmed = text.trim();
      if (!trimmed || sending || disabled) return;

      setSending(true);
      try {
        await onSend(trimmed);
        setText('');
      } catch (err) {
        console.error('Failed to send message:', err);
        const description = err instanceof Error ? err.message : 'Check radio connection';
        const isRadioNoResponse =
          err instanceof Error && err.message.toLowerCase().includes(RADIO_NO_RESPONSE_SNIPPET);
        toast.error(isRadioNoResponse ? 'Radio did not confirm send' : 'Failed to send message', {
          description,
        });
        return;
      } finally {
        setSending(false);
      }
      // Refocus after React re-enables the textarea
      setTimeout(() => textareaRef.current?.focus(), 0);
    },
    [text, sending, disabled, onSend]
  );

  const handleChange = useCallback((e: ChangeEvent<HTMLTextAreaElement>) => {
    const input = e.target;
    const raw = input.value;
    // Skip replacement during IME / dead-key composition to avoid garbling interim input
    if (!e.nativeEvent || (e.nativeEvent as InputEvent).isComposing) {
      setText(raw);
      return;
    }
    if (getTextReplaceEnabled()) {
      const result = applyTextReplacements(
        raw,
        input.selectionStart ?? raw.length,
        getTextReplaceMapJson()
      );
      if (result) {
        setText(result.text);
        // Schedule cursor restore after React flushes the new value
        const pos = result.cursor;
        requestAnimationFrame(() => input.setSelectionRange(pos, pos));
        return;
      }
    }
    setText(raw);
  }, []);

  const handleKeyDown = useCallback(
    (e: KeyboardEvent<HTMLTextAreaElement>) => {
      if (e.key === 'Enter' && !e.shiftKey) {
        e.preventDefault();
        handleSubmit(e as unknown as FormEvent);
      }
      // Shift+Enter falls through naturally and inserts a newline
    },
    [handleSubmit]
  );

  const canSubmit = text.trim().length > 0;

  // Show counter for messages (not raw).
  // Desktop: always visible. Mobile: only show count after 100 characters.
  const showCharCounter = limits !== null;
  const showMobileCounterValue = text.length > 100;

  return (
    <form
      className="message-input-shell px-4 py-2.5 border-t border-border flex flex-col gap-1"
      onSubmit={handleSubmit}
      autoComplete="off"
    >
      <div className="flex gap-2 items-end">
        <textarea
          ref={textareaRef}
          name="chat-message-input"
          aria-label={placeholder || 'Type a message'}
          data-lpignore="true"
          data-1p-ignore="true"
          data-bwignore="true"
          rows={1}
          value={text}
          onChange={handleChange}
          onKeyDown={handleKeyDown}
          placeholder={placeholder || 'Type a message...'}
          disabled={disabled || sending}
          className={cn(
            'flex-1 min-w-0 resize-none overflow-y-auto',
            'rounded-md border border-input bg-background px-3 py-2 text-base ring-offset-background',
            'placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2',
            'disabled:cursor-not-allowed disabled:opacity-50 md:text-sm'
          )}
          style={{ minHeight: '40px', maxHeight: '160px' }}
        />
        <Button
          type="submit"
          disabled={disabled || sending || !canSubmit}
          className="flex-shrink-0"
        >
          {sending ? 'Sending...' : 'Send'}
        </Button>
      </div>
      {showCharCounter && (
        <>
          <div className="hidden sm:flex items-center justify-end gap-2 text-xs">
            <span
              className={cn(
                'tabular-nums',
                limitState === 'error' || limitState === 'danger'
                  ? 'text-destructive font-medium'
                  : limitState === 'warning'
                    ? 'text-warning'
                    : 'text-muted-foreground'
              )}
            >
              {showCompressed && (
                <Shrink
                  className="inline h-3 w-3 -mt-0.5 mr-0.5 text-primary"
                  aria-label="compressed size"
                />
              )}
              {effectiveByteLen}/{limits!.hardLimit}
              {remaining < 0 && ` (${remaining})`}
            </span>
            {warningMessage && (
              <span className={cn(limitState === 'error' ? 'text-destructive' : 'text-warning')}>
                — {warningMessage}
              </span>
            )}
          </div>

          {(showMobileCounterValue || warningMessage) && (
            <div className="flex sm:hidden items-center justify-end gap-2 text-xs">
              {showMobileCounterValue && (
                <span
                  className={cn(
                    'tabular-nums',
                    limitState === 'error' || limitState === 'danger'
                      ? 'text-destructive font-medium'
                      : limitState === 'warning'
                        ? 'text-warning'
                        : 'text-muted-foreground'
                  )}
                >
                  {showCompressed && (
                    <Shrink
                      className="inline h-3 w-3 -mt-0.5 mr-0.5 text-primary"
                      aria-label="compressed size"
                    />
                  )}
                  {effectiveByteLen}/{limits!.hardLimit}
                  {remaining < 0 && ` (${remaining})`}
                </span>
              )}
              {warningMessage && (
                <span className={cn(limitState === 'error' ? 'text-destructive' : 'text-warning')}>
                  — {warningMessage}
                </span>
              )}
            </div>
          )}
        </>
      )}
    </form>
  );
});
