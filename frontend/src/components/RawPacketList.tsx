import { useEffect, useRef } from 'react';
import type { RawPacket } from '../types';

interface RawPacketListProps {
  packets: RawPacket[];
}

function formatTime(timestamp: number): string {
  const date = new Date(timestamp * 1000);
  return date.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' });
}

function formatPayloadType(type: string): string {
  // Convert SNAKE_CASE to Title Case
  return type
    .split('_')
    .map((word) => word.charAt(0) + word.slice(1).toLowerCase())
    .join(' ');
}

function getDecryptedLabel(packet: RawPacket): string {
  if (!packet.decrypted || !packet.decrypted_info) {
    return formatPayloadType(packet.payload_type);
  }

  const info = packet.decrypted_info;
  if (packet.payload_type === 'GROUP_TEXT' && info.channel_name) {
    return `GroupText to ${info.channel_name}`;
  }
  if (packet.payload_type === 'TEXT_MESSAGE' && info.sender) {
    return `TextMessage from ${info.sender}`;
  }

  return formatPayloadType(packet.payload_type);
}

function formatSignalInfo(packet: RawPacket): string {
  const parts: string[] = [];
  if (packet.snr !== null && packet.snr !== undefined) {
    parts.push(`SNR: ${packet.snr.toFixed(1)} dB`);
  }
  if (packet.rssi !== null && packet.rssi !== undefined) {
    parts.push(`RSSI: ${packet.rssi} dBm`);
  }
  return parts.join(' | ');
}

export function RawPacketList({ packets }: RawPacketListProps) {
  const listRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (listRef.current) {
      listRef.current.scrollTop = listRef.current.scrollHeight;
    }
  }, [packets]);

  if (packets.length === 0) {
    return (
      <div className="h-full overflow-y-auto p-5 text-center text-muted-foreground">
        No packets received yet. Packets will appear here in real-time.
      </div>
    );
  }

  // Sort packets by timestamp ascending (oldest first)
  const sortedPackets = [...packets].sort((a, b) => a.timestamp - b.timestamp);

  return (
    <div className="h-full overflow-y-auto p-4 flex flex-col gap-3" ref={listRef}>
      {sortedPackets.map((packet) => (
        <div key={packet.id} className="py-2 px-3 bg-muted rounded">
          <div className={packet.decrypted ? 'text-primary' : 'text-destructive'}>
            {!packet.decrypted && <span className="mr-1">🔒</span>}
            {getDecryptedLabel(packet)}
            {' • '}
            {formatTime(packet.timestamp)}
          </div>
          {(packet.snr !== null || packet.rssi !== null) && (
            <div className="text-[11px] text-muted-foreground mt-0.5">
              {formatSignalInfo(packet)}
            </div>
          )}
          <div className="font-mono text-[11px] break-all text-muted-foreground/70 mt-1">
            {packet.data.toUpperCase()}
          </div>
        </div>
      ))}
    </div>
  );
}
