import { useState, useCallback, useMemo, useEffect } from 'react';
import { api } from '../api';
import type { Contact, Conversation, Message, TelemetryResponse, NeighborInfo, AclEntry } from '../types';
import { CONTACT_TYPE_REPEATER } from '../types';

// Format seconds into human-readable duration (e.g., 1d17h2m, 1h5m, 3m)
export function formatDuration(seconds: number): string {
  if (seconds < 60) return `${seconds}s`;

  const days = Math.floor(seconds / 86400);
  const hours = Math.floor((seconds % 86400) / 3600);
  const mins = Math.floor((seconds % 3600) / 60);

  if (days > 0) {
    if (hours > 0 && mins > 0) return `${days}d${hours}h${mins}m`;
    if (hours > 0) return `${days}d${hours}h`;
    if (mins > 0) return `${days}d${mins}m`;
    return `${days}d`;
  }
  if (hours > 0) {
    return mins > 0 ? `${hours}h${mins}m` : `${hours}h`;
  }
  return `${mins}m`;
}

// Format telemetry response as human-readable text
export function formatTelemetry(telemetry: TelemetryResponse): string {
  const lines = [
    `Telemetry`,
    `Battery Voltage: ${telemetry.battery_volts.toFixed(3)}V`,
    `Uptime: ${formatDuration(telemetry.uptime_seconds)}`,
    `TX Airtime: ${formatDuration(telemetry.airtime_seconds)}`,
    `RX Airtime: ${formatDuration(telemetry.rx_airtime_seconds)}`,
    '',
    `Noise Floor: ${telemetry.noise_floor_dbm} dBm`,
    `Last RSSI: ${telemetry.last_rssi_dbm} dBm`,
    `Last SNR: ${telemetry.last_snr_db.toFixed(1)} dB`,
    '',
    `Packets: ${telemetry.packets_received.toLocaleString()} rx / ${telemetry.packets_sent.toLocaleString()} tx`,
    `Flood: ${telemetry.recv_flood.toLocaleString()} rx / ${telemetry.sent_flood.toLocaleString()} tx`,
    `Direct: ${telemetry.recv_direct.toLocaleString()} rx / ${telemetry.sent_direct.toLocaleString()} tx`,
    `Duplicates: ${telemetry.flood_dups.toLocaleString()} flood / ${telemetry.direct_dups.toLocaleString()} direct`,
    '',
    `TX Queue: ${telemetry.tx_queue_len}`,
    `Debug Flags: ${telemetry.full_events}`,
  ];
  return lines.join('\n');
}

// Format neighbors list as human-readable text
export function formatNeighbors(neighbors: NeighborInfo[]): string {
  if (neighbors.length === 0) {
    return 'Neighbors\nNo neighbors reported';
  }
  // Sort by SNR descending (highest first)
  const sorted = [...neighbors].sort((a, b) => b.snr - a.snr);
  const lines = [`Neighbors (${sorted.length})`];
  for (const n of sorted) {
    const name = n.name || n.pubkey_prefix;
    const snr = n.snr >= 0 ? `+${n.snr.toFixed(1)}` : n.snr.toFixed(1);
    lines.push(`${name}, ${snr} dB [${formatDuration(n.last_heard_seconds)} ago]`);
  }
  return lines.join('\n');
}

// Format ACL list as human-readable text
export function formatAcl(acl: AclEntry[]): string {
  if (acl.length === 0) {
    return 'ACL\nNo ACL entries';
  }
  const lines = [`ACL (${acl.length})`];
  for (const entry of acl) {
    const name = entry.name || entry.pubkey_prefix;
    lines.push(`${name}: ${entry.permission_name}`);
  }
  return lines.join('\n');
}

// Create a local message object (not persisted to database)
function createLocalMessage(
  conversationKey: string,
  text: string,
  outgoing: boolean,
  idOffset = 0
): Message {
  const now = Math.floor(Date.now() / 1000);
  return {
    id: -Date.now() - idOffset,
    type: 'PRIV',
    conversation_key: conversationKey,
    text,
    sender_timestamp: now,
    received_at: now,
    path_len: null,
    txt_type: 0,
    signature: null,
    outgoing,
    acked: 1,
  };
}

