import { render, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { StatisticsView } from '../components/StatisticsView';
import type { StatisticsResponse } from '../types';

const emptyStats: StatisticsResponse = {
  busiest_channels_24h: [],
  contact_count: 0,
  repeater_count: 0,
  channel_count: 0,
  total_packets: 0,
  decrypted_packets: 0,
  undecrypted_packets: 0,
  total_dms: 0,
  total_channel_messages: 0,
  total_outgoing: 0,
  contacts_heard: { last_hour: 0, last_24_hours: 0, last_week: 0 },
  repeaters_heard: { last_hour: 0, last_24_hours: 0, last_week: 0 },
  known_channels_active: { last_hour: 0, last_24_hours: 0, last_week: 0 },
  path_hash_width_24h: {
    total_packets: 0,
    single_byte: 0,
    double_byte: 0,
    triple_byte: 0,
    single_byte_pct: 0,
    double_byte_pct: 0,
    triple_byte_pct: 0,
  },
  multibyte_rollout: {
    contacts_with_route: 0,
    contacts_multibyte: 0,
    single_byte: 0,
    double_byte: 0,
    triple_byte: 0,
    repeaters_with_route: 0,
    repeaters_multibyte: 0,
  },
  region_scope_24h: {
    total_messages: 0,
    scoped_messages: 0,
    scoped_pct: 0,
    false_positive_floor: 0,
    total_senders: 0,
    scoped_senders: 0,
    scoped_senders_pct: 0,
  },
  packets_per_hour_72h: [],
  noise_floor_24h: {
    sample_interval_seconds: 60,
    coverage_seconds: 0,
    latest_noise_floor_dbm: null,
    latest_timestamp: null,
    samples: [],
  },
};

function mockStatsFetch(stats: StatisticsResponse) {
  return vi.spyOn(globalThis, 'fetch').mockResolvedValue(
    new Response(JSON.stringify(stats), {
      status: 200,
      headers: { 'Content-Type': 'application/json' },
    })
  );
}

describe('StatisticsView', () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it('fetches statistics on mount and renders the data', async () => {
    const mockStats: StatisticsResponse = {
      ...emptyStats,
      busiest_channels_24h: [
        { channel_key: 'AA'.repeat(16), channel_name: 'general', message_count: 42 },
      ],
      contact_count: 10,
      repeater_count: 3,
      channel_count: 5,
      total_packets: 200,
      decrypted_packets: 150,
      undecrypted_packets: 50,
      total_dms: 25,
      total_channel_messages: 80,
      total_outgoing: 30,
      contacts_heard: { last_hour: 2, last_24_hours: 7, last_week: 10 },
      repeaters_heard: { last_hour: 1, last_24_hours: 3, last_week: 3 },
      known_channels_active: { last_hour: 1, last_24_hours: 4, last_week: 6 },
      path_hash_width_24h: {
        total_packets: 120,
        single_byte: 60,
        double_byte: 36,
        triple_byte: 24,
        single_byte_pct: 50,
        double_byte_pct: 30,
        triple_byte_pct: 20,
      },
      region_scope_24h: {
        total_messages: 120,
        scoped_messages: 40,
        scoped_pct: 33.3,
        false_positive_floor: 2,
        total_senders: 12,
        scoped_senders: 3,
        scoped_senders_pct: 25,
      },
      packets_per_hour_72h: [
        { timestamp: 1711792800, count: 12 },
        { timestamp: 1711796400, count: 8 },
      ],
      noise_floor_24h: {
        sample_interval_seconds: 60,
        coverage_seconds: 3600,
        latest_noise_floor_dbm: -105,
        latest_timestamp: 1711800000,
        samples: [],
      },
    };

    const fetchSpy = mockStatsFetch(mockStats);

    render(<StatisticsView />);

    await waitFor(() => {
      expect(screen.getByText('Network')).toBeInTheDocument();
    });
    expect(fetchSpy).toHaveBeenCalledWith('./api/statistics', expect.any(Object));

    // Verify key labels are present
    expect(screen.getByText('Contacts')).toBeInTheDocument();
    expect(screen.getByText('Repeaters')).toBeInTheDocument();
    expect(screen.getByText('Direct Messages')).toBeInTheDocument();
    expect(screen.getByText('Channel Messages')).toBeInTheDocument();
    expect(screen.getByText('Sent (Outgoing)')).toBeInTheDocument();
    expect(screen.getByText('Total stored')).toBeInTheDocument();
    expect(screen.getByText('Decrypted')).toBeInTheDocument();
    expect(screen.getByText('Undecrypted')).toBeInTheDocument();
    expect(screen.getByText('Path Hash Width (24h)')).toBeInTheDocument();
    expect(
      screen.getByText(/Parsed stored raw packets from the last 24 hours: 120/)
    ).toBeInTheDocument();
    expect(screen.getByText('Contacts heard')).toBeInTheDocument();
    expect(screen.getByText('Repeaters heard')).toBeInTheDocument();
    expect(screen.getByText('Known-channels active')).toBeInTheDocument();
    expect(screen.getByText('Busiest Channels (24h)')).toBeInTheDocument();
    expect(screen.getByText('Noise Floor (24h)')).toBeInTheDocument();
    expect(screen.getByText('Region Scope (24h)')).toBeInTheDocument();
    // Fractions, not bare percentages — the sample size matters at this sparsity
    expect(screen.getByText(/40 of 120/)).toBeInTheDocument();
    expect(screen.getByText(/3 of 12/)).toBeInTheDocument();
    // 40 scoped is well above the floor of 2, so the percentage is shown
    expect(screen.getByText(/33\.3%/)).toBeInTheDocument();
    expect(
      screen.queryByText(/at or below the estimated false-positive floor/)
    ).not.toBeInTheDocument();
  });

  it('discloses the false-positive floor and withholds a sub-0.1% scoped share', async () => {
    // Mirrors real-world data: 70 "scoped" packets against a measured floor of
    // 60 is corrupt-capture noise, not adoption.
    const mockStats: StatisticsResponse = {
      ...emptyStats,
      region_scope_24h: {
        total_messages: 391757,
        scoped_messages: 70,
        scoped_pct: 0.0179,
        false_positive_floor: 60.3,
        total_senders: 117,
        scoped_senders: 3,
        scoped_senders_pct: 2.56,
      },
    };

    mockStatsFetch(mockStats);

    render(<StatisticsView />);

    await waitFor(() => {
      expect(screen.getByText('Region Scope (24h)')).toBeInTheDocument();
    });

    // 70 scoped sits just above the 60.3 floor, so most of it is corrupt captures
    expect(screen.getByText(/Includes an estimated 60 false positives/)).toBeInTheDocument();
    // 0.0179% would render as a meaningless "0.0%", so the share is withheld
    expect(screen.queryByText(/0\.0%/)).not.toBeInTheDocument();
    expect(screen.getByText(/70 of 391,757/)).toBeInTheDocument();
    // ...but the decryption-backed sender figure still stands
    expect(screen.getByText(/3 of 117/)).toBeInTheDocument();
    expect(screen.getByText(/2\.6%/)).toBeInTheDocument();
  });

  it('reports scoped traffic as noise when it is at or below the floor', async () => {
    const mockStats: StatisticsResponse = {
      ...emptyStats,
      region_scope_24h: {
        total_messages: 5000,
        scoped_messages: 12,
        scoped_pct: 0.24,
        false_positive_floor: 20,
        total_senders: 40,
        scoped_senders: 0,
        scoped_senders_pct: 0,
      },
    };

    mockStatsFetch(mockStats);

    render(<StatisticsView />);

    await waitFor(() => {
      expect(screen.getByText('Region Scope (24h)')).toBeInTheDocument();
    });

    expect(
      screen.getByText(/at or below the estimated false-positive floor \(20\)/)
    ).toBeInTheDocument();
    // Percentage withheld even though 0.24% would round visibly — it is noise
    expect(screen.queryByText(/0\.2%/)).not.toBeInTheDocument();
  });

  it('shows an error message when the statistics fetch fails', async () => {
    vi.spyOn(globalThis, 'fetch').mockRejectedValue(new Error('network down'));

    render(<StatisticsView />);

    await waitFor(() => {
      expect(screen.getByText('Failed to load statistics.')).toBeInTheDocument();
    });
  });
});
