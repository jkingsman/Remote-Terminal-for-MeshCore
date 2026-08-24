import { useSyncExternalStore } from 'react';

import type { BotLogEntry } from '../types';

/**
 * Live bot-engine log lines, held outside React like the raw packet stream:
 * only the Bots view's Logs tab reads them, so log chatter never re-renders
 * the app tree. Seeded from GET /api/bots/logs on first open, then appended
 * from `bot_log` WebSocket events.
 */

export const MAX_BOT_LOG_ENTRIES = 500;

let entries: BotLogEntry[] = [];
let seeded = false;
const listeners = new Set<() => void>();

function subscribe(listener: () => void): () => void {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

function emit(): void {
  for (const listener of listeners) {
    listener();
  }
}

export function recordBotLog(entry: BotLogEntry): void {
  entries = [...entries, entry];
  if (entries.length > MAX_BOT_LOG_ENTRIES) {
    entries = entries.slice(entries.length - MAX_BOT_LOG_ENTRIES);
  }
  emit();
}

/** Replace the buffer with the REST snapshot (deduped against live arrivals). */
export function seedBotLogs(snapshot: BotLogEntry[]): void {
  if (seeded && entries.length >= snapshot.length) {
    return;
  }
  const seen = new Set(entries.map((e) => `${e.timestamp}|${e.source}|${e.message}`));
  const merged = [
    ...snapshot.filter((e) => !seen.has(`${e.timestamp}|${e.source}|${e.message}`)),
    ...entries,
  ];
  merged.sort((a, b) => a.timestamp - b.timestamp);
  entries = merged.slice(Math.max(0, merged.length - MAX_BOT_LOG_ENTRIES));
  seeded = true;
  emit();
}

export function clearBotLogs(): void {
  entries = [];
  emit();
}

export function getBotLogs(): BotLogEntry[] {
  return entries;
}

export function useBotLogs(): BotLogEntry[] {
  return useSyncExternalStore(subscribe, getBotLogs, getBotLogs);
}

/** Test-only reset. */
export function resetBotLogStore(): void {
  entries = [];
  seeded = false;
  emit();
}
