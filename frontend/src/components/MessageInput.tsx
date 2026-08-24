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
  type PointerEvent,
} from 'react';
import { ImagePlus, Loader2, Mic, Smile, X } from 'lucide-react';
import { api } from '../api';
import { encodeMeshImage, type EncodedMeshImage } from '../services/imageCodec';
import { estimateImageTransmitSeconds, IMAGE_FRAGMENT_BYTES } from '../utils/imageEnvelope';
import { VoiceCapture } from '../services/voiceCapture';
import { Button } from './ui/button';
import { toast } from './ui/sonner';
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
const DM_HARD_LIMIT = 156; // Max bytes for direct delivery
const DM_WARNING_THRESHOLD = 140; // Conservative for multi-hop
const CHANNEL_HARD_LIMIT = 156; // Base byte limit before sender overhead
const CHANNEL_WARNING_THRESHOLD = 120; // Conservative for multi-hop
const CHANNEL_DANGER_BUFFER = 8; // Red zone starts this many bytes before hard limit

const textEncoder = new TextEncoder();
const RADIO_NO_RESPONSE_SNIPPET = 'no response was heard back';
const COMPOSER_EMOJIS = [
  '😀',
  '😂',
  '😊',
  '😍',
  '🥰',
  '😎',
  '🤔',
  '😢',
  '😮',
  '👍',
  '👎',
  '👏',
  '🙏',
  '💪',
  '❤️',
  '🔥',
  '🎉',
  '✨',
  '✅',
  '📻',
  '📍',
  '🚀',
  '👋',
  '💬',
];
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
  voiceConversation?: { type: 'PRIV' | 'CHAN'; key: string };
}

type LimitState = 'normal' | 'warning' | 'danger' | 'error';

export interface MessageInputHandle {
  appendText: (text: string) => void;
  focus: () => void;
}

