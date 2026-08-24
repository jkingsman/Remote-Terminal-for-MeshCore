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

const FEATURES_BUTTON = { name: 'Conversation features' };

describe('ChatHeader conversation-features modal', () => {
  it('opens the modal and enables MCMP for a channel that has it off', () => {
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

    // The toggle is not shown until the modal is opened.
    expect(screen.queryByRole('switch', { name: /MCMP compression/ })).toBeNull();
    fireEvent.click(screen.getByRole('button', FEATURES_BUTTON));

    fireEvent.click(screen.getByRole('switch', { name: 'Enable MCMP compression' }));
    expect(onSetMcmpEnabled).toHaveBeenCalledWith('channel', key, true, 2);
  });

  it('shows the toggle on for a channel that has MCMP enabled, and disables it', () => {
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

    fireEvent.click(screen.getByRole('button', FEATURES_BUTTON));
    const toggle = screen.getByRole('switch', { name: 'Disable MCMP compression' });
    expect(toggle).toHaveAttribute('aria-checked', 'true');
    fireEvent.click(toggle);
    expect(onSetMcmpEnabled).toHaveBeenCalledWith('channel', key, false, 2);
  });

  it('selects the MCMP version from the modal', () => {
    const key = 'EE'.repeat(16);
    const channel = { ...makeChannel(key, '#general', true), mcmp_version: 2 };
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

    fireEvent.click(screen.getByRole('button', FEATURES_BUTTON));
    // v2 is selected; choosing v3 keeps enabled=true and sets version=3.
    const v3 = screen.getByRole('radio', { name: 'MCMP v3' });
    expect(v3).toHaveAttribute('aria-checked', 'false');
    fireEvent.click(v3);
    expect(onSetMcmpEnabled).toHaveBeenCalledWith('channel', key, true, 3);
  });

  it('opens the modal for a regular contact', () => {
    const key = 'ab'.repeat(32);
    const contact: Contact = { ...makeRoomContact(key, 'Alice'), type: 1 };
    const conversation: Conversation = { type: 'contact', id: key, name: 'Alice' };
    const onSetMcmpEnabled = vi.fn();

    render(
      <ChatHeader
        {...baseProps}
        conversation={conversation}
        contacts={[contact]}
        onSetMcmpEnabled={onSetMcmpEnabled}
      />
    );

    fireEvent.click(screen.getByRole('button', FEATURES_BUTTON));
    fireEvent.click(screen.getByRole('switch', { name: 'Enable MCMP compression' }));
    expect(onSetMcmpEnabled).toHaveBeenCalledWith('contact', key, true, 2);
  });

  it('does not render the features button without an onSetMcmpEnabled handler', () => {
    const key = 'CC'.repeat(16);
    const conversation: Conversation = { type: 'channel', id: key, name: '#general' };

    render(
      <ChatHeader
        {...baseProps}
        conversation={conversation}
        channels={[makeChannel(key, '#general')]}
      />
    );

    expect(screen.queryByRole('button', FEATURES_BUTTON)).toBeNull();
  });

  it('does not offer features for room servers', () => {
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

    expect(screen.queryByRole('button', FEATURES_BUTTON)).toBeNull();
  });
});
