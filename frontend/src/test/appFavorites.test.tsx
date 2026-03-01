import React from 'react';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

const mocks = vi.hoisted(() => ({
  api: {
    getRadioConfig: vi.fn(),
    getSettings: vi.fn(),
    getUndecryptedPacketCount: vi.fn(),
    getChannels: vi.fn(),
    getContacts: vi.fn(),
    toggleFavorite: vi.fn(),
    updateSettings: vi.fn(),
    getHealth: vi.fn(),
    sendAdvertisement: vi.fn(),
    rebootRadio: vi.fn(),
    createChannel: vi.fn(),
    decryptHistoricalPackets: vi.fn(),
    createContact: vi.fn(),
    deleteChannel: vi.fn(),
    deleteContact: vi.fn(),
    sendChannelMessage: vi.fn(),
    sendDirectMessage: vi.fn(),
    requestTrace: vi.fn(),
    updateRadioConfig: vi.fn(),
    setPrivateKey: vi.fn(),
    migratePreferences: vi.fn(),
  },
  toast: {
    success: vi.fn(),
    error: vi.fn(),
  },
  hookFns: {
    setMessages: vi.fn(),
    fetchMessages: vi.fn(async () => {}),
    fetchOlderMessages: vi.fn(async () => {}),
    addMessageIfNew: vi.fn(),
    updateMessageAck: vi.fn(),
    incrementUnread: vi.fn(),
    markAllRead: vi.fn(),
    trackNewMessage: vi.fn(),
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
      setMessages: mocks.hookFns.setMessages,
      fetchMessages: mocks.hookFns.fetchMessages,
      fetchOlderMessages: mocks.hookFns.fetchOlderMessages,
      addMessageIfNew: mocks.hookFns.addMessageIfNew,
      updateMessageAck: mocks.hookFns.updateMessageAck,
    }),
    useUnreadCounts: () => ({
      unreadCounts: {},
      mentions: {},
      lastMessageTimes: {},
      incrementUnread: mocks.hookFns.incrementUnread,
      markAllRead: mocks.hookFns.markAllRead,
      trackNewMessage: mocks.hookFns.trackNewMessage,
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
  StatusBar: ({
    settingsMode,
    onSettingsClick,
  }: {
    settingsMode?: boolean;
    onSettingsClick: () => void;
  }) => (
    <button type="button" onClick={onSettingsClick} data-testid="status-bar-settings-toggle">
      {settingsMode ? 'Back to Chat' : 'Radio & Config'}
    </button>
  ),
}));

vi.mock('../components/Sidebar', () => ({
  Sidebar: () => <div data-testid="sidebar" />,
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
  SettingsModal: ({ desktopSection }: { desktopSection?: string }) => (
    <div data-testid="settings-modal-section">{desktopSection ?? 'none'}</div>
  ),
  SETTINGS_SECTION_ORDER: ['radio', 'local', 'database', 'bot'],
  SETTINGS_SECTION_LABELS: {
    radio: '📻 Radio',
    local: '🖥️ Local Configuration',
    database: '🗄️ Database & Messaging',
    bot: '🤖 Bot',
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
  toast: mocks.toast,
}));

vi.mock('../utils/urlHash', () => ({
  parseHashConversation: () => null,
  updateUrlHash: vi.fn(),
  getMapFocusHash: () => '#map',
}));

import { App } from '../App';

const baseConfig = {
  public_key: 'aa'.repeat(32),
  name: 'TestNode',
  lat: 0,
  lon: 0,
  tx_power: 17,
  max_tx_power: 22,
  radio: { freq: 910.525, bw: 62.5, sf: 7, cr: 5 },
};

const baseSettings = {
  max_radio_contacts: 200,
  favorites: [] as Array<{ type: 'channel' | 'contact'; id: string }>,
  auto_decrypt_dm_on_advert: false,
  sidebar_sort_order: 'recent' as const,
  last_message_times: {},
  preferences_migrated: false,
  advert_interval: 0,
  last_advert_time: 0,
  bots: [],
  apprise_enabled: false,
  apprise_url: '',
  apprise_mode: 'all' as const,
  apprise_preserve_identity: true,
  apprise_targets: [] as Array<{ type: 'channel' | 'contact'; id: string }>,
};

const publicChannel = {
  key: '8B3387E9C5CDEA6AC9E5EDBAA115CD72',
  name: 'Public',
  is_hashtag: false,
  on_radio: false,
  last_read_at: null,
};

describe('App favorite toggle flow', () => {
  beforeEach(() => {
    vi.clearAllMocks();

    mocks.api.getRadioConfig.mockResolvedValue(baseConfig);
    mocks.api.getSettings.mockResolvedValue({ ...baseSettings });
    mocks.api.getUndecryptedPacketCount.mockResolvedValue({ count: 0 });
    mocks.api.getChannels.mockResolvedValue([publicChannel]);
    mocks.api.getContacts.mockResolvedValue([]);
    mocks.api.toggleFavorite.mockResolvedValue({
      ...baseSettings,
      favorites: [{ type: 'channel', id: publicChannel.key }],
    });
  });

  it('optimistically toggles favorite and persists on success', async () => {
    render(<App />);

    await waitFor(() => {
      expect(screen.getByTitle('Add to favorites')).toBeInTheDocument();
    });

    fireEvent.click(screen.getByTitle('Add to favorites'));

    await waitFor(() => {
      expect(mocks.api.toggleFavorite).toHaveBeenCalledWith('channel', publicChannel.key);
    });

    await waitFor(() => {
      expect(screen.getByTitle('Remove from favorites')).toBeInTheDocument();
    });
  });

  it('rolls back favorite state by refetching settings on toggle failure', async () => {
    mocks.api.toggleFavorite.mockRejectedValue(new Error('toggle failed'));
    mocks.api.getSettings
      .mockResolvedValueOnce({ ...baseSettings }) // initial load
      .mockResolvedValueOnce({ ...baseSettings }); // rollback refetch

    render(<App />);

    await waitFor(() => {
      expect(screen.getByTitle('Add to favorites')).toBeInTheDocument();
    });

    fireEvent.click(screen.getByTitle('Add to favorites'));

    await waitFor(() => {
      expect(mocks.api.toggleFavorite).toHaveBeenCalledWith('channel', publicChannel.key);
    });

    await waitFor(() => {
      expect(mocks.api.getSettings).toHaveBeenCalledTimes(2);
    });

    await waitFor(() => {
      expect(mocks.toast.error).toHaveBeenCalledWith('Failed to update favorite');
    });

    await waitFor(() => {
      expect(screen.getByTitle('Add to favorites')).toBeInTheDocument();
    });
  });

  it('toggles settings page mode and syncs selected section into SettingsModal', async () => {
    render(<App />);

    await waitFor(() => {
      expect(screen.getByRole('button', { name: 'Radio & Config' })).toBeInTheDocument();
    });

    fireEvent.click(screen.getByRole('button', { name: 'Radio & Config' }));

    await waitFor(() => {
      expect(screen.getByRole('button', { name: 'Back to Chat' })).toBeInTheDocument();
      expect(screen.getByTestId('settings-modal-section')).toHaveTextContent('radio');
    });

    fireEvent.click(screen.getAllByRole('button', { name: /Local Configuration/i })[0]);

    await waitFor(() => {
      expect(screen.getByTestId('settings-modal-section')).toHaveTextContent('local');
    });

    fireEvent.click(screen.getByRole('button', { name: 'Back to Chat' }));

    await waitFor(() => {
      expect(screen.getByRole('button', { name: 'Radio & Config' })).toBeInTheDocument();
      expect(screen.queryByTestId('settings-modal-section')).not.toBeInTheDocument();
    });
  });
});
