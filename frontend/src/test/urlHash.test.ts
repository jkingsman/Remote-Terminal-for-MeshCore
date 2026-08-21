/**
 * Tests for URL hash utilities.
 *
 * These tests verify the URL hash parsing and generation
 * for deep linking to conversations.
 */

import { describe, it, expect, beforeEach, afterEach } from 'vitest';
import {
  parseHashConversation,
  parseHashSettingsSection,
  getConversationHash,
  getSettingsHash,
  getMapFocusHash,
  resolveChannelFromHashToken,
  resolveContactFromHashToken,
} from '../utils/urlHash';
import type { Channel, Contact } from '../types';
import { PUBLIC_CHANNEL_KEY } from '../utils/publicChannel';

describe('parseHashConversation', () => {
  let originalHash: string;

  beforeEach(() => {
    originalHash = window.location.hash;
  });

  afterEach(() => {
    window.location.hash = originalHash;
  });

  it('returns null for empty hash', () => {
    window.location.hash = '';

    const result = parseHashConversation();

    expect(result).toBeNull();
  });

  it('parses #raw as raw type', () => {
    window.location.hash = '#raw';

    const result = parseHashConversation();

    expect(result).toEqual({ type: 'raw', name: 'raw' });
  });

  it('parses #map as map type', () => {
    window.location.hash = '#map';

    const result = parseHashConversation();

    expect(result).toEqual({ type: 'map', name: 'map' });
  });

  it('parses #trace as trace type', () => {
    window.location.hash = '#trace';

    const result = parseHashConversation();

    expect(result).toEqual({ type: 'trace', name: 'trace' });
  });

  it('parses #bots as bots type', () => {
    window.location.hash = '#bots';

    const result = parseHashConversation();

    expect(result).toEqual({ type: 'bots', name: 'bots' });
  });

  it('parses #bots/{id} with a bot editor deep link', () => {
    window.location.hash = '#bots/abc-123';

    const result = parseHashConversation();

    expect(result).toEqual({ type: 'bots', name: 'bots', botId: 'abc-123' });
  });

  it('parses #map/focus/PUBKEY with focus key', () => {
    window.location.hash = '#map/focus/ABCD1234';

    const result = parseHashConversation();

    expect(result).toEqual({ type: 'map', name: 'map', mapFocusKey: 'ABCD1234' });
  });

  it('parses #map/focus/ with empty focus as plain map', () => {
    window.location.hash = '#map/focus/';

    const result = parseHashConversation();

    expect(result).toEqual({ type: 'map', name: 'map' });
  });

  it('decodes URL-encoded map focus key', () => {
    window.location.hash = '#map/focus/AB%20CD';

    const result = parseHashConversation();

    expect(result).toEqual({ type: 'map', name: 'map', mapFocusKey: 'AB CD' });
  });

  it('parses channel hash', () => {
    window.location.hash = '#channel/ABCDEF0123456789ABCDEF0123456789';

    const result = parseHashConversation();

    expect(result).toEqual({ type: 'channel', name: 'ABCDEF0123456789ABCDEF0123456789' });
  });

  it('parses contact hash', () => {
    window.location.hash =
      '#contact/abc123def4567890abc123def4567890abc123def4567890abc123def4567890';

    const result = parseHashConversation();

    expect(result).toEqual({
      type: 'contact',
      name: 'abc123def4567890abc123def4567890abc123def4567890abc123def4567890',
    });
  });

  it('parses id plus readable label and preserves id token', () => {
    window.location.hash = '#channel/ABCDEF0123456789ABCDEF0123456789/Public%20Room';

    const result = parseHashConversation();

    expect(result).toEqual({
      type: 'channel',
      name: 'ABCDEF0123456789ABCDEF0123456789',
      label: 'Public Room',
    });
  });

  it('decodes URL-encoded names', () => {
    window.location.hash = '#contact/John%20Doe';

    const result = parseHashConversation();

    expect(result).toEqual({ type: 'contact', name: 'John Doe' });
  });

  it('returns null for invalid type', () => {
    window.location.hash = '#invalid/Test';

    const result = parseHashConversation();

    expect(result).toBeNull();
  });

  it('returns null for hash without slash', () => {
    window.location.hash = '#channelPublic';

    const result = parseHashConversation();

    expect(result).toBeNull();
  });

  it('returns null for hash with empty name', () => {
    window.location.hash = '#channel/';

    const result = parseHashConversation();

    expect(result).toBeNull();
  });

  it('handles channel names with special characters', () => {
    window.location.hash = '#channel/Test%20Channel%21';

    const result = parseHashConversation();

    expect(result).toEqual({ type: 'channel', name: 'Test Channel!' });
  });
});