export interface UseRepeaterModeResult {
  repeaterLoggedIn: boolean;
  activeContactIsRepeater: boolean;
  handleTelemetryRequest: (password: string) => Promise<void>;
  handleRepeaterCommand: (command: string) => Promise<void>;
}

export function useRepeaterMode(
  activeConversation: Conversation | null,
  contacts: Contact[],
  setMessages: React.Dispatch<React.SetStateAction<Message[]>>
): UseRepeaterModeResult {
  const [repeaterLoggedIn, setRepeaterLoggedIn] = useState(false);

  // Reset login state when conversation changes
  useEffect(() => {
    setRepeaterLoggedIn(false);
  }, [activeConversation?.id]);

  // Check if active conversation is a repeater
  const activeContactIsRepeater = useMemo(() => {
    if (!activeConversation || activeConversation.type !== 'contact') return false;
    const contact = contacts.find(c => c.public_key === activeConversation.id);
    return contact?.type === CONTACT_TYPE_REPEATER;
  }, [activeConversation, contacts]);

  // Request telemetry from a repeater
  const handleTelemetryRequest = useCallback(
    async (password: string) => {
      if (!activeConversation || activeConversation.type !== 'contact') return;
      if (!activeContactIsRepeater) return;

      try {
        const telemetry = await api.requestTelemetry(activeConversation.id, password);

        // Create local messages to display the telemetry (not persisted to database)
        const telemetryMessage = createLocalMessage(
          activeConversation.id,
          formatTelemetry(telemetry),
          false,
          0
        );

        const neighborsMessage = createLocalMessage(
          activeConversation.id,
          formatNeighbors(telemetry.neighbors),
          false,
          1
        );

        const aclMessage = createLocalMessage(
          activeConversation.id,
          formatAcl(telemetry.acl),
          false,
          2
        );

        // Add all messages to the list
        setMessages((prev) => [...prev, telemetryMessage, neighborsMessage, aclMessage]);

        // Mark as logged in for CLI command mode
        setRepeaterLoggedIn(true);
      } catch (err) {
        const errorMessage = createLocalMessage(
          activeConversation.id,
          `Telemetry request failed: ${err instanceof Error ? err.message : 'Unknown error'}`,
          false,
          0
        );
        setMessages((prev) => [...prev, errorMessage]);
      }
    },
    [activeConversation, activeContactIsRepeater, setMessages]
  );

  // Send CLI command to a repeater (after logged in)
  const handleRepeaterCommand = useCallback(
    async (command: string) => {
      if (!activeConversation || activeConversation.type !== 'contact') return;
      if (!activeContactIsRepeater || !repeaterLoggedIn) return;

      // Show the command as an outgoing message
      const commandMessage = createLocalMessage(
        activeConversation.id,
        `> ${command}`,
        true,
        0
      );
      setMessages((prev) => [...prev, commandMessage]);

      try {
        const response = await api.sendRepeaterCommand(activeConversation.id, command);

        // Use the actual timestamp from the repeater if available
        const responseMessage = createLocalMessage(
          activeConversation.id,
          response.response,
          false,
          1
        );
        if (response.sender_timestamp) {
          responseMessage.sender_timestamp = response.sender_timestamp;
        }

        setMessages((prev) => [...prev, responseMessage]);
      } catch (err) {
        const errorMessage = createLocalMessage(
          activeConversation.id,
          `Command failed: ${err instanceof Error ? err.message : 'Unknown error'}`,
          false,
          1
        );
        setMessages((prev) => [...prev, errorMessage]);
      }
    },
    [activeConversation, activeContactIsRepeater, repeaterLoggedIn, setMessages]
  );

  return {
    repeaterLoggedIn,
    activeContactIsRepeater,
    handleTelemetryRequest,
    handleRepeaterCommand,
  };
}
