import { useEffect, useState } from 'react';
import { Loader2, Sparkles, X } from 'lucide-react';
import { api } from '../api';
import { Button } from './ui/button';

export interface ChannelUnreadSummaryRequest {
  channelId: string;
  after: number;
}

interface ChannelUnreadSummaryBannerProps {
  request: ChannelUnreadSummaryRequest | null;
}

export function ChannelUnreadSummaryBanner({ request }: ChannelUnreadSummaryBannerProps) {
  const [summary, setSummary] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [dismissed, setDismissed] = useState(false);
  const [messageCount, setMessageCount] = useState(0);

  useEffect(() => {
    if (!request) {
      setSummary(null);
      setError(null);
      setLoading(false);
      setDismissed(false);
      setMessageCount(0);
      return;
    }

    let cancelled = false;
    setSummary(null);
    setError(null);
    setDismissed(false);
    setLoading(true);
    setMessageCount(0);

    api
      .summarizeChannelUnread(request.channelId, request.after)
      .then((result) => {
        if (cancelled) return;
        setMessageCount(result.message_count);
        if (result.skipped || !result.summary) {
          setSummary(null);
          setError(null);
          return;
        }
        setSummary(result.summary);
      })
      .catch((err) => {
        if (cancelled) return;
        setSummary(null);
        setError(err instanceof Error ? err.message : 'Failed to summarize unread messages');
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });

    return () => {
      cancelled = true;
    };
  }, [request]);

  if (!request || dismissed) return null;
  if (!loading && !summary && !error) return null;

  return (
    <div className="mx-4 mt-3 rounded-md border border-primary/25 bg-primary/5 px-3 py-2.5 text-sm">
      <div className="flex items-start gap-2">
        <Sparkles className="mt-0.5 h-4 w-4 shrink-0 text-primary" aria-hidden="true" />
        <div className="min-w-0 flex-1 space-y-1">
          <div className="flex items-center justify-between gap-2">
            <p className="font-medium text-foreground">
              Unread summary
              {messageCount > 0 ? (
                <span className="ml-1.5 font-normal text-muted-foreground">
                  ({messageCount} message{messageCount === 1 ? '' : 's'})
                </span>
              ) : null}
            </p>
            <Button
              type="button"
              variant="ghost"
              size="icon"
              className="h-6 w-6 shrink-0 text-muted-foreground"
              onClick={() => setDismissed(true)}
              aria-label="Dismiss unread summary"
            >
              <X className="h-3.5 w-3.5" />
            </Button>
          </div>
          {loading ? (
            <p className="flex items-center gap-2 text-muted-foreground">
              <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden="true" />
              Summarizing unread messages…
            </p>
          ) : error ? (
            <p className="text-destructive">{error}</p>
          ) : (
            <p className="whitespace-pre-wrap text-foreground/90">{summary}</p>
          )}
        </div>
      </div>
    </div>
  );
}
