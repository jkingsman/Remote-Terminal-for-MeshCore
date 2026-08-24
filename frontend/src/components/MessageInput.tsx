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
import { Button } from './ui/button';
import { toast } from './ui/sonner';
import { api } from '../api';
import { cn } from '@/lib/utils';
import {
  getTextReplaceEnabled,
  getTextReplaceMapJson,
  applyTextReplacements,
} from '../utils/textReplace';

// MeshCore message size limits (empirically determined from LoRa packet constraints)
// Direct delivery allows ~156 bytes; multi-hop requires buffer for path growth.
// Channels include "sender: " prefix in the encrypted payload.
// All limits are in bytes (UTF-8), not characters, since LoRa packets are byte-constrained.
const DM_HARD_LIMIT = 156;
const DM_WARNING_THRESHOLD = 140;
const CHANNEL_HARD_LIMIT = 156;
const CHANNEL_WARNING_THRESHOLD = 120;
const CHANNEL_DANGER_BUFFER = 8;

const textEncoder = new TextEncoder();
const RADIO_NO_RESPONSE_SNIPPET = 'no response was heard back';

function byteLen(s: string): number {
  return textEncoder.encode(s).length;
}

interface MessageInputProps {
  onSend: (text: string) => Promise<void>;
  disabled: boolean;
  placeholder?: string;
  conversationType?: 'contact' | 'channel' | 'raw';
  senderName?: string;
  /** Channel key for MCMP size estimation (channels only) */
  channelKey?: string;
  /** Whether MCMP compression is enabled for this conversation */
  mcmpEnabled?: boolean;
  /** Whether MCMP signing is enabled (channels only) */
  mcmpSignEnabled?: boolean;
}

type LimitState = 'normal' | 'warning' | 'danger' | 'error';

export interface MessageInputHandle {
  appendText: (text: string) => void;
  focus: () => void;
}

export const MessageInput = forwardRef<MessageInputHandle, MessageInputProps>(function MessageInput(
  {
    onSend,
    disabled,
    placeholder,
    conversationType,
    senderName,
    channelKey,
    mcmpEnabled = false,
    mcmpSignEnabled = false,
  },
  ref
) {
  const [text, setText] = useState('');
  const [sending, setSending] = useState(false);
  const [estimatedSize, setEstimatedSize] = useState<number | null>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  const autoResize = useCallback(() => {
    const el = textareaRef.current;
    if (!el) return;
    el.style.height = 'auto';
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

  useEffect(() => {
    autoResize();
  }, [text, autoResize]);

  // Debounced MCMP size estimation
  useEffect(() => {
    if (!mcmpEnabled) {
      setEstimatedSize(null);
      return;
    }

    const trimmed = text.trim();
    if (!trimmed) {
      setEstimatedSize(0);
      return;
    }

    let cancelled = false;
    const timer = setTimeout(async () => {
      try {
        const result = await api.estimateMessageSize({
          text: trimmed,
          channel_key: conversationType === 'channel' ? channelKey : undefined,
          include_signature: conversationType === 'channel' && mcmpSignEnabled,
        });
        if (!cancelled) setEstimatedSize(result.size);
      } catch {
        if (!cancelled) setEstimatedSize(null);
      }
    }, 300);

    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
  }, [text, mcmpEnabled, mcmpSignEnabled, conversationType, channelKey]);

  // Calculate character limits based on conversation type
  const limits = useMemo(() => {
    if (conversationType === 'contact') {
      return {
        warningAt: DM_WARNING_THRESHOLD,
        dangerAt: DM_HARD_LIMIT,
        hardLimit: DM_HARD_LIMIT,
      };
    } else if (conversationType === 'channel') {
      const nameByteLen = senderName ? byteLen(senderName) : 10;
      const hardLimit = Math.max(1, CHANNEL_HARD_LIMIT - nameByteLen - 2);
      return {
        warningAt: CHANNEL_WARNING_THRESHOLD,
        dangerAt: Math.max(1, hardLimit - CHANNEL_DANGER_BUFFER),
        hardLimit,
      };
    }
    return null;
  }, [conversationType, senderName]);

  // UTF-8 byte length of the current text (raw, uncompressed)
  const textByteLen = useMemo(() => byteLen(text), [text]);

  // Effective payload size: compressed if MCMP enabled and estimate available, else raw
  const effectiveTextSize = mcmpEnabled && estimatedSize !== null ? estimatedSize : textByteLen;

  // Determine current limit state
  const { limitState, warningMessage } = useMemo((): {
    limitState: LimitState;
    warningMessage: string | null;
  } => {
    if (!limits) return { limitState: 'normal', warningMessage: null };

    const size = effectiveTextSize;
    if (size >= limits.hardLimit) {
      return { limitState: 'error', warningMessage: 'likely truncated by radio' };
    }
    if (size >= limits.dangerAt) {
      return { limitState: 'danger', warningMessage: 'may impact multi-repeater hop delivery' };
    }
    if (size >= limits.warningAt) {
      return { limitState: 'warning', warningMessage: 'may impact multi-repeater hop delivery' };
    }
    return { limitState: 'normal', warningMessage: null };
  }, [effectiveTextSize, limits]);

  const remaining = limits ? limits.hardLimit - effectiveTextSize : 0;

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
      setTimeout(() => textareaRef.current?.focus(), 0);
    },
    [text, sending, disabled, onSend]
  );

  const handleChange = useCallback((e: ChangeEvent<HTMLTextAreaElement>) => {
    const input = e.target;
    const raw = input.value;
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
    },
    [handleSubmit]
  );

  const canSubmit = text.trim().length > 0;
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
              {effectiveTextSize}/{limits!.hardLimit}
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
                  {effectiveTextSize}/{limits!.hardLimit}
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