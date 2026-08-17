import { useState, useCallback, useRef, useEffect, type FormEvent } from 'react';
import { Button } from '../ui/button';
import { Input } from '../ui/input';

export function ConsolePane({
  history,
  loading,
  onSend,
}: {
  history: Array<{ command: string; response: string; timestamp: number; outgoing: boolean }>;
  loading: boolean;
  onSend: (command: string) => Promise<void>;
}) {
  const [input, setInput] = useState('');
  const outputRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  const prevLoadingRef = useRef(loading);

  // Auto-scroll to bottom on new entries
  useEffect(() => {
    if (outputRef.current) {
      outputRef.current.scrollTop = outputRef.current.scrollHeight;
    }
  }, [history]);

  // Refocus input after command completes
  useEffect(() => {
    if (prevLoadingRef.current && !loading) {
      inputRef.current?.focus();
    }
    prevLoadingRef.current = loading;
  }, [loading]);

  const handleSubmit = useCallback(
    async (e: FormEvent) => {
      e.preventDefault();
      const trimmed = input.trimStart();
      if (!trimmed || loading) return;
      setInput('');
      await onSend(trimmed);
    },
    [input, loading, onSend]
  );

  return (
    <div className="border border-border rounded-lg overflow-hidden col-span-full">
      <div className="px-3 py-2 bg-muted/50 border-b border-border">
        <h3 className="text-sm font-medium">Console</h3>
      </div>
      <div
        ref={outputRef}
        className="h-48 overflow-y-auto p-3 font-mono text-xs bg-console-bg/50 text-console space-y-1"
      >
        {history.length === 0 && (
          <p className="text-muted-foreground italic">Type a CLI command below...</p>
        )}
        {history.map((entry, i) =>
          entry.outgoing ? (
            <div key={i} className="text-console-command">
              &gt; {entry.command}
            </div>
          ) : (
            <div key={i} className="text-console/80 whitespace-pre-wrap">
              {entry.response}
            </div>
          )
        )}
        {loading && <div className="text-muted-foreground animate-pulse">...</div>}
      </div>
      <form onSubmit={handleSubmit} className="flex gap-2 p-2 border-t border-border">
        <Input
          ref={inputRef}
          type="text"
          autoComplete="off"
          autoCapitalize="none"
          name="console-input"
          value={input}
          onChange={(e) => setInput(e.target.value)}
          placeholder="CLI command..."
          aria-label="Console command"
          disabled={loading}
          className="flex-1 font-mono text-sm"
        />
        <Button type="submit" size="sm" disabled={loading || !input.trimStart()}>
          Send
        </Button>
      </form>
    </div>
  );
}
