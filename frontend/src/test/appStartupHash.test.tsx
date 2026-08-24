import React from 'react';
import { configure, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const mocks = vi.hoisted(() => ({
  api: {
    getRadioConfig: vi.fn(),
    getSettings: vi.fn(),
    getUndecryptedPacketCount: vi.fn(),
    getChannels: vi.fn(),
    getContacts: vi.fn(),
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
      hasNewerMessages: false,
      loadingNewer: false,
      fetchOlderMessages: vi.fn(async () => {}),
      fetchNewerMessages: vi.fn(async () => {}),
      jumpToBottom: vi.fn(),
      reloadCurrentConversation: vi.fn(),
      observeMessage: vi.fn(() => ({ added: false, activeConversation: false })),
      receiveMessageAck: vi.fn(),
      reconcileOnReconnect: vi.fn(),
      renameConversationMessages: vi.fn(),
      removeConversationMessages: vi.fn(),
      clearConversationMessages: vi.fn(),
    }),
    useUnreadCounts: () => ({
      unreadCounts: {},
      mentions: {},
      lastMessageTimes: {},
      unreadLastReadAts: {},
      firstUnreadIds: {},
      recordMessageEvent: vi.fn(),
      renameConversationState: vi.fn(),
      markAllRead: vi.fn(),
      refreshUnreads: vi.fn(async () => {}),
    }),
  };
});

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
  SettingsModal: ({ desktopSection }: { desktopSection?: string }) => (
    <div data-testid="settings-modal-section">{desktopSection ?? 'none'}</div>
  ),
  SETTINGS_SECTION_ORDER: ['radio', 'local', 'radio-app', 'database', 'bot'],
  SETTINGS_SECTION_LABELS: {
    radio: 'Radio',
    local: 'Local Configuration',
    'radio-app': 'Radio-App Management',
    database: 'Database',
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

vi.mock('../components/bots/BotsView', () => ({
  BotsView: ({ botId }: { botId: string | null }) => (
    <div data-testid="bots-view">{botId ?? 'workspace'}</div>
  ),
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

import { act } from '@testing-library/react';
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
  favorite: false,
};

// Modest timeout headroom for genuine CPU contention under the full parallel
// suite (a healthy render settles in ~100ms). Must run at module scope, not in
// beforeAll: vitest bakes each test's timeout in when it() registers during
// collection, before any beforeAll runs. vi.setConfig is scoped to this file.
vi.setConfig({ testTimeout: 15000 });
configure({ asyncUtilTimeout: 5000 });

// Set the URL hash the way the app reads it, WITHOUT the jsdom side effect that
// makes this suite flaky. Assigning `window.location.hash = ...` queues an
// *asynchronous* popstate in jsdom (real browsers only fire hashchange); those
// stray popstates drain during a later test and reset the App's resolved
// conversation to null via the popstate handler, permanently stranding it.
// history.replaceState updates the hash without ever queuing a popstate.
function setHash(hash: string) {
  window.history.replaceState(null, '', hash || window.location.pathname);
}

describe('App startup hash resolution', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    localStorage.clear();
    setHash(`#contact/${'a'.repeat(64)}/Alice`);

    mocks.api.getRadioConfig.mockResolvedValue({
      public_key: 'aa'.repeat(32),
      name: 'TestNode',
      lat: 0,
      lon: 0,
      tx_power: 17,
      max_tx_power: 22,
      radio: { freq: 910.525, bw: 62.5, sf: 7, cr: 5 },
      path_hash_mode: 0,
      path_hash_mode_supported: false,
    });
    mocks.api.getSettings.mockResolvedValue({
      max_radio_contacts: 200,
      auto_decrypt_dm_on_advert: false,
      last_message_times: {},

      advert_interval: 0,
      last_advert_time: 0,
    });
    mocks.api.getUndecryptedPacketCount.mockResolvedValue({ count: 0 });
    mocks.api.getChannels.mockResolvedValue([publicChannel]);
    mocks.api.getContacts.mockResolvedValue([]);
  });

  afterEach(() => {
    // Reset via replaceState (never queues a popstate) so no hash state leaks
    // into the next test. beforeEach also sets a known hash before each render.
    setHash('');
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

  it('restores the trace tool from the URL hash', async () => {
    setHash('#trace');

    render(<App />);

    await waitFor(() => {
      for (const node of screen.getAllByTestId('active-conversation')) {
        expect(node).toHaveTextContent('trace:trace:Trace');
      }
    });
  });

  it('restores the trace tool from the URL hash even when channels are unavailable', async () => {
    setHash('#trace');
    mocks.api.getChannels.mockResolvedValue([]);

    render(<App />);

    await waitFor(() => {
      for (const node of screen.getAllByTestId('active-conversation')) {
        expect(node).toHaveTextContent('trace:trace:Trace');
      }
    });
  });

  it('restores the bots workspace from the URL hash instead of falling back to Public', async () => {
    setHash('#bots');

    render(<App />);

    await waitFor(() => {
      for (const node of screen.getAllByTestId('active-conversation')) {
        expect(node).toHaveTextContent('bots:bots:Bots');
      }
    });
    expect(window.location.hash).toBe('#bots');
    await waitFor(() => {
      expect(screen.getByTestId('bots-view')).toHaveTextContent('workspace');
    });
  });

  it('restores a bots editor deep link with its bot id from the URL hash', async () => {
    setHash('#bots/abc-123');
    mocks.api.getChannels.mockResolvedValue([]);

    render(<App />);

    await waitFor(() => {
      for (const node of screen.getAllByTestId('active-conversation')) {
        expect(node).toHaveTextContent('bots:bots:Bots');
      }
    });
    // The bot id must survive resolution: the hash keeps the /{id} suffix and
    // the workspace opens straight into that bot's editor.
    expect(window.location.hash).toBe('#bots/abc-123');
    await waitFor(() => {
      expect(screen.getByTestId('bots-view')).toHaveTextContent('abc-123');
    });
  });

  it('reopens the last viewed trace tool even when channels are unavailable', async () => {
    setHash('');
    localStorage.setItem(REOPEN_LAST_CONVERSATION_KEY, '1');
    localStorage.setItem(
      LAST_VIEWED_CONVERSATION_KEY,
      JSON.stringify({
        type: 'trace',
        id: 'trace',
        name: 'Trace',
      })
    );
    mocks.api.getChannels.mockResolvedValue([]);

    render(<App />);

    await waitFor(() => {
      for (const node of screen.getAllByTestId('active-conversation')) {
        expect(node).toHaveTextContent('trace:trace:Trace');
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
      favorite: false,
    };

    setHash('');
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
      favorite: false,
    };

    setHash('');
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

  it('tracks the current conversation in local storage even before reopen is enabled', async () => {
    const chatChannel = {
      key: '11111111111111111111111111111111',
      name: 'Ops',
      is_hashtag: false,
      on_radio: false,
      last_read_at: null,
      favorite: false,
    };

    setHash('');
    mocks.api.getChannels.mockResolvedValue([publicChannel, chatChannel]);
    localStorage.setItem(
      LAST_VIEWED_CONVERSATION_KEY,
      JSON.stringify({
        type: 'channel',
        id: chatChannel.key,
        name: chatChannel.name,
      })
    );

    render(<App />);

    await waitFor(() => {
      for (const node of screen.getAllByTestId('active-conversation')) {
        expect(node).toHaveTextContent(`channel:${publicChannel.key}:Public`);
      }
    });

    expect(localStorage.getItem(LAST_VIEWED_CONVERSATION_KEY)).toContain(publicChannel.key);
  });

  it('restores last viewed contact from legacy name token when hash is empty and reopen is enabled', async () => {
    const aliceContact = {
      public_key: 'b'.repeat(64),
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
    };

    setHash('');
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

  describe('Browser back/forward navigation', () => {
    it('navigates to a channel conversation when popstate fires', async () => {
      const opsChannel = {
        key: 'CCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCC',
        name: 'Ops',
        is_hashtag: false,
        on_radio: false,
        last_read_at: null,
        favorite: false,
      };

      setHash(`#channel/${opsChannel.key}/Ops`);
      mocks.api.getChannels.mockResolvedValue([publicChannel, opsChannel]);

      render(<App />);

      await waitFor(() => {
        for (const node of screen.getAllByTestId('active-conversation')) {
          expect(node).toHaveTextContent(`channel:${opsChannel.key}:Ops`);
        }
      });

      act(() => {
        setHash(`#channel/${publicChannel.key}/Public`);
        window.dispatchEvent(new PopStateEvent('popstate', { state: null }));
      });

      await waitFor(() => {
        for (const node of screen.getAllByTestId('active-conversation')) {
          expect(node).toHaveTextContent(`channel:${publicChannel.key}:Public`);
        }
      });
    });

    it('navigates to a contact conversation when popstate fires with a contact hash', async () => {
      const aliceContact = {
        public_key: 'b'.repeat(64),
        name: 'Alice',
        type: 1,
        flags: 0,
        direct_path: null,
        direct_path_len: -1,
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

      setHash('');
      mocks.api.getContacts.mockResolvedValue([aliceContact]);

      render(<App />);

      await waitFor(() => {
        for (const node of screen.getAllByTestId('active-conversation')) {
          expect(node).toHaveTextContent(`channel:${publicChannel.key}:Public`);
        }
      });

      // Flush any pending React work (contacts render + its effects) so that
      // contactsRef.current is populated before the popstate handler reads it.
      // waitFor may resolve after the channels render commits but before the
      // contacts render commits, leaving contactsRef.current=[].
      await act(async () => {});

      act(() => {
        setHash(`#contact/${aliceContact.public_key}/Alice`);
        window.dispatchEvent(new PopStateEvent('popstate', { state: null }));
      });

      await waitFor(() => {
        for (const node of screen.getAllByTestId('active-conversation')) {
          expect(node).toHaveTextContent(`contact:${aliceContact.public_key}:Alice`);
        }
      });
    });

    it('navigates to the bots workspace when popstate fires with a bots hash', async () => {
      setHash('');
      render(<App />);

      await waitFor(() => {
        for (const node of screen.getAllByTestId('active-conversation')) {
          expect(node).toHaveTextContent(`channel:${publicChannel.key}:Public`);
        }
      });

      act(() => {
        setHash('#bots/abc-123');
        window.dispatchEvent(new PopStateEvent('popstate', { state: null }));
      });

      await waitFor(() => {
        for (const node of screen.getAllByTestId('active-conversation')) {
          expect(node).toHaveTextContent('bots:bots:Bots');
        }
      });
      await waitFor(() => {
        expect(screen.getByTestId('bots-view')).toHaveTextContent('abc-123');
      });
    });

    it('recovers to Public when a popstate lands on an empty/unresolvable hash', async () => {
      setHash('');
      render(<App />);

      await waitFor(() => {
        for (const node of screen.getAllByTestId('active-conversation')) {
          expect(node).toHaveTextContent(`channel:${publicChannel.key}:Public`);
        }
      });

      // A back/forward navigation to the bare URL (empty hash) must not strand
      // the app on a blank conversation: the initial-load phases are gated by
      // hasSetDefaultConversation and won't re-resolve, so the popstate handler
      // itself has to fall back to Public rather than clearing to null.
      act(() => {
        setHash('');
        window.dispatchEvent(new PopStateEvent('popstate', { state: null }));
      });

      await waitFor(() => {
        for (const node of screen.getAllByTestId('active-conversation')) {
          expect(node).toHaveTextContent(`channel:${publicChannel.key}:Public`);
        }
      });
    });
  });

  it('stays on radio settings section even when radio is disconnected', async () => {
    setHash('#settings/radio');
    mocks.api.getRadioConfig.mockRejectedValue(new Error('radio offline'));

    render(<App />);

    await waitFor(() => {
      expect(screen.getByTestId('settings-modal-section')).toHaveTextContent('radio');
    });

    // Section stays on radio (no redirect to local) and hash is preserved
    expect(window.location.hash).toBe('#settings/radio');
  });
});
