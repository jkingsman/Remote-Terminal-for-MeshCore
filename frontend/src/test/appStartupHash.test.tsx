import React from 'react';
import { render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const mocks = vi.hoisted(() => ({
  api: {
    getRadioConfig: vi.fn(),
    getSettings: vi.fn(),
    getUndecryptedPacketCount: vi.fn(),
    getChannels: vi.fn(),
    getContacts: vi.fn(),
    migratePreferences: vi.fn(),
  },
}));

vi.mock('../api', () => ({
  api: mocks.api,
}));

vi.mock('../useWebSocket', () => ({
  useWebSocket: vi.fn(),
}));

vi.mock('../hooks', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../hooks')>();
  return {
    ...actual,
    useConversationMessages: () => ({
      messages: [],
      messagesLoading: false,
      loadingOlder: false,
      hasOlderMessages: false,
      setMessages: vi.fn(),
      fetchMessages: vi.fn(async () => {}),
      fetchOlderMessages: vi.fn(async () => {}),
      addMessageIfNew: vi.fn(),
      updateMessageAck: vi.fn(),
    }),
    useUnreadCounts: () => ({
      unreadCounts: {},
      mentions: {},
      lastMessageTimes: {},
      incrementUnread: vi.fn(),
      markAllRead: vi.fn(),
      trackNewMessage: vi.fn(),
    }),
    getMessageContentKey: () => 'content-key',
  };
});

vi.mock('../messageCache', () => ({
  addMessage: vi.fn(),
  updateAck: vi.fn(),
  remove: vi.fn(),
}));

vi.mock('../components/StatusBar', () => ({
  StatusBar: () => <div data-testid="status-bar" />,
}));

vi.mock('../components/Sidebar', () => ({
  Sidebar: ({
    activeConversation,
  }: {
    activeConversation: { type: string; id: string; name: string } | null;
  }) => (
    <div data-testid="active-conversation">
      {activeConversation
        ? `${activeConversation.type}:${activeConversation.id}:${activeConversation.name}`
        : 'none'}
    </div>
  ),
}));

vi.mock('../components/MessageList', () => ({
  MessageList: () => <div data-testid="message-list" />,
}));

vi.mock('../components/MessageInput', () => ({
  MessageInput: React.forwardRef((_props, ref) => {
    React.useImperativeHandle(ref, () => ({ appendText: vi.fn() }));
    return <div data-testid="message-input" />;
  }),
}));

vi.mock('../components/NewMessageModal', () => ({
  NewMessageModal: () => null,
}));

vi.mock('../components/SettingsModal', () => ({
  SettingsModal: () => null,
  SETTINGS_SECTION_ORDER: ['radio', 'local', 'database', 'bot'],
  SETTINGS_SECTION_LABELS: {
    radio: 'Radio',
    local: 'Local Configuration',
    database: 'Database & Messaging',
    bot: 'Bot',
  },
}));

vi.mock('../components/RawPacketList', () => ({
  RawPacketList: () => null,
}));

vi.mock('../components/MapView', () => ({
  MapView: () => null,
}));

vi.mock('../components/VisualizerView', () => ({
  VisualizerView: () => null,
}));

vi.mock('../components/CrackerPanel', () => ({
  CrackerPanel: () => null,
}));

vi.mock('../components/ui/sheet', () => ({
  Sheet: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
  SheetContent: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
  SheetHeader: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
  SheetTitle: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
  SheetDescription: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
}));

vi.mock('../components/ui/sonner', () => ({
  Toaster: () => null,
  toast: {
    success: vi.fn(),
    error: vi.fn(),
  },
}));

import { App } from '../App';
import {
  LAST_VIEWED_CONVERSATION_KEY,
  REOPEN_LAST_CONVERSATION_KEY,
} from '../utils/lastViewedConversation';

const publicChannel = {
  key: '8B3387E9C5CDEA6AC9E5EDBAA115CD72',
  name: 'Public',
  is_hashtag: false,
  on_radio: false,
  last_read_at: null,
};

