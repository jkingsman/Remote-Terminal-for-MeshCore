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
import { cn } from '@/lib/utils';
import {
  getTextReplaceEnabled,
  getTextReplaceMapJson,
  applyTextReplacements,
} from '../utils/textReplace';
import type { Contact } from '../types';
import { getContactDisplayName } from '../utils/pubkey';
import { getCaretCoordinates } from '../utils/caretCoordinates';

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
  /** Contacts list for @-mention autocomplete */
  contacts?: Contact[];
}

type LimitState = 'normal' | 'warning' | 'danger' | 'error';

export interface MessageInputHandle {
  appendText: (text: string) => void;
  focus: () => void;
}

export const MessageInput = forwardRef<MessageInputHandle, MessageInputProps>(function MessageInput(
  { onSend, disabled, placeholder, conversationType, senderName, contacts },
  ref
) {
  const [text, setText] = useState('');
  const [sending, setSending] = useState(false);
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  // Mention state
  const [mentionQuery, setMentionQuery] = useState<string | null>(null);
  const [mentionCaretPos, setMentionCaretPos] = useState<number>(-1);
  const [mentionCoords, setMentionCoords] = useState<{ top: number; left: number } | null>(null);
  const [mentionIndex, setMentionIndex] = useState(0);

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

  // Determine current limit state
  const { limitState, warningMessage } = useMemo((): {
    limitState: LimitState;
    warningMessage: string | null;
  } => {
    if (!limits) return { limitState: 'normal', warningMessage: null };

    if (textByteLen >= limits.hardLimit) {
      return { limitState: 'error', warningMessage: 'likely truncated by radio' };
    }
    if (textByteLen >= limits.dangerAt) {
      return { limitState: 'danger', warningMessage: 'may impact multi-repeater hop delivery' };
    }
    if (textByteLen >= limits.warningAt) {
      return { limitState: 'warning', warningMessage: 'may impact multi-repeater hop delivery' };
    }
    return { limitState: 'normal', warningMessage: null };
  }, [textByteLen, limits]);

  const remaining = limits ? limits.hardLimit - textByteLen : 0;

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
    const pos = input.selectionStart ?? raw.length;

    // Skip replacement during IME / dead-key composition to avoid garbling interim input
    if (!e.nativeEvent || (e.nativeEvent as InputEvent).isComposing) {
      setText(raw);
      return;
    }

    // Mention detection
    const textBeforeCaret = raw.substring(0, pos);
    const lastAt = textBeforeCaret.lastIndexOf('@');
    // We want to trigger if @ is the start of a word, newline, or start of string
    const isAtStartOrAfterWhitespace =
      lastAt === 0 || (lastAt > 0 && /\s/.test(textBeforeCaret[lastAt - 1]));

    if (lastAt !== -1 && isAtStartOrAfterWhitespace) {
      const query = textBeforeCaret.substring(lastAt + 1);
      // Only show if there's no space between @ and caret
      if (!query.includes(' ')) {
        setMentionQuery(query);
        setMentionCaretPos(lastAt);
        setMentionIndex(0);
        // Calculate coords
        const coords = getCaretCoordinates(input, lastAt);
        setMentionCoords({
          top: coords.top - input.scrollTop,
          left: coords.left - input.scrollLeft,
        });
      } else {
        setMentionQuery(null);
      }
    } else {
      setMentionQuery(null);
    }

    if (getTextReplaceEnabled()) {
      const result = applyTextReplacements(raw, pos, getTextReplaceMapJson());
      if (result) {
        setText(result.text);
        // Schedule cursor restore after React flushes the new value
        const resPos = result.cursor;
        requestAnimationFrame(() => input.setSelectionRange(resPos, resPos));
        return;
      }
    }
    setText(raw);
  }, []);

  const filteredContacts = useMemo(() => {
    if (mentionQuery === null || !contacts) return [];
    const q = mentionQuery.toLowerCase();
    return contacts
      .map((c) => ({
        contact: c,
        displayName: getContactDisplayName(c.name, c.public_key, c.last_advert),
      }))
      .filter((item) => item.displayName.toLowerCase().includes(q))
      .slice(0, 8);
  }, [mentionQuery, contacts]);

  const insertMention = useCallback(
    (item: { contact: Contact; displayName: string }) => {
      if (mentionCaretPos === -1) return;
      const mention = `@[${item.displayName}] `;
      const before = text.substring(0, mentionCaretPos);
      const after = text.substring(textareaRef.current?.selectionStart ?? text.length);
      const newText = before + mention + after;
      setText(newText);
      setMentionQuery(null);

      // Restore focus and position cursor after the mention
      const newPos = mentionCaretPos + mention.length;
      requestAnimationFrame(() => {
        if (textareaRef.current) {
          textareaRef.current.focus();
          textareaRef.current.setSelectionRange(newPos, newPos);
        }
      });
    },
    [text, mentionCaretPos]
  );

  const handleKeyDown = useCallback(
    (e: KeyboardEvent<HTMLTextAreaElement>) => {
      if (mentionQuery !== null && filteredContacts.length > 0) {
        if (e.key === 'ArrowDown') {
          e.preventDefault();
          setMentionIndex((prev) => (prev + 1) % filteredContacts.length);
          return;
        }
        if (e.key === 'ArrowUp') {
          e.preventDefault();
          setMentionIndex((prev) => (prev - 1 + filteredContacts.length) % filteredContacts.length);
          return;
        }
        if (e.key === 'Enter' || e.key === 'Tab') {
          e.preventDefault();
          insertMention(filteredContacts[mentionIndex]);
          return;
        }
        if (e.key === 'Escape') {
          e.preventDefault();
          setMentionQuery(null);
          return;
        }
      }

      if (e.key === 'Enter' && !e.shiftKey) {
        e.preventDefault();
        handleSubmit(e as unknown as FormEvent);
      }
      // Shift+Enter falls through naturally and inserts a newline
    },
    [mentionQuery, filteredContacts, mentionIndex, insertMention, handleSubmit]
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
      <div className="relative flex gap-2 items-end">
        <textarea
          ref={textareaRef}
          autoComplete="off"
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

        {mentionQuery !== null && mentionCoords && (
          <div
            className="absolute z-[100] min-w-[180px] overflow-hidden rounded-md border bg-popover text-popover-foreground shadow-md animate-in fade-in-0 zoom-in-95"
            style={{
              top: `${mentionCoords.top}px`,
              left: `${mentionCoords.left}px`,
              transform: 'translateY(-100%) translateY(-8px)', // Position above the caret
            }}
          >
            <div className="flex flex-col p-1">
              {filteredContacts.length > 0 ? (
                filteredContacts.map((item, index) => (
                  <button
                    key={item.contact.public_key}
                    type="button"
                    className={cn(
                      'flex items-center gap-2 rounded-sm px-2 py-1.5 text-sm outline-none transition-colors text-left',
                      index === mentionIndex
                        ? 'bg-accent text-accent-foreground'
                        : 'hover:bg-accent/50'
                    )}
                    onClick={() => insertMention(item)}
                  >
                    <span className="truncate">{item.displayName}</span>
                    <span className="ml-auto text-[10px] text-muted-foreground opacity-50">
                      {item.contact.public_key.slice(0, 8)}
                    </span>
                  </button>
                ))
              ) : (
                <div className="px-3 py-2 text-xs text-muted-foreground italic text-center">
                  {contacts && contacts.length > 0
                    ? 'No se encontraron contactos'
                    : 'Lista de contactos vacía'}
                </div>
              )}
            </div>
          </div>
        )}

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
              {textByteLen}/{limits!.hardLimit}
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
                  {textByteLen}/{limits!.hardLimit}
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
