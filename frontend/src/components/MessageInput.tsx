import { useState, useCallback, useImperativeHandle, forwardRef, useRef, type FormEvent, type KeyboardEvent } from 'react';
import { Input } from './ui/input';
import { Button } from './ui/button';

interface MessageInputProps {
  onSend: (text: string) => Promise<void>;
  disabled: boolean;
  placeholder?: string;
  /** When true, input becomes password field for repeater telemetry */
  isRepeaterMode?: boolean;
}

export interface MessageInputHandle {
  appendText: (text: string) => void;
}

export const MessageInput = forwardRef<MessageInputHandle, MessageInputProps>(
  function MessageInput({ onSend, disabled, placeholder, isRepeaterMode }, ref) {
  const [text, setText] = useState('');
  const [sending, setSending] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);

  useImperativeHandle(ref, () => ({
    appendText: (appendedText: string) => {
      setText((prev) => prev + appendedText);
      // Focus the input after appending
      inputRef.current?.focus();
    },
  }));

  const handleSubmit = useCallback(
    async (e: FormEvent) => {
      e.preventDefault();
      const trimmed = text.trim();

      // For repeater mode, allow empty password via "."
      if (isRepeaterMode) {
        if (sending || disabled) return;
        // "." means empty password
        const password = trimmed === '.' ? '' : trimmed;
        setSending(true);
        try {
          await onSend(password);
          setText('');
        } catch (err) {
          console.error('Failed to request telemetry:', err);
          return;
        } finally {
          setSending(false);
        }
        // Refocus after React re-enables the input (now in CLI command mode)
        setTimeout(() => inputRef.current?.focus(), 0);
      } else {
        if (!trimmed || sending || disabled) return;
        setSending(true);
        try {
          await onSend(trimmed);
          setText('');
        } catch (err) {
          console.error('Failed to send message:', err);
          return;
        } finally {
          setSending(false);
        }
        // Refocus after React re-enables the input
        setTimeout(() => inputRef.current?.focus(), 0);
      }
    },
    [text, sending, disabled, onSend, isRepeaterMode]
  );

  const handleKeyDown = useCallback(
    (e: KeyboardEvent<HTMLInputElement>) => {
      if (e.key === 'Enter' && !e.shiftKey) {
        e.preventDefault();
        handleSubmit(e as unknown as FormEvent);
      }
    },
    [handleSubmit]
  );

  // For repeater mode, enable submit if there's text OR if it's just "." for empty password
  const canSubmit = isRepeaterMode
    ? text.trim().length > 0 || text === '.'
    : text.trim().length > 0;

  return (
    <form className="px-4 py-3 border-t border-border flex gap-2" onSubmit={handleSubmit}>
      <Input
        ref={inputRef}
        type={isRepeaterMode ? 'password' : 'text'}
        value={text}
        onChange={(e) => setText(e.target.value)}
        onKeyDown={handleKeyDown}
        placeholder={placeholder || (isRepeaterMode ? 'Enter password (or . for none)...' : 'Type a message...')}
        disabled={disabled || sending}
        className="flex-1 min-w-0"
      />
      <Button type="submit" disabled={disabled || sending || !canSubmit} className="flex-shrink-0">
        {sending
          ? (isRepeaterMode ? 'Fetching...' : 'Sending...')
          : (isRepeaterMode ? 'Fetch' : 'Send')}
      </Button>
    </form>
  );
});
