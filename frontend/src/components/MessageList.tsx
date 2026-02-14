import {
  useEffect,
  useLayoutEffect,
  useRef,
  useCallback,
  useMemo,
  useState,
  type ReactNode,
} from 'react';
import type { Contact, Message, MessagePath, RadioConfig } from '../types';
import { CONTACT_TYPE_REPEATER } from '../types';
import { formatTime, parseSenderFromText } from '../utils/messageParser';
import { formatHopCounts, type SenderInfo } from '../utils/pathUtils';
import { ContactAvatar } from './ContactAvatar';
import { PathModal } from './PathModal';
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
  config?: RadioConfig | null;
  onResendMessage?: (messageId: number) => void;
}

// URL regex for linkifying plain text
const URL_PATTERN =
  /https?:\/\/(www\.)?[-a-zA-Z0-9@:%._+~#=]{1,256}\.[a-zA-Z0-9()]{1,6}\b([-a-zA-Z0-9()@:%_+.~#?&//=]*)/g;

// Helper to convert URLs in a plain text string into clickable links
function linkifyText(text: string, keyPrefix: string): ReactNode[] {
  const parts: ReactNode[] = [];
  let lastIndex = 0;
  let match: RegExpExecArray | null;
  let keyIndex = 0;

  URL_PATTERN.lastIndex = 0;
  while ((match = URL_PATTERN.exec(text)) !== null) {
    if (match.index > lastIndex) {
      parts.push(text.slice(lastIndex, match.index));
    }
    parts.push(
      <a
        key={`${keyPrefix}-link-${keyIndex++}`}
        href={match[0]}
        target="_blank"
        rel="noopener noreferrer"
        className="text-primary underline hover:text-primary/80"
      >
        {match[0]}
      </a>
    );
    lastIndex = match.index + match[0].length;
  }

  if (lastIndex === 0) return [text];
  if (lastIndex < text.length) {
    parts.push(text.slice(lastIndex));
  }
  return parts;
}

// Helper to render text with highlighted @[Name] mentions and clickable URLs
function renderTextWithMentions(text: string, radioName?: string): ReactNode {
  const mentionPattern = /@\[([^\]]+)\]/g;
  const parts: ReactNode[] = [];
  let lastIndex = 0;
  let match: RegExpExecArray | null;
  let keyIndex = 0;

  while ((match = mentionPattern.exec(text)) !== null) {
    // Add text before the match (with linkification)
    if (match.index > lastIndex) {
      parts.push(...linkifyText(text.slice(lastIndex, match.index), `pre-${keyIndex}`));
    }

    const mentionedName = match[1];
    const isOwnMention = radioName ? mentionedName === radioName : false;

    parts.push(
      <span
        key={`mention-${keyIndex++}`}
        className={cn(
          'rounded px-0.5',
          isOwnMention ? 'bg-primary/30 text-primary font-medium' : 'bg-muted-foreground/20'
        )}
      >
        @[{mentionedName}]
      </span>
    );

    lastIndex = match.index + match[0].length;
  }

  // Add remaining text after last match (with linkification)
  if (lastIndex < text.length) {
    parts.push(...linkifyText(text.slice(lastIndex), `post-${keyIndex}`));
  }

  return parts.length > 0 ? parts : text;
}

// Clickable hop count badge that opens the path modal
interface HopCountBadgeProps {
  paths: MessagePath[];
  onClick: () => void;
  variant: 'header' | 'inline';
}

