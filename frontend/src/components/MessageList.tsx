import { useEffect, useLayoutEffect, useRef, useCallback, useState, type ReactNode } from 'react';
import type { Contact, Message } from '../types';
import { formatTime, parseSenderFromText } from '../utils/messageParser';
import { pubkeysMatch } from '../utils/pubkey';
import { ContactAvatar } from './ContactAvatar';
import { cn } from '@/lib/utils';

interface MessageListProps {
  messages: Message[];
  contacts: Contact[];
  loading: boolean;
  loadingOlder?: boolean;
  hasOlderMessages?: boolean;
  onSenderClick?: (sender: string) => void;
  onLoadOlder?: () => void;
  radioName?: string;
}

// Helper to render text with highlighted @[Name] mentions
function renderTextWithMentions(text: string, radioName?: string): ReactNode {
  if (!radioName) return text;

  const mentionPattern = /@\[([^\]]+)\]/g;
  const parts: ReactNode[] = [];
  let lastIndex = 0;
  let match: RegExpExecArray | null;
  let keyIndex = 0;

  while ((match = mentionPattern.exec(text)) !== null) {
    // Add text before the match
    if (match.index > lastIndex) {
      parts.push(text.slice(lastIndex, match.index));
    }

    const mentionedName = match[1];
    const isOwnMention = mentionedName === radioName;

    parts.push(
      <span
        key={keyIndex++}
        className={cn(
          "rounded px-0.5",
          isOwnMention
            ? "bg-primary/30 text-primary font-medium"
            : "bg-muted-foreground/20"
        )}
      >
        @[{mentionedName}]
      </span>
    );

    lastIndex = match.index + match[0].length;
  }

  // Add remaining text after last match
  if (lastIndex < text.length) {
    parts.push(text.slice(lastIndex));
  }

  return parts.length > 0 ? parts : text;
}