describe('App startup hash resolution', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    localStorage.clear();
    window.location.hash = `#contact/${'a'.repeat(64)}/Alice`;

    mocks.api.getRadioConfig.mockResolvedValue({
      public_key: 'aa'.repeat(32),
      name: 'TestNode',
      lat: 0,
      lon: 0,
      tx_power: 17,
      max_tx_power: 22,
      radio: { freq: 910.525, bw: 62.5, sf: 7, cr: 5 },
    });
    mocks.api.getSettings.mockResolvedValue({
      max_radio_contacts: 200,
      favorites: [],
      auto_decrypt_dm_on_advert: false,
      sidebar_sort_order: 'recent',
      last_message_times: {},
      preferences_migrated: true,
      advert_interval: 0,
      last_advert_time: 0,
      bots: [],
      apprise_enabled: false,
      apprise_url: '',
      apprise_mode: 'all',
      apprise_preserve_identity: true,
      apprise_targets: [],
    });
    mocks.api.getUndecryptedPacketCount.mockResolvedValue({ count: 0 });
    mocks.api.getChannels.mockResolvedValue([publicChannel]);
    mocks.api.getContacts.mockResolvedValue([]);
  });

  afterEach(() => {
    window.location.hash = '';
    localStorage.clear();
  });

  it('falls back to Public when contact hash is unresolvable and contacts are empty', async () => {
    render(<App />);

    await waitFor(() => {
      for (const node of screen.getAllByTestId('active-conversation')) {
        expect(node).toHaveTextContent(`channel:${publicChannel.key}:Public`);
      }
    });
  });

  it('restores last viewed channel when hash is empty and reopen preference is enabled', async () => {
    const chatChannel = {
      key: '11111111111111111111111111111111',
      name: 'Ops',
      is_hashtag: false,
      on_radio: false,
      last_read_at: null,
    };

    window.location.hash = '';
    localStorage.setItem(REOPEN_LAST_CONVERSATION_KEY, '1');
    localStorage.setItem(
      LAST_VIEWED_CONVERSATION_KEY,
      JSON.stringify({
        type: 'channel',
        id: chatChannel.key,
        name: chatChannel.name,
      })
    );
    mocks.api.getChannels.mockResolvedValue([publicChannel, chatChannel]);

    render(<App />);

    await waitFor(() => {
      for (const node of screen.getAllByTestId('active-conversation')) {
        expect(node).toHaveTextContent(`channel:${chatChannel.key}:${chatChannel.name}`);
      }
    });
    expect(window.location.hash).toBe('');
  });

  it('uses Public channel when hash is empty and reopen preference is disabled', async () => {
    const chatChannel = {
      key: '11111111111111111111111111111111',
      name: 'Ops',
      is_hashtag: false,
      on_radio: false,
      last_read_at: null,
    };

    window.location.hash = '';
    localStorage.setItem(
      LAST_VIEWED_CONVERSATION_KEY,
      JSON.stringify({
        type: 'channel',
        id: chatChannel.key,
        name: chatChannel.name,
      })
    );
    mocks.api.getChannels.mockResolvedValue([publicChannel, chatChannel]);

    render(<App />);

    await waitFor(() => {
      for (const node of screen.getAllByTestId('active-conversation')) {
        expect(node).toHaveTextContent(`channel:${publicChannel.key}:Public`);
      }
    });
    expect(window.location.hash).toBe('');
  });

  it('restores last viewed contact from legacy name token when hash is empty and reopen is enabled', async () => {
    const aliceContact = {
      public_key: 'b'.repeat(64),
      name: 'Alice',
      type: 1,
      flags: 0,
      last_path: null,
      last_path_len: -1,
      last_advert: null,
      lat: null,
      lon: null,
      last_seen: null,
      on_radio: false,
      last_contacted: null,
      last_read_at: null,
      first_seen: null,
    };

    window.location.hash = '';
    localStorage.setItem(REOPEN_LAST_CONVERSATION_KEY, '1');
    localStorage.setItem(
      LAST_VIEWED_CONVERSATION_KEY,
      JSON.stringify({
        type: 'contact',
        id: 'Alice',
        name: 'Alice',
      })
    );
    mocks.api.getContacts.mockResolvedValue([aliceContact]);

    render(<App />);

    await waitFor(() => {
      for (const node of screen.getAllByTestId('active-conversation')) {
        expect(node).toHaveTextContent(`contact:${aliceContact.public_key}:Alice`);
      }
    });
    expect(window.location.hash).toBe('');
  });
});
