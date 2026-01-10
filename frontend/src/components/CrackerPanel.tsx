import { useState, useEffect, useRef, useCallback } from 'react';
import { GroupTextCracker, type ProgressReport } from 'meshcore-hashtag-cracker';
import { ENGLISH_WORDLIST } from 'meshcore-hashtag-cracker/wordlist';
import NoSleep from 'nosleep.js';
import type { RawPacket, Channel } from '../types';
import { api } from '../api';
import { cn } from '@/lib/utils';

interface CrackedRoom {
  roomName: string;
  key: string;
  packetId: number;
  message: string;
  crackedAt: number;
}

interface QueueItem {
  packet: RawPacket;
  attempts: number;
  lastAttemptLength: number;
  status: 'pending' | 'cracking' | 'cracked' | 'failed';
}

interface CrackerPanelProps {
  packets: RawPacket[];
  channels: Channel[];
  onChannelCreate: (name: string, key: string) => Promise<void>;
  onRunningChange?: (running: boolean) => void;
}

export function CrackerPanel({ packets, channels, onChannelCreate, onRunningChange }: CrackerPanelProps) {
  const [isRunning, setIsRunning] = useState(false);
  const [maxLength, setMaxLength] = useState(6);
  const [retryFailedAtNextLength, setRetryFailedAtNextLength] = useState(false);
  const [decryptHistorical, setDecryptHistorical] = useState(true);
  const [turboMode, setTurboMode] = useState(false);
  const [progress, setProgress] = useState<ProgressReport | null>(null);
  const [queue, setQueue] = useState<Map<number, QueueItem>>(new Map());
  const [crackedRooms, setCrackedRooms] = useState<CrackedRoom[]>([]);
  const [wordlistLoaded, setWordlistLoaded] = useState(false);
  const [gpuAvailable, setGpuAvailable] = useState<boolean | null>(null);
  const [undecryptedPacketCount, setUndecryptedPacketCount] = useState<number | null>(null);

  const crackerRef = useRef<GroupTextCracker | null>(null);
  const noSleepRef = useRef<NoSleep | null>(null);
  const isRunningRef = useRef(false);
  const abortedRef = useRef(false);
  const isProcessingRef = useRef(false);
  const queueRef = useRef<Map<number, QueueItem>>(new Map());
  const retryFailedRef = useRef(false);
  const maxLengthRef = useRef(6);
  const decryptHistoricalRef = useRef(true);
  const turboModeRef = useRef(false);
  const undecryptedIdsRef = useRef<Set<number>>(new Set());

  // Initialize cracker and NoSleep
  useEffect(() => {
    const cracker = new GroupTextCracker();
    crackerRef.current = cracker;
    setGpuAvailable(cracker.isGpuAvailable());

    const noSleep = new NoSleep();
    noSleepRef.current = noSleep;

    // Use built-in wordlist
    cracker.setWordlist(ENGLISH_WORDLIST);
    setWordlistLoaded(true);

    return () => {
      cracker.destroy();
      crackerRef.current = null;
      noSleep.disable();
      noSleepRef.current = null;
    };
  }, []);

  // Fetch undecrypted packet count
  useEffect(() => {
    const fetchCount = () => {
      api.getUndecryptedPacketCount()
        .then(({ count }) => setUndecryptedPacketCount(count))
        .catch(() => setUndecryptedPacketCount(null));
    };
    fetchCount();
    // Refresh periodically
    const interval = setInterval(fetchCount, 30000);
    return () => clearInterval(interval);
  }, []);

  // Get existing channel keys for filtering
  const existingChannelKeys = new Set(channels.map(c => c.key.toUpperCase()));

  // Filter packets to only undecrypted GROUP_TEXT
  const undecryptedGroupText = packets.filter(
    p => p.payload_type === 'GROUP_TEXT' && !p.decrypted
  );

  // Update queue when packets change
  useEffect(() => {
    setQueue(prev => {
      const newQueue = new Map(prev);
      let changed = false;

      for (const packet of undecryptedGroupText) {
        if (!newQueue.has(packet.id)) {
          newQueue.set(packet.id, {
            packet,
            attempts: 0,
            lastAttemptLength: 0,
            status: 'pending',
          });
          changed = true;
        }
      }

      if (changed) {
        queueRef.current = newQueue;
        return newQueue;
      }
      return prev;
    });
  }, [undecryptedGroupText.length]);

  // Keep refs in sync with state
  useEffect(() => {
    queueRef.current = queue;
  }, [queue]);

  useEffect(() => {
    retryFailedRef.current = retryFailedAtNextLength;
  }, [retryFailedAtNextLength]);

  useEffect(() => {
    maxLengthRef.current = maxLength;
  }, [maxLength]);

  useEffect(() => {
    decryptHistoricalRef.current = decryptHistorical;
  }, [decryptHistorical]);

  useEffect(() => {
    turboModeRef.current = turboMode;
  }, [turboMode]);

  // Keep undecrypted IDs ref in sync - used to skip packets already decrypted by other means
  useEffect(() => {
    undecryptedIdsRef.current = new Set(undecryptedGroupText.map(p => p.id));
  }, [undecryptedGroupText]);

  // Notify parent of running state changes
  useEffect(() => {
    onRunningChange?.(isRunning);
  }, [isRunning, onRunningChange]);

  // Stats (cracking count is implicit - if progress is shown, we're cracking one)
  const pendingCount = Array.from(queue.values()).filter(q => q.status === 'pending').length;
  const crackedCount = Array.from(queue.values()).filter(q => q.status === 'cracked').length;
  const failedCount = Array.from(queue.values()).filter(q => q.status === 'failed').length;

  // Process next packet in queue
  const processNext = useCallback(async () => {
    // Prevent concurrent processing
    if (isProcessingRef.current) return;
    if (!crackerRef.current || !isRunningRef.current) return;

    const currentQueue = queueRef.current;

    // Find next pending packet
    let nextItem: QueueItem | null = null;
    let nextId: number | null = null;

    for (const [id, item] of currentQueue.entries()) {
      if (item.status === 'pending') {
        nextItem = item;
        nextId = id;
        break;
      }
    }

    // If no pending and retry option is enabled, pick the failed one with lowest lastAttemptLength
    if (!nextItem && retryFailedRef.current) {
      const failedItems = Array.from(currentQueue.entries()).filter(
        ([, item]) => item.status === 'failed' && item.lastAttemptLength < 10 // Hard cap at length 10
      );
      if (failedItems.length > 0) {
        // Sort by lastAttemptLength ascending and pick the first (lowest)
        failedItems.sort((a, b) => a[1].lastAttemptLength - b[1].lastAttemptLength);
        [nextId, nextItem] = failedItems[0];
      }
    }

    if (!nextItem || nextId === null) {
      // Nothing to process right now, but keep running and check again later
      if (isRunningRef.current) {
        setTimeout(() => processNext(), 1000);
      }
      return;
    }

    // Check if this packet is still undecrypted - it may have been decrypted
    // by historical decrypt when we cracked another packet from the same channel
    if (!undecryptedIdsRef.current.has(nextId)) {
      // Already decrypted by other means, remove from queue and continue
      setQueue(prev => {
        const updated = new Map(prev);
        updated.delete(nextId);
        return updated;
      });
      if (isRunningRef.current) {
        setTimeout(() => processNext(), 10);
      }
      return;
    }

    // Lock processing
    isProcessingRef.current = true;

    const currentMaxLength = maxLengthRef.current;
    const isRetry = nextItem.lastAttemptLength > 0;
    const targetLength = isRetry
      ? nextItem.lastAttemptLength + 1
      : currentMaxLength;

    try {
      const result = await crackerRef.current.crack(
        nextItem.packet.data,
        {
          maxLength: targetLength,
          useTimestampFilter: true,
          useUtf8Filter: true,
          ...(turboModeRef.current && { gpuDispatchMs: 10000 }),
          // For retries, skip dictionary and shorter lengths - we already checked those
          ...(isRetry && { useDictionary: false, startingLength: targetLength }),
        },
        (prog) => {
          setProgress(prog);
        }
      );

      if (abortedRef.current) {
        abortedRef.current = false;
        isProcessingRef.current = false;
        setProgress(null);
        return;
      }

      if (result.found && result.roomName && result.key) {
        // Success!
        setQueue(prev => {
          const updated = new Map(prev);
          const item = updated.get(nextId!);
          if (item) {
            updated.set(nextId!, {
              ...item,
              status: 'cracked',
              attempts: item.attempts + 1,
              lastAttemptLength: targetLength,
            });
          }
          return updated;
        });

        const newRoom: CrackedRoom = {
          roomName: result.roomName,
          key: result.key,
          packetId: nextId!,
          message: result.decryptedMessage || '',
          crackedAt: Date.now(),
        };
        setCrackedRooms(prev => [...prev, newRoom]);

        // Auto-add channel if not already exists
        const keyUpper = result.key.toUpperCase();
        if (!existingChannelKeys.has(keyUpper)) {
          try {
            const channelName = '#' + result.roomName;
            await onChannelCreate(channelName, result.key);
            // Optionally decrypt any other historical packets with this newly discovered key
            // This prevents wasting cracking cycles on packets from the same channel
            if (decryptHistoricalRef.current) {
              await api.decryptHistoricalPackets({ key_type: 'channel', channel_name: channelName });
            }
          } catch (err) {
            console.error('Failed to create channel or decrypt historical:', err);
          }
        }
      } else {
        // Failed
        setQueue(prev => {
          const updated = new Map(prev);
          const item = updated.get(nextId!);
          if (item) {
            updated.set(nextId!, {
              ...item,
              status: 'failed',
              attempts: item.attempts + 1,
              lastAttemptLength: targetLength,
            });
          }
          return updated;
        });
      }
    } catch (err) {
      console.error('Cracking error:', err);
      setQueue(prev => {
        const updated = new Map(prev);
        const item = updated.get(nextId!);
        if (item) {
          updated.set(nextId!, {
            ...item,
            status: 'failed',
            attempts: item.attempts + 1,
            lastAttemptLength: targetLength,
          });
        }
        return updated;
      });
    }

    // Unlock processing
    isProcessingRef.current = false;
    setProgress(null);

    // Continue processing if still running
    if (isRunningRef.current) {
      setTimeout(() => processNext(), 100);
    }
  }, [existingChannelKeys, onChannelCreate]);

  // Start/stop handlers
  const handleStart = () => {
    if (!gpuAvailable) {
      alert('WebGPU is not available in your browser. Please use Chrome 113+ or Edge 113+.');
      return;
    }
    setIsRunning(true);
    isRunningRef.current = true;
    abortedRef.current = false;
    noSleepRef.current?.enable();
    processNext();
  };

  const handleStop = () => {
    setIsRunning(false);
    isRunningRef.current = false;
    abortedRef.current = true;
    crackerRef.current?.abort();
    noSleepRef.current?.disable();
  };


  return (
    <div className="flex flex-col h-full p-3 gap-3 bg-background border-t border-border">
      <p className="text-xs text-muted-foreground leading-relaxed">
        This will attempt to dictionary attack, then brute force GroupText packets as they arrive, testing room names up to the specified length.
        <strong> Retry failed at n+1</strong> will let the cracker return to the failed queue and pick up messages it couldn't crack, attempting them at one longer length.
        <strong> Decrypt historical</strong> will run an async job on any room name it finds to see if any historically captured packets will decrypt with that key.
        <strong> Turbo mode</strong> will push your GPU to the max (target dispatch time of 10s) and may allow accelerated cracking and/or system instability.
      </p>
      <div className="flex items-center gap-3 flex-wrap">
        <button
          onClick={isRunning ? handleStop : handleStart}
          disabled={!wordlistLoaded || gpuAvailable === false}
          className={cn(
            "px-4 py-1.5 rounded text-sm font-medium",
            isRunning
              ? "bg-destructive text-destructive-foreground hover:bg-destructive/90"
              : "bg-primary text-primary-foreground hover:bg-primary/90",
            "disabled:opacity-50 disabled:cursor-not-allowed"
          )}
        >
          {isRunning ? 'Stop' : 'Start Cracking'}
        </button>

        <div className="flex items-center gap-2">
          <label className="text-sm text-muted-foreground">Max Length:</label>
          <input
            type="number"
            min={1}
            max={10}
            value={maxLength}
            onChange={(e) => setMaxLength(Math.min(10, Math.max(1, parseInt(e.target.value) || 6)))}
            className="w-14 px-2 py-1 text-sm bg-muted border border-border rounded"
          />
        </div>

        <label className="flex items-center gap-2 text-sm text-muted-foreground cursor-pointer">
          <input
            type="checkbox"
            checked={retryFailedAtNextLength}
            onChange={(e) => setRetryFailedAtNextLength(e.target.checked)}
            className="rounded"
          />
          Retry failed at n+1
        </label>

        <label className="flex items-center gap-2 text-sm text-muted-foreground cursor-pointer">
          <input
            type="checkbox"
            checked={decryptHistorical}
            onChange={(e) => setDecryptHistorical(e.target.checked)}
            className="rounded"
          />
          Decrypt historical
        </label>
        {decryptHistorical && (
          <span className="text-xs text-muted-foreground">
            {undecryptedPacketCount !== null && undecryptedPacketCount > 0
              ? `(${undecryptedPacketCount.toLocaleString()} packets; messages stream in as decrypted)`
              : '(messages stream in as decrypted)'}
          </span>
        )}

        <label className="flex items-center gap-2 text-sm text-muted-foreground cursor-pointer">
          <input
            type="checkbox"
            checked={turboMode}
            onChange={(e) => setTurboMode(e.target.checked)}
            className="rounded"
          />
          Turbo mode (experimental)
        </label>
      </div>

      {/* Status */}
      <div className="flex gap-4 text-sm">
        <span className="text-muted-foreground">
          Pending: <span className="text-foreground font-medium">{pendingCount}</span>
        </span>
        <span className="text-muted-foreground">
          Cracked: <span className="text-green-500 font-medium">{crackedCount}</span>
        </span>
        <span className="text-muted-foreground">
          Failed: <span className="text-destructive font-medium">{failedCount}</span>
        </span>
      </div>

      {/* Progress */}
      {progress && (
        <div className="space-y-1">
          <div className="flex justify-between text-xs text-muted-foreground">
            <span>
              {progress.phase === 'wordlist' ? 'Dictionary' : progress.phase === 'bruteforce' ? 'Bruteforce' : 'Public Key'}
              {progress.phase === 'bruteforce' && ` - Length ${progress.currentLength}`}
              : {progress.currentPosition}
            </span>
            <span>
              {progress.rateKeysPerSec >= 1e9
                ? `${(progress.rateKeysPerSec / 1e9).toFixed(2)} Gkeys/s`
                : `${(progress.rateKeysPerSec / 1e6).toFixed(1)} Mkeys/s`}
              {' '}• ETA: {progress.etaSeconds < 60 ? `${Math.round(progress.etaSeconds)}s` : `${Math.round(progress.etaSeconds / 60)}m`}
            </span>
          </div>
          <div className="h-2 bg-muted rounded overflow-hidden">
            <div
              className="h-full bg-primary transition-all duration-200"
              style={{ width: `${progress.percent}%` }}
            />
          </div>
        </div>
      )}

      {/* GPU status */}
      {gpuAvailable === false && (
        <div className="text-sm text-destructive">
          WebGPU not available. Cracking requires Chrome 113+ or Edge 113+.
        </div>
      )}
      {!wordlistLoaded && gpuAvailable !== false && (
        <div className="text-sm text-muted-foreground">
          Loading wordlist...
        </div>
      )}

      {/* Cracked rooms list */}
      {crackedRooms.length > 0 && (
        <div className="flex-1 overflow-y-auto min-h-0">
          <div className="text-xs text-muted-foreground mb-1">Cracked Rooms:</div>
          <div className="space-y-1">
            {crackedRooms.map((room, i) => (
              <div key={i} className="text-sm bg-green-950/30 border border-green-900/50 rounded px-2 py-1">
                <span className="text-green-400 font-medium">#{room.roomName}</span>
                <span className="text-muted-foreground ml-2 text-xs">
                  "{room.message.slice(0, 50)}{room.message.length > 50 ? '...' : ''}"
                </span>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