function HopCountBadge({ paths, onClick, variant }: HopCountBadgeProps) {
  const hopInfo = formatHopCounts(paths);
  const label = `(${hopInfo.display})`;

  const className =
    variant === 'header'
      ? 'font-normal text-muted-foreground ml-1 text-[11px] cursor-pointer hover:text-primary hover:underline'
      : 'text-[10px] text-muted-foreground ml-1 cursor-pointer hover:text-primary hover:underline';

  return (
    <span
      className={className}
      onClick={(e) => {
        e.stopPropagation();
        onClick();
      }}
      title="View message path"
    >
      {label}
    </span>
  );
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
  config,
  onResendMessage,
}: MessageListProps) {
  const listRef = useRef<HTMLDivElement>(null);
  const prevMessagesLengthRef = useRef<number>(0);
  const isInitialLoadRef = useRef<boolean>(true);
  const [showScrollToBottom, setShowScrollToBottom] = useState(false);
  const [resendPromptMessageId, setResendPromptMessageId] = useState<number | null>(null);
  const [selectedPath, setSelectedPath] = useState<{
    paths: MessagePath[];
    senderInfo: SenderInfo;
    messageId?: number;
    canSendAgain?: boolean;
  } | null>(null);

  // Capture scroll state in the scroll handler BEFORE any state updates
  const scrollStateRef = useRef({
    scrollTop: 0,
    scrollHeight: 0,
    clientHeight: 0,
    wasNearTop: false,
    wasNearBottom: true, // Default to true so initial messages scroll to bottom
  });

  // Track conversation key to detect when entire message set changes
  const prevConvKeyRef = useRef<string | null>(null);

  // Handle scroll position AFTER render
  useLayoutEffect(() => {
    if (!listRef.current) return;

    const list = listRef.current;
    const messagesAdded = messages.length - prevMessagesLengthRef.current;

    // Detect if messages are from a different conversation (handles the case where
    // the key prop remount consumes isInitialLoadRef on stale data from the previous
    // conversation before the cache restore effect sets the correct messages)
    const convKey = messages.length > 0 ? messages[0].conversation_key : null;
    const conversationChanged = convKey !== null && convKey !== prevConvKeyRef.current;
    if (convKey !== null) prevConvKeyRef.current = convKey;

    if ((isInitialLoadRef.current || conversationChanged) && messages.length > 0) {
      // Initial load or conversation switch - scroll to bottom
      list.scrollTop = list.scrollHeight;
      isInitialLoadRef.current = false;
    } else if (messagesAdded > 0 && prevMessagesLengthRef.current > 0) {
      // Messages were added - use scroll state captured before the update
      const scrollHeightDiff = list.scrollHeight - scrollStateRef.current.scrollHeight;

      if (scrollStateRef.current.wasNearTop && scrollHeightDiff > 0) {
        // User was near top (loading older) - preserve position by adding the height diff
        list.scrollTop = scrollStateRef.current.scrollTop + scrollHeightDiff;
      } else if (scrollStateRef.current.wasNearBottom) {
        // User was near bottom - scroll to bottom for new messages (including sent)
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
      prevConvKeyRef.current = null;
      scrollStateRef.current = {
        scrollTop: 0,
        scrollHeight: 0,
        clientHeight: 0,
        wasNearTop: false,
        wasNearBottom: true,
      };
    }
  }, [messages.length]);

  // Handle scroll - capture state and detect when user is near top/bottom
  const handleScroll = useCallback(() => {
    if (!listRef.current) return;

    const { scrollTop, scrollHeight, clientHeight } = listRef.current;
    const distanceFromBottom = scrollHeight - scrollTop - clientHeight;

    // Always capture current scroll state (needed for scroll preservation)
    scrollStateRef.current = {
      scrollTop,
      scrollHeight,
      clientHeight,
      wasNearTop: scrollTop < 150,
      wasNearBottom: distanceFromBottom < 100,
    };

    // Show scroll-to-bottom button when not near the bottom (more than 100px away)
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

  // Sort messages by received_at ascending (oldest first)
  // Note: Deduplication is handled by useConversationMessages.addMessageIfNew()
  // and the database UNIQUE constraint on (type, conversation_key, text, sender_timestamp)
  const sortedMessages = useMemo(
    () => [...messages].sort((a, b) => a.received_at - b.received_at),
    [messages]
  );

  // Look up contact by public key
  const getContact = (conversationKey: string | null): Contact | null => {
    if (!conversationKey) return null;
    return contacts.find((c) => c.public_key === conversationKey) || null;
  };

  // Look up contact by name (for channel messages where we parse sender from text)
  const getContactByName = (name: string): Contact | null => {
    return contacts.find((c) => c.name === name) || null;
  };

  // Build sender info for path modal
  const getSenderInfo = (
    msg: Message,
    contact: Contact | null,
    parsedSender: string | null
  ): SenderInfo => {
    if (msg.type === 'PRIV' && contact) {
      return {
        name: contact.name || contact.public_key.slice(0, 12),
        publicKeyOrPrefix: contact.public_key,
        lat: contact.lat,
        lon: contact.lon,
      };
    }
    // For channel messages, try to find contact by parsed sender name
    if (parsedSender) {
      const senderContact = getContactByName(parsedSender);
      if (senderContact) {
        return {
          name: parsedSender,
          publicKeyOrPrefix: senderContact.public_key,
          lat: senderContact.lat,
          lon: senderContact.lon,
        };
      }
    }
    // Fallback: unknown sender
    return {
      name: parsedSender || 'Unknown',
      publicKeyOrPrefix: msg.conversation_key || '',
      lat: null,
      lon: null,
    };
  };

  const openResendPrompt = useCallback((messageId: number) => {
    setResendPromptMessageId(messageId);
  }, []);

  const handleResendConfirm = useCallback(
    (messageId: number) => {
      if (!onResendMessage) return;
      onResendMessage(messageId);
      setResendPromptMessageId(null);
    },
    [onResendMessage]
  );

  const clearResendPrompt = useCallback(() => {
    setResendPromptMessageId(null);
  }, []);

  if (loading) {
    return (
      <div className="flex-1 overflow-y-auto p-5 text-center text-muted-foreground">
        Loading messages...
      </div>
    );
  }

  if (messages.length === 0) {
    return (
      <div className="flex-1 overflow-y-auto p-5 text-center text-muted-foreground">
        No messages yet
      </div>
    );
  }

  // Helper to get a unique sender key for grouping messages
  const getSenderKey = (msg: Message, sender: string | null): string => {
    if (msg.outgoing) return '__outgoing__';
    if (msg.type === 'PRIV' && msg.conversation_key) return msg.conversation_key;
    return sender || '__unknown__';
  };

  return (
    <div className="flex-1 overflow-hidden relative">
      <div
        className="h-full overflow-y-auto p-4 flex flex-col gap-0.5"
        ref={listRef}
        onScroll={handleScroll}
      >
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
          // For DMs, look up contact; for channel messages, use parsed sender
          const contact = msg.type === 'PRIV' ? getContact(msg.conversation_key) : null;
          const isRepeater = contact?.type === CONTACT_TYPE_REPEATER;

          // Skip sender parsing for repeater messages (CLI responses often have colons)
          const { sender, content } = isRepeater
            ? { sender: null, content: msg.text }
            : parseSenderFromText(msg.text);
          const displaySender = msg.outgoing
            ? 'You'
            : contact?.name || sender || msg.conversation_key?.slice(0, 8) || 'Unknown';

          const canClickSender = !msg.outgoing && onSenderClick && displaySender !== 'Unknown';

          // Determine if we should show avatar (first message in a chunk from same sender)
          const currentSenderKey = getSenderKey(msg, sender);
          const prevMsg = sortedMessages[index - 1];
          const prevSenderKey = prevMsg
            ? getSenderKey(prevMsg, parseSenderFromText(prevMsg.text).sender)
            : null;
          const isFirstInGroup = currentSenderKey !== prevSenderKey;
          const showAvatar = !msg.outgoing && isFirstInGroup;
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

          const showResendPrompt = resendPromptMessageId === msg.id;

          return (
            <div
              key={msg.id}
              className={cn(
                'flex items-start max-w-[85%]',
                msg.outgoing && 'flex-row-reverse self-end',
                isFirstInGroup && !isFirstMessage && 'mt-3'
              )}
            >
              {!msg.outgoing && (
                <div className="w-10 flex-shrink-0 flex items-start pt-0.5">
                  {showAvatar && avatarKey && (
                    <ContactAvatar name={avatarName} publicKey={avatarKey} size={32} />
                  )}
                </div>
              )}
              <div
                className={cn(
                  'py-1.5 px-3 rounded-lg min-w-0',
                  msg.outgoing ? 'bg-[#1e3a29]' : 'bg-muted'
                )}
              >
                {showAvatar && (
                  <div className="text-[13px] font-bold text-foreground mb-0.5">
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
                    <span className="font-normal text-muted-foreground ml-2 text-[11px]">
                      {formatTime(msg.sender_timestamp || msg.received_at)}
                    </span>
                    {!msg.outgoing && msg.paths && msg.paths.length > 0 && (
                      <HopCountBadge
                        paths={msg.paths}
                        variant="header"
                        onClick={() =>
                          setSelectedPath({
                            paths: msg.paths!,
                            senderInfo: getSenderInfo(msg, contact, sender),
                          })
                        }
                      />
                    )}
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
                    <>
                      <span className="text-[10px] text-muted-foreground ml-2">
                        {formatTime(msg.sender_timestamp || msg.received_at)}
                      </span>
                      {!msg.outgoing && msg.paths && msg.paths.length > 0 && (
                        <HopCountBadge
                          paths={msg.paths}
                          variant="inline"
                          onClick={() =>
                            setSelectedPath({
                              paths: msg.paths!,
                              senderInfo: getSenderInfo(msg, contact, sender),
                            })
                          }
                        />
                      )}
                    </>
                  )}
                  {msg.outgoing &&
                    (() => {
                      const pathCount = msg.paths?.length ?? 0;
                      const canSendAgain = pathCount <= 1;

                      if (msg.acked > 0) {
                        if (msg.paths && msg.paths.length > 0) {
                          return (
                            <span
                              className="cursor-pointer hover:text-primary"
                              onClick={(e) => {
                                e.stopPropagation();
                                setSelectedPath({
                                  paths: msg.paths!,
                                  senderInfo: {
                                    name: config?.name || 'Unknown',
                                    publicKeyOrPrefix: config?.public_key || '',
                                    lat: config?.lat ?? null,
                                    lon: config?.lon ?? null,
                                  },
                                  messageId: msg.id,
                                  canSendAgain,
                                });
                              }}
                              title="View echo paths"
                            >{` ✓${msg.acked > 1 ? msg.acked : ''}`}</span>
                          );
                        }

                        return ` ✓${msg.acked > 1 ? msg.acked : ''}`;
                      }

                      if (canSendAgain && onResendMessage) {
                        return (
                          <span
                            className="cursor-pointer hover:text-primary"
                            onClick={(e) => {
                              e.stopPropagation();
                              openResendPrompt(msg.id);
                            }}
                            title="No repeats heard yet. Try sending again"
                          >
                            {' ?'}
                          </span>
                        );
                      }

                      return <span title="No repeats heard yet"> ?</span>;
                    })()}
                  {showResendPrompt && (
                    <span className="ml-2 inline-flex items-center gap-1 text-[10px] text-muted-foreground">
                      <span>Send again?</span>
                      <button
                        className="px-1.5 py-0.5 rounded border border-border hover:bg-accent hover:text-foreground"
                        onClick={(e) => {
                          e.stopPropagation();
                          handleResendConfirm(msg.id);
                        }}
                      >
                        Yes
                      </button>
                      <button
                        className="px-1.5 py-0.5 rounded border border-border hover:bg-accent hover:text-foreground"
                        onClick={(e) => {
                          e.stopPropagation();
                          clearResendPrompt();
                        }}
                      >
                        No
                      </button>
                    </span>
                  )}
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

      {/* Path modal */}
      {selectedPath && (() => {
        const resendMessageId = selectedPath.messageId;
        const canSendAgainFromModal =
          resendMessageId !== undefined && Boolean(selectedPath.canSendAgain && onResendMessage);

        return (
          <PathModal
            open={true}
            onClose={() => setSelectedPath(null)}
            paths={selectedPath.paths}
            senderInfo={selectedPath.senderInfo}
            contacts={contacts}
            config={config ?? null}
            canSendAgain={canSendAgainFromModal}
            onSendAgain={
              canSendAgainFromModal
                ? () => {
                    setSelectedPath(null);
                    handleResendConfirm(resendMessageId);
                  }
                : undefined
            }
          />
        );
      })()}
    </div>
  );
}