export function MessageList({
  messages,
  contacts,
  loading,
  loadingOlder = false,
  hasOlderMessages = false,
  onSenderClick,
  onLoadOlder,
  radioName,
}: MessageListProps) {
  const listRef = useRef<HTMLDivElement>(null);
  const prevMessagesLengthRef = useRef<number>(0);
  const isInitialLoadRef = useRef<boolean>(true);
  const [showScrollToBottom, setShowScrollToBottom] = useState(false);

  // Capture scroll state in the scroll handler BEFORE any state updates
  const scrollStateRef = useRef({
    scrollTop: 0,
    scrollHeight: 0,
    wasNearTop: false,
  });

  // Handle scroll position AFTER render
  useLayoutEffect(() => {
    if (!listRef.current) return;

    const list = listRef.current;
    const messagesAdded = messages.length - prevMessagesLengthRef.current;

    if (isInitialLoadRef.current && messages.length > 0) {
      // Initial load - scroll to bottom
      list.scrollTop = list.scrollHeight;
      isInitialLoadRef.current = false;
    } else if (messagesAdded > 0 && prevMessagesLengthRef.current > 0) {
      // Messages were added - use scroll state captured before the update
      const scrollHeightDiff = list.scrollHeight - scrollStateRef.current.scrollHeight;

      if (scrollStateRef.current.wasNearTop && scrollHeightDiff > 0) {
        // User was near top (loading older) - preserve position by adding the height diff
        list.scrollTop = scrollStateRef.current.scrollTop + scrollHeightDiff;
      } else if (!scrollStateRef.current.wasNearTop) {
        // User was at bottom - scroll to bottom for new messages
        list.scrollTop = list.scrollHeight;
      }
    }

    prevMessagesLengthRef.current = messages.length;
  }, [messages]);

  // Reset initial load flag when conversation changes (messages becomes empty then filled)
  useEffect(() => {
    if (messages.length === 0) {
      isInitialLoadRef.current = true;
      prevMessagesLengthRef.current = 0;
      scrollStateRef.current = { scrollTop: 0, scrollHeight: 0, wasNearTop: false };
    }
  }, [messages.length]);

  // Handle scroll - capture state and detect when user is near top/bottom
  const handleScroll = useCallback(() => {
    if (!listRef.current) return;

    const { scrollTop, scrollHeight, clientHeight } = listRef.current;

    // Always capture current scroll state (needed for scroll preservation)
    scrollStateRef.current = {
      scrollTop,
      scrollHeight,
      wasNearTop: scrollTop < 150,
    };

    // Show scroll-to-bottom button when not near the bottom (more than 100px away)
    const distanceFromBottom = scrollHeight - scrollTop - clientHeight;
    setShowScrollToBottom(distanceFromBottom > 100);

    if (!onLoadOlder || loadingOlder || !hasOlderMessages) return;

    // Trigger load when within 100px of top
    if (scrollTop < 100) {
      onLoadOlder();
    }
  }, [onLoadOlder, loadingOlder, hasOlderMessages]);

  // Scroll to bottom handler
  const scrollToBottom = useCallback(() => {
    if (listRef.current) {
      listRef.current.scrollTop = listRef.current.scrollHeight;
    }
  }, []);

  // Look up contact by public key or prefix
  const getContact = (conversationKey: string | null): Contact | null => {
    if (!conversationKey) return null;
    return contacts.find(c => pubkeysMatch(c.public_key, conversationKey)) || null;
  };

  // Look up contact by name (for channel messages where we parse sender from text)
  const getContactByName = (name: string): Contact | null => {
    return contacts.find(c => c.name === name) || null;
  };

  if (loading) {
    return <div className="flex-1 overflow-y-auto p-5 text-center text-muted-foreground">Loading messages...</div>;
  }

  if (messages.length === 0) {
    return <div className="flex-1 overflow-y-auto p-5 text-center text-muted-foreground">No messages yet</div>;
  }

  // Deduplicate messages by content + timestamp (same message via different paths)
  const deduplicatedMessages = messages.reduce<Message[]>((acc, msg) => {
    const key = `${msg.type}-${msg.conversation_key}-${msg.text}-${msg.sender_timestamp}`;
    const existing = acc.find(m =>
      `${m.type}-${m.conversation_key}-${m.text}-${m.sender_timestamp}` === key
    );
    if (!existing) {
      acc.push(msg);
    }
    return acc;
  }, []);

  // Sort messages by received_at ascending (oldest first)
  const sortedMessages = [...deduplicatedMessages].sort(
    (a, b) => a.received_at - b.received_at
  );

  // Helper to get a unique sender key for grouping messages
  const getSenderKey = (msg: Message, sender: string | null): string => {
    if (msg.outgoing) return '__outgoing__';
    if (msg.type === 'PRIV' && msg.conversation_key) return msg.conversation_key;
    return sender || '__unknown__';
  };

  return (
    <div className="flex-1 overflow-hidden relative">
      <div className="h-full overflow-y-auto p-4 flex flex-col gap-0.5" ref={listRef} onScroll={handleScroll}>
      {loadingOlder && (
        <div className="text-center py-2 text-muted-foreground text-sm">
          Loading older messages...
        </div>
      )}
      {!loadingOlder && hasOlderMessages && (
        <div className="text-center py-2 text-muted-foreground text-xs">
          Scroll up for older messages
        </div>
      )}
      {sortedMessages.map((msg, index) => {
        const { sender, content } = parseSenderFromText(msg.text);
        // For DMs, look up contact; for channel messages, use parsed sender
        const contact = msg.type === 'PRIV' ? getContact(msg.conversation_key) : null;
        const displaySender = msg.outgoing
          ? 'You'
          : contact?.name || sender || msg.conversation_key?.slice(0, 8) || 'Unknown';

        const canClickSender = !msg.outgoing && onSenderClick && displaySender !== 'Unknown';

        // Determine if we should show avatar (first message in a chunk from same sender)
        const currentSenderKey = getSenderKey(msg, sender);
        const prevMsg = sortedMessages[index - 1];
        const prevSenderKey = prevMsg ? getSenderKey(prevMsg, parseSenderFromText(prevMsg.text).sender) : null;
        const showAvatar = !msg.outgoing && currentSenderKey !== prevSenderKey;
        const isFirstMessage = index === 0;

        // Get avatar info for incoming messages
        let avatarName: string | null = null;
        let avatarKey: string = '';
        if (!msg.outgoing) {
          if (msg.type === 'PRIV' && msg.conversation_key) {
            // DM: use conversation_key (sender's public key)
            avatarName = contact?.name || null;
            avatarKey = msg.conversation_key;
          } else if (sender) {
            // Channel message: try to find contact by name, or use sender name as pseudo-key
            const senderContact = getContactByName(sender);
            avatarName = sender;
            avatarKey = senderContact?.public_key || `name:${sender}`;
          }
        }

        return (
          <div
            key={msg.id}
            className={cn(
              "flex items-start max-w-[85%]",
              msg.outgoing && "flex-row-reverse self-end",
              showAvatar && !isFirstMessage && "mt-3"
            )}
          >
            {!msg.outgoing && (
              <div className="w-10 flex-shrink-0 flex items-start pt-0.5">
                {showAvatar && avatarKey && (
                  <ContactAvatar name={avatarName} publicKey={avatarKey} size={32} />
                )}
              </div>
            )}
            <div className={cn(
              "py-1.5 px-3 rounded-lg min-w-0",
              msg.outgoing ? "bg-[#1e3a29]" : "bg-muted"
            )}>
              {showAvatar && (
                <div className="text-[13px] font-semibold text-muted-foreground mb-0.5">
                  {canClickSender ? (
                    <span
                      className="cursor-pointer hover:text-primary hover:underline"
                      onClick={() => onSenderClick(displaySender)}
                      title={`Mention ${displaySender}`}
                    >
                      {displaySender}
                    </span>
                  ) : (
                    displaySender
                  )}
                  <span className="font-normal text-muted-foreground/70 ml-2 text-[11px]">
                    {formatTime(msg.sender_timestamp || msg.received_at)}
                  </span>
                </div>
              )}
              <div className="break-words whitespace-pre-wrap">
                {content.split('\n').map((line, i, arr) => (
                  <span key={i}>
                    {renderTextWithMentions(line, radioName)}
                    {i < arr.length - 1 && <br />}
                  </span>
                ))}
                {!showAvatar && (
                  <span className="text-[10px] text-muted-foreground/50 ml-2">
                    {formatTime(msg.sender_timestamp || msg.received_at)}
                  </span>
                )}
                {msg.outgoing && (msg.acked ? ' ✓' : ' ?')}
              </div>
            </div>
          </div>
        );
      })}
      </div>

      {/* Scroll to bottom button */}
      {showScrollToBottom && (
        <button
          onClick={scrollToBottom}
          className="absolute bottom-4 right-4 w-10 h-10 rounded-full bg-muted hover:bg-accent border border-border flex items-center justify-center shadow-lg transition-opacity"
          title="Scroll to bottom"
        >
          <svg
            xmlns="http://www.w3.org/2000/svg"
            width="20"
            height="20"
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            strokeWidth="2"
            strokeLinecap="round"
            strokeLinejoin="round"
            className="text-muted-foreground"
          >
            <polyline points="6 9 12 15 18 9" />
          </svg>
        </button>
      )}
    </div>
  );
}