export const MessageInput = forwardRef<MessageInputHandle, MessageInputProps>(function MessageInput(
  { onSend, disabled, placeholder, conversationType, senderName, voiceConversation },
  ref
) {
  const [text, setText] = useState('');
  const [sending, setSending] = useState(false);
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const captureRef = useRef<VoiceCapture | null>(null);
  const voiceHeldRef = useRef(false);
  const cancelVoiceRef = useRef(false);
  const voiceStartedAtRef = useRef(0);
  const [recording, setRecording] = useState(false);
  const [voiceSending, setVoiceSending] = useState(false);
  const [cancelVoice, setCancelVoice] = useState(false);
  const [elapsed, setElapsed] = useState(0);
  const [emojiPickerOpen, setEmojiPickerOpen] = useState(false);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const [imageFile, setImageFile] = useState<File | null>(null);
  const [imageSize, setImageSize] = useState<64 | 128 | 256>(256);
  const [imagePreview, setImagePreview] = useState<EncodedMeshImage | null>(null);
  const [imagePreparing, setImagePreparing] = useState(false);
  const [imageSending, setImageSending] = useState(false);
  const [imagePreviewUrl, setImagePreviewUrl] = useState<string | null>(null);

  useEffect(() => {
    if (!imagePreview) {
      setImagePreviewUrl(null);
      return;
    }
    const url = URL.createObjectURL(imagePreview.blob);
    setImagePreviewUrl(url);
    return () => URL.revokeObjectURL(url);
  }, [imagePreview]);

  const prepareImage = useCallback(async (file: File, maxDimension: 64 | 128 | 256) => {
    setImagePreparing(true);
    setImagePreview(null);
    try {
      setImagePreview(await encodeMeshImage(file, maxDimension));
    } catch (error) {
      setImageFile(null);
      toast.error('Image unavailable', {
        description: error instanceof Error ? error.message : String(error),
      });
    } finally {
      setImagePreparing(false);
    }
  }, []);

  const clearImage = useCallback(() => {
    setImageFile(null);
    setImagePreview(null);
    if (fileInputRef.current) fileInputRef.current.value = '';
  }, []);

  const sendImage = useCallback(async () => {
    if (!voiceConversation || !imagePreview || imageSending) return;
    setImageSending(true);
    try {
      await api.sendImage(voiceConversation.type, voiceConversation.key, imagePreview);
      clearImage();
    } catch (error) {
      toast.error('Image message failed', {
        description: error instanceof Error ? error.message : String(error),
      });
    } finally {
      setImageSending(false);
    }
  }, [clearImage, imagePreview, imageSending, voiceConversation]);

  useEffect(() => {
    if (!recording) return;
    const timer = window.setInterval(
      () => setElapsed((performance.now() - voiceStartedAtRef.current) / 1000),
      100
    );
    return () => window.clearInterval(timer);
  }, [recording]);

  const finishVoice = useCallback(
    async (cancel = false) => {
      const capture = captureRef.current;
      if (!capture) return;
      captureRef.current = null;
      setRecording(false);
      try {
        if (cancel || cancelVoiceRef.current) {
          await capture.cancel();
          return;
        }
        const result = await capture.stop();
        if (result.durationMs < 200) return;
        if (!voiceConversation) return;
        setVoiceSending(true);
        await api.sendVoice(voiceConversation.type, voiceConversation.key, result.pcm);
      } catch (error) {
        toast.error('Voice message failed', {
          description: error instanceof Error ? error.message : String(error),
        });
      } finally {
        setVoiceSending(false);
        setCancelVoice(false);
        cancelVoiceRef.current = false;
        setElapsed(0);
      }
    },
    [voiceConversation]
  );

  const startVoice = useCallback(
    async (event: PointerEvent<HTMLButtonElement>) => {
      if (!voiceConversation || disabled || voiceSending) return;
      if (!window.isSecureContext) {
        toast.error('Voice recording requires HTTPS to access your microphone.', {
          action: {
            label: 'Configure HTTPS',
            onClick: () => {
              window.location.hash = '#settings/https';
            },
          },
        });
        return;
      }
      event.currentTarget.setPointerCapture?.(event.pointerId);
      voiceHeldRef.current = true;
      const capture = new VoiceCapture();
      try {
        await capture.start();
        if (!voiceHeldRef.current) {
          await capture.cancel();
          return;
        }
        captureRef.current = capture;
        voiceStartedAtRef.current = performance.now();
        setRecording(true);
        window.setTimeout(() => {
          void finishVoice(false);
        }, 10_000);
      } catch (error) {
        toast.error('Microphone unavailable', {
          description: error instanceof Error ? error.message : String(error),
        });
      }
    },
    [disabled, finishVoice, voiceConversation, voiceSending]
  );

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

  const insertEmoji = useCallback(
    (emoji: string) => {
      const input = textareaRef.current;
      const start = input?.selectionStart ?? text.length;
      const end = input?.selectionEnd ?? start;
      setText((current) => `${current.slice(0, start)}${emoji}${current.slice(end)}`);
      setEmojiPickerOpen(false);
      requestAnimationFrame(() => {
        const cursor = start + emoji.length;
        textareaRef.current?.focus();
        textareaRef.current?.setSelectionRange(cursor, cursor);
      });
    },
    [text.length]
  );

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
      {(imageFile || imagePreparing) && (
        <div className="rounded-lg border border-border bg-muted/30 p-3">
          <div className="flex gap-3">
            <div className="flex h-24 w-24 shrink-0 items-center justify-center overflow-hidden rounded-md bg-muted">
              {imagePreparing || !imagePreviewUrl ? (
                <Loader2 className="animate-spin text-muted-foreground" size={22} />
              ) : (
                <img
                  src={imagePreviewUrl}
                  alt="Image attachment preview"
                  className="h-full w-full object-cover"
                />
              )}
            </div>
            <div className="min-w-0 flex-1 text-sm">
              <div className="font-medium">Image attachment</div>
              {imagePreview && (
                <div className="mt-1 space-y-0.5 text-xs text-muted-foreground">
                  <div>
                    {imagePreview.width}×{imagePreview.height} ·{' '}
                    {imagePreview.format === 0 ? 'AVIF' : 'JPEG'}
                  </div>
                  <div>
                    {(imagePreview.blob.size / 1024).toFixed(1)} KB ·{' '}
                    {Math.ceil(imagePreview.blob.size / IMAGE_FRAGMENT_BYTES)} fragments
                  </div>
                  <div>
                    Estimated transfer: ~
                    {Math.max(
                      1,
                      Math.round(
                        estimateImageTransmitSeconds(
                          Math.ceil(imagePreview.blob.size / IMAGE_FRAGMENT_BYTES),
                          imagePreview.blob.size
                        )
                      )
                    )}
                    s per direct hop
                  </div>
                  {imagePreview.blob.size > 3000 && (
                    <div className="text-warning">
                      Large LoRa transfer — consider a smaller size.
                    </div>
                  )}
                </div>
              )}
              <label className="mt-2 flex items-center gap-2 text-xs">
                Max size
                <select
                  aria-label="Maximum image dimension"
                  value={imageSize}
                  disabled={imagePreparing || imageSending}
                  className="rounded border border-input bg-background px-1.5 py-1"
                  onChange={(event) => {
                    const size = Number(event.target.value) as 64 | 128 | 256;
                    setImageSize(size);
                    if (imageFile) void prepareImage(imageFile, size);
                  }}
                >
                  <option value={64}>64 px</option>
                  <option value={128}>128 px</option>
                  <option value={256}>256 px</option>
                </select>
              </label>
            </div>
          </div>
          <div className="mt-3 flex justify-end gap-2">
            <Button
              type="button"
              variant="outline"
              size="sm"
              disabled={imageSending}
              onClick={clearImage}
            >
              Cancel
            </Button>
            <Button
              type="button"
              size="sm"
              disabled={!imagePreview || imagePreparing || imageSending}
              onClick={() => void sendImage()}
            >
              {imageSending ? 'Sending...' : 'Send image'}
            </Button>
          </div>
        </div>
      )}
      <div className="relative flex gap-2 items-end">
        {recording && voiceConversation && (
          <Button
            type="button"
            variant={recording ? 'destructive' : 'outline'}
            size="icon"
            className={cn(
              'flex-shrink-0 touch-none rounded-full',
              recording && !cancelVoice && 'animate-pulse'
            )}
            disabled={disabled || voiceSending}
            aria-label={
              recording ? 'Release to send voice message' : 'Hold to record voice message'
            }
            onPointerDown={startVoice}
            onPointerUp={() => {
              voiceHeldRef.current = false;
              void finishVoice();
            }}
            onPointerCancel={() => {
              voiceHeldRef.current = false;
              void finishVoice(true);
            }}
            onPointerMove={(event) => {
              const bounds = event.currentTarget.getBoundingClientRect();
              const movedAway =
                event.clientY < bounds.top - 60 ||
                event.clientY > bounds.bottom + 60 ||
                event.clientX < bounds.left - 60 ||
                event.clientX > bounds.right + 60;
              if (recording && movedAway) {
                cancelVoiceRef.current = true;
                setCancelVoice(true);
              }
            }}
          >
            {cancelVoice ? <X size={18} /> : <Mic size={18} />}
          </Button>
        )}
        {recording ? (
          <div
            role="status"
            aria-live="polite"
            className={cn(
              'flex min-h-10 flex-1 min-w-0 items-center gap-2 rounded-md border px-3 text-sm',
              cancelVoice
                ? 'border-destructive/50 bg-destructive/10 text-destructive'
                : 'border-border bg-muted/50 text-foreground'
            )}
          >
            <span
              className={cn(
                'h-2 w-2 shrink-0 rounded-full bg-destructive',
                !cancelVoice && 'animate-pulse'
              )}
            />
            <span className="w-10 shrink-0 tabular-nums font-medium">
              {Math.floor(elapsed / 60)}:
              {Math.floor(elapsed % 60)
                .toString()
                .padStart(2, '0')}
            </span>
            <span
              className="flex h-5 flex-1 items-center justify-center gap-0.5"
              aria-hidden="true"
            >
              {[2, 4, 3, 5, 3, 4, 2].map((height, index) => (
                <span
                  key={index}
                  className={cn(
                    'w-0.5 rounded-full bg-current opacity-60',
                    !cancelVoice && 'animate-pulse'
                  )}
                  style={{ height: `${height * 2}px`, animationDelay: `${index * 90}ms` }}
                />
              ))}
            </span>
            <span className="shrink-0 text-xs text-muted-foreground">
              {cancelVoice ? 'Release to cancel' : 'Release to send'}
            </span>
          </div>
        ) : (
          <>
            <div className="relative flex-shrink-0">
              <Button
                type="button"
                variant="outline"
                size="icon"
                className="rounded-full"
                aria-label="Add emoji"
                aria-expanded={emojiPickerOpen}
                disabled={disabled || sending}
                onClick={() => setEmojiPickerOpen((open) => !open)}
              >
                <Smile size={18} />
              </Button>
              {emojiPickerOpen && (
                <div
                  role="dialog"
                  aria-label="Emoji picker"
                  className="absolute bottom-12 left-0 z-20 grid w-56 grid-cols-6 gap-1 rounded-lg border border-border bg-popover p-2 text-popover-foreground shadow-lg"
                >
                  {COMPOSER_EMOJIS.map((emoji) => (
                    <button
                      key={emoji}
                      type="button"
                      className="flex h-8 w-8 items-center justify-center rounded-md text-lg hover:bg-accent focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                      aria-label={`Insert ${emoji}`}
                      onClick={() => insertEmoji(emoji)}
                    >
                      {emoji}
                    </button>
                  ))}
                </div>
              )}
            </div>
            {voiceConversation && (
              <>
                <input
                  ref={fileInputRef}
                  type="file"
                  accept="image/jpeg,image/png,image/webp,image/avif"
                  className="sr-only"
                  aria-label="Choose image"
                  onChange={(event) => {
                    const file = event.target.files?.[0];
                    if (!file) return;
                    setImageFile(file);
                    void prepareImage(file, imageSize);
                  }}
                />
                <Button
                  type="button"
                  variant="outline"
                  size="icon"
                  className="flex-shrink-0 rounded-full"
                  disabled={disabled || sending || imagePreparing || imageSending}
                  aria-label="Attach image"
                  onClick={() => fileInputRef.current?.click()}
                >
                  <ImagePlus size={18} />
                </Button>
                <Button
                  type="button"
                  variant="outline"
                  size="icon"
                  className="flex-shrink-0 touch-none rounded-full"
                  disabled={disabled || voiceSending}
                  aria-label="Hold to record voice message"
                  onPointerDown={startVoice}
                  onPointerUp={() => {
                    voiceHeldRef.current = false;
                    void finishVoice();
                  }}
                  onPointerCancel={() => {
                    voiceHeldRef.current = false;
                    void finishVoice(true);
                  }}
                  onPointerMove={(event) => {
                    const bounds = event.currentTarget.getBoundingClientRect();
                    const movedAway =
                      event.clientY < bounds.top - 60 ||
                      event.clientY > bounds.bottom + 60 ||
                      event.clientX < bounds.left - 60 ||
                      event.clientX > bounds.right + 60;
                    if (recording && movedAway) {
                      cancelVoiceRef.current = true;
                      setCancelVoice(true);
                    }
                  }}
                >
                  <Mic size={18} />
                </Button>
              </>
            )}
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
          </>
        )}
        <Button
          type="submit"
          disabled={disabled || sending || recording || !canSubmit}
          className="flex-shrink-0"
        >
          {sending ? 'Sending...' : 'Send'}
        </Button>
      </div>
      {recording && !cancelVoice && (
        <div className="text-center text-xs text-muted-foreground">Slide up to cancel</div>
      )}
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
