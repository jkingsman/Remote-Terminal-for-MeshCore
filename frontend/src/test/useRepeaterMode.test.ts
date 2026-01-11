/**
 * Tests for useRepeaterMode hook utilities.
 *
 * These tests verify the formatting functions used to display
 * telemetry data from repeaters.
 */

import { describe, it, expect } from 'vitest';
import {
  formatDuration,
  formatTelemetry,
  formatNeighbors,
  formatAcl,
} from '../hooks/useRepeaterMode';
import type { TelemetryResponse, NeighborInfo, AclEntry } from '../types';

describe('formatDuration', () => {
  it('formats seconds under a minute', () => {
    expect(formatDuration(0)).toBe('0s');
    expect(formatDuration(30)).toBe('30s');
    expect(formatDuration(59)).toBe('59s');
  });

  it('formats minutes only', () => {
    expect(formatDuration(60)).toBe('1m');
    expect(formatDuration(120)).toBe('2m');
    expect(formatDuration(300)).toBe('5m');
    expect(formatDuration(3599)).toBe('59m');
  });

  it('formats hours and minutes', () => {
    expect(formatDuration(3600)).toBe('1h');
    expect(formatDuration(3660)).toBe('1h1m');
    expect(formatDuration(7200)).toBe('2h');
    expect(formatDuration(7380)).toBe('2h3m');
  });

  it('formats days only', () => {
    expect(formatDuration(86400)).toBe('1d');
    expect(formatDuration(172800)).toBe('2d');
  });

  it('formats days and hours', () => {
    expect(formatDuration(90000)).toBe('1d1h');
    expect(formatDuration(97200)).toBe('1d3h');
  });

  it('formats days and minutes (no hours)', () => {
    expect(formatDuration(86700)).toBe('1d5m');
  });

  it('formats days, hours, and minutes', () => {
    expect(formatDuration(90060)).toBe('1d1h1m');
    expect(formatDuration(148920)).toBe('1d17h22m');
  });
});

describe('formatTelemetry', () => {
  it('formats telemetry response with all fields', () => {
    const telemetry: TelemetryResponse = {
      pubkey_prefix: 'abc123',
      battery_volts: 4.123,
      uptime_seconds: 90060, // 1d1h1m
      airtime_seconds: 3600, // 1h
      rx_airtime_seconds: 7200, // 2h
      noise_floor_dbm: -120,
      last_rssi_dbm: -90,
      last_snr_db: 8.5,
      packets_received: 1000,
      packets_sent: 500,
      recv_flood: 800,
      sent_flood: 400,
      recv_direct: 200,
      sent_direct: 100,
      flood_dups: 50,
      direct_dups: 10,
      tx_queue_len: 2,
      full_events: 0,
      neighbors: [],
      acl: [],
    };

    const result = formatTelemetry(telemetry);

    expect(result).toContain('Telemetry');
    expect(result).toContain('Battery Voltage: 4.123V');
    expect(result).toContain('Uptime: 1d1h1m');
    expect(result).toContain('TX Airtime: 1h');
    expect(result).toContain('RX Airtime: 2h');
    expect(result).toContain('Noise Floor: -120 dBm');
    expect(result).toContain('Last RSSI: -90 dBm');
    expect(result).toContain('Last SNR: 8.5 dB');
    expect(result).toContain('Packets: 1,000 rx / 500 tx');
    expect(result).toContain('Flood: 800 rx / 400 tx');
    expect(result).toContain('Direct: 200 rx / 100 tx');
    expect(result).toContain('Duplicates: 50 flood / 10 direct');
    expect(result).toContain('TX Queue: 2');
  });

  it('formats battery voltage with 3 decimal places', () => {
    const telemetry: TelemetryResponse = {
      pubkey_prefix: 'abc123',
      battery_volts: 3.7,
      uptime_seconds: 0,
      airtime_seconds: 0,
      rx_airtime_seconds: 0,
      noise_floor_dbm: 0,
      last_rssi_dbm: 0,
      last_snr_db: 0,
      packets_received: 0,
      packets_sent: 0,
      recv_flood: 0,
      sent_flood: 0,
      recv_direct: 0,
      sent_direct: 0,
      flood_dups: 0,
      direct_dups: 0,
      tx_queue_len: 0,
      full_events: 0,
      neighbors: [],
      acl: [],
    };

    const result = formatTelemetry(telemetry);
    expect(result).toContain('Battery Voltage: 3.700V');
  });
});

