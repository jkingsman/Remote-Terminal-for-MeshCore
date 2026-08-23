import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import { ChatHeader } from '../components/ChatHeader';
import type { Channel, Contact, Conversation, PathDiscoveryResponse } from '../types';
import { CONTACT_TYPE_ROOM } from '../types';

function makeChannel(key: string, name: string, mcmpEnabled = false): Channel {
  return {
    key,
    name,
    is_hashtag: true,
    on_radio: false,
    last_read_at: null,
    favorite: false,
    muted: false,
    mcmp_enabled: mcmpEnabled,
  };
}

function makeRoomContact(publicKey: string, name: string): Contact {
  return {
    public_key: publicKey,
    name,
    type: CONTACT_TYPE_ROOM,
    flags: 0,
    direct_path: null,
    direct_path_len: -1,
    direct_path_hash_mode: -1,
    last_advert: null,
    lat: null,
    lon: null,
    last_seen: null,
    on_radio: false,
    favorite: false,
    last_contacted: null,
    last_read_at: null,
    first_seen: null,
  };
}

const noop = () => {};

const baseProps = {
  contacts: [],
  channels: [],
  config: null,
  notificationsSupported: false,
  notificationsEnabled: false,
  notificationsPermission: 'granted' as const,
  onTrace: noop,
  onPathDiscovery: vi.fn(async () => {
    throw new Error('unused');
  }) as (_: string) => Promise<PathDiscoveryResponse>,
  onToggleNotifications: noop,
  onToggleFavorite: noop,
  onDeleteChannel: noop,
  onDeleteContact: noop,
};

describe('ChatHeader MCMP toggle', () => {
  it('enables compression for a channel that has it off', () => {
    const key = 'AA'.repeat(16);
    const channel = makeChannel(key, '#general', false);
    const conversation: Conversation = { type: 'channel', id: key, name: '#general' };
    const onSetMcmpEnabled = vi.fn();

    render(
      <ChatHeader
        {...baseProps}
        conversation={conversation}
        channels={[channel]}
        onSetMcmpEnabled={onSetMcmpEnabled}
      />
    );

    fireEvent.click(screen.getByRole('button', { name: 'Enable MCMP compression' }));
    expect(onSetMcmpEnabled).toHaveBeenCalledWith('channel', key, true);
  });

  it('disables compression for a channel that has it on', () => {
    const key = 'BB'.repeat(16);
    const channel = makeChannel(key, '#general', true);
    const conversation: Conversation = { type: 'channel', id: key, name: '#general' };
    const onSetMcmpEnabled = vi.fn();

    render(
      <ChatHeader
        {...baseProps}
        conversation={conversation}
        channels={[channel]}
        onSetMcmpEnabled={onSetMcmpEnabled}
      />
    );

    fireEvent.click(screen.getByRole('button', { name: 'Disable MCMP compression' }));
    expect(onSetMcmpEnabled).toHaveBeenCalledWith('channel', key, false);
  });

  it('does not render the toggle without an onSetMcmpEnabled handler', () => {
    const key = 'CC'.repeat(16);
    const conversation: Conversation = { type: 'channel', id: key, name: '#general' };

    render(
      <ChatHeader
        {...baseProps}
        conversation={conversation}
        channels={[makeChannel(key, '#general')]}
      />
    );

    expect(screen.queryByRole('button', { name: /MCMP compression/ })).toBeNull();
  });

  it('does not offer compression for room servers', () => {
    const key = 'dd'.repeat(32);
    const room = makeRoomContact(key, 'Ops Room');
    const conversation: Conversation = { type: 'contact', id: key, name: 'Ops Room' };

    render(
      <ChatHeader
        {...baseProps}
        conversation={conversation}
        contacts={[room]}
        onSetMcmpEnabled={vi.fn()}
      />
    );

    expect(screen.queryByRole('button', { name: /MCMP compression/ })).toBeNull();
  });
});