describe('settings URL hashes', () => {
  let originalHash: string;

  beforeEach(() => {
    originalHash = window.location.hash;
  });

  afterEach(() => {
    window.location.hash = originalHash;
  });

  it('parses a valid settings section hash', () => {
    window.location.hash = '#settings/database';

    expect(parseHashSettingsSection()).toBe('database');
  });

  it('returns null for an invalid settings section hash', () => {
    window.location.hash = '#settings/not-a-section';

    expect(parseHashSettingsSection()).toBeNull();
  });

  it('builds a stable settings hash', () => {
    expect(getSettingsHash('local')).toBe('#settings/local');
  });
});

describe('resolveChannelFromHashToken', () => {
  const channels: Channel[] = [
    {
      key: PUBLIC_CHANNEL_KEY,
      name: 'Public',
      is_hashtag: false,
      on_radio: true,
      last_read_at: null,
      favorite: false,
      muted: false,
    },
    {
      key: '11111111111111111111111111111111',
      name: '#mesh',
      is_hashtag: true,
      on_radio: false,
      last_read_at: null,
      favorite: false,
      muted: false,
    },
    {
      key: '22222222222222222222222222222222',
      name: 'Public',
      is_hashtag: false,
      on_radio: false,
      last_read_at: null,
      favorite: false,
      muted: false,
    },
  ];

  it('prefers stable key lookup (case-insensitive)', () => {
    const result = resolveChannelFromHashToken(PUBLIC_CHANNEL_KEY.toLowerCase(), channels);
    expect(result?.key).toBe(PUBLIC_CHANNEL_KEY);
  });

  it('resolves legacy Public hashes to the canonical Public key', () => {
    const result = resolveChannelFromHashToken('Public', channels);
    expect(result?.key).toBe(PUBLIC_CHANNEL_KEY);
  });

  it('supports legacy hashtag hash without leading #', () => {
    const result = resolveChannelFromHashToken('mesh', channels);
    expect(result?.key).toBe('11111111111111111111111111111111');
  });
});

describe('resolveContactFromHashToken', () => {
  const contacts: Contact[] = [
    {
      public_key: 'abc123def4567890abc123def4567890abc123def4567890abc123def4567890',
      name: 'Alice',
      type: 1,
      flags: 0,
      direct_path: null,
      direct_path_len: -1,
      direct_path_hash_mode: 0,
      last_advert: null,
      lat: null,
      lon: null,
      last_seen: null,
      on_radio: false,
      favorite: false,
      last_contacted: null,
      last_read_at: null,
      first_seen: null,
    },
    {
      public_key: 'def456abc1237890def456abc1237890def456abc1237890def456abc1237890',
      name: 'Alice',
      type: 1,
      flags: 0,
      direct_path: null,
      direct_path_len: -1,
      direct_path_hash_mode: 0,
      last_advert: null,
      lat: null,
      lon: null,
      last_seen: null,
      on_radio: false,
      favorite: false,
      last_contacted: null,
      last_read_at: null,
      first_seen: null,
    },
    {
      public_key: 'eeeeee111111222222333333444444555555666666777777888888999999aaaa',
      name: null,
      type: 1,
      flags: 0,
      direct_path: null,
      direct_path_len: -1,
      direct_path_hash_mode: 0,
      last_advert: null,
      lat: null,
      lon: null,
      last_seen: null,
      on_radio: false,
      favorite: false,
      last_contacted: null,
      last_read_at: null,
      first_seen: null,
    },
  ];

  it('prefers stable public-key lookup (case-insensitive)', () => {
    const result = resolveContactFromHashToken(
      'ABC123DEF4567890ABC123DEF4567890ABC123DEF4567890ABC123DEF4567890',
      contacts
    );
    expect(result?.public_key).toBe(
      'abc123def4567890abc123def4567890abc123def4567890abc123def4567890'
    );
  });

  it('supports legacy display-name hash lookup', () => {
    const result = resolveContactFromHashToken('Alice', contacts);
    expect(result?.public_key).toBe(
      'abc123def4567890abc123def4567890abc123def4567890abc123def4567890'
    );
  });

  it('supports legacy unnamed-contact prefix hash lookup', () => {
    const result = resolveContactFromHashToken('eeeeee111111', contacts);
    expect(result?.public_key).toBe(
      'eeeeee111111222222333333444444555555666666777777888888999999aaaa'
    );
  });
});

describe('getMapFocusHash', () => {
  it('generates hash with focus key', () => {
    const result = getMapFocusHash('ABCD1234');

    expect(result).toBe('#map/focus/ABCD1234');
  });

  it('encodes special characters in key', () => {
    const result = getMapFocusHash('AB CD/12');

    expect(result).toBe('#map/focus/AB%20CD%2F12');
  });
});

describe('getConversationHash for bots', () => {
  it('generates #bots for the workspace', () => {
    expect(getConversationHash({ type: 'bots', id: 'bots', name: 'Bots' })).toBe('#bots');
  });

  it('generates #bots/{id} for the editor', () => {
    expect(getConversationHash({ type: 'bots', id: 'bots', name: 'Bots', botId: 'abc-123' })).toBe(
      '#bots/abc-123'
    );
  });
});