describe('formatNeighbors', () => {
  it('returns "No neighbors" message for empty list', () => {
    const result = formatNeighbors([]);

    expect(result).toBe('Neighbors\nNo neighbors reported');
  });

  it('formats single neighbor', () => {
    const neighbors: NeighborInfo[] = [
      { pubkey_prefix: 'abc123', name: 'Alice', snr: 8.5, last_heard_seconds: 60 },
    ];

    const result = formatNeighbors(neighbors);

    expect(result).toContain('Neighbors (1)');
    expect(result).toContain('Alice, +8.5 dB [1m ago]');
  });

  it('sorts neighbors by SNR descending', () => {
    const neighbors: NeighborInfo[] = [
      { pubkey_prefix: 'aaa', name: 'Low', snr: -5, last_heard_seconds: 10 },
      { pubkey_prefix: 'bbb', name: 'High', snr: 10, last_heard_seconds: 20 },
      { pubkey_prefix: 'ccc', name: 'Mid', snr: 5, last_heard_seconds: 30 },
    ];

    const result = formatNeighbors(neighbors);
    const lines = result.split('\n');

    expect(lines[1]).toContain('High');
    expect(lines[2]).toContain('Mid');
    expect(lines[3]).toContain('Low');
  });

  it('uses pubkey_prefix when name is null', () => {
    const neighbors: NeighborInfo[] = [
      { pubkey_prefix: 'abc123def456', name: null, snr: 5, last_heard_seconds: 120 },
    ];

    const result = formatNeighbors(neighbors);

    expect(result).toContain('abc123def456, +5.0 dB [2m ago]');
  });

  it('formats negative SNR without plus sign', () => {
    const neighbors: NeighborInfo[] = [
      { pubkey_prefix: 'abc', name: 'Test', snr: -3.5, last_heard_seconds: 60 },
    ];

    const result = formatNeighbors(neighbors);

    expect(result).toContain('Test, -3.5 dB');
  });

  it('formats last heard in various durations', () => {
    const neighbors: NeighborInfo[] = [
      { pubkey_prefix: 'a', name: 'Seconds', snr: 0, last_heard_seconds: 45 },
      { pubkey_prefix: 'b', name: 'Minutes', snr: 0, last_heard_seconds: 300 },
      { pubkey_prefix: 'c', name: 'Hours', snr: 0, last_heard_seconds: 7200 },
    ];

    const result = formatNeighbors(neighbors);

    expect(result).toContain('Seconds, +0.0 dB [45s ago]');
    expect(result).toContain('Minutes, +0.0 dB [5m ago]');
    expect(result).toContain('Hours, +0.0 dB [2h ago]');
  });
});

describe('formatAcl', () => {
  it('returns "No ACL entries" message for empty list', () => {
    const result = formatAcl([]);

    expect(result).toBe('ACL\nNo ACL entries');
  });

  it('formats single ACL entry', () => {
    const acl: AclEntry[] = [
      { pubkey_prefix: 'abc123', name: 'Alice', permission: 3, permission_name: 'Admin' },
    ];

    const result = formatAcl(acl);

    expect(result).toContain('ACL (1)');
    expect(result).toContain('Alice: Admin');
  });

  it('formats multiple ACL entries', () => {
    const acl: AclEntry[] = [
      { pubkey_prefix: 'aaa', name: 'Admin User', permission: 3, permission_name: 'Admin' },
      { pubkey_prefix: 'bbb', name: 'Read Only', permission: 1, permission_name: 'Read-only' },
      { pubkey_prefix: 'ccc', name: null, permission: 0, permission_name: 'Guest' },
    ];

    const result = formatAcl(acl);

    expect(result).toContain('ACL (3)');
    expect(result).toContain('Admin User: Admin');
    expect(result).toContain('Read Only: Read-only');
    expect(result).toContain('ccc: Guest');
  });

  it('uses pubkey_prefix when name is null', () => {
    const acl: AclEntry[] = [
      { pubkey_prefix: 'xyz789', name: null, permission: 2, permission_name: 'Read-write' },
    ];

    const result = formatAcl(acl);

    expect(result).toContain('xyz789: Read-write');
  });
});
