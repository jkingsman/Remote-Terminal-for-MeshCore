/**
 * Parse sender from channel message text.
 * Channel messages have format "sender: message".
 */
export function parseSenderFromText(text: string): { sender: string | null; content: string } {
  const colonIndex = text.indexOf(': ');
  if (colonIndex > 0 && colonIndex < 50) {
    const potentialSender = text.substring(0, colonIndex);
    // Check for colon in potential sender (would indicate it's not a simple name)
    if (!potentialSender.includes(':')) {
      return {
        sender: potentialSender,
        content: text.substring(colonIndex + 2),
      };
    }
  }
  return { sender: null, content: text };
}

/**
 * Format a Unix timestamp to a time string.
 * Shows date for messages not from today.
 */
export function formatTime(timestamp: number): string {
  const date = new Date(timestamp * 1000);
  const now = new Date();
  const isToday = date.toDateString() === now.toDateString();

  const time = date.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });

  if (isToday) {
    return time;
  }

  // Show short date for older messages
  const dateStr = date.toLocaleDateString([], { month: 'short', day: 'numeric' });
  return `${dateStr} ${time}`;
}
