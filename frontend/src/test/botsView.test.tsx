import { render, screen, fireEvent, waitFor, within } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { BotsView } from '../components/bots/BotsView';
import { api } from '../api';
import type { Bot, BotEngineStatus } from '../types';

function makeBot(overrides: Partial<Bot> = {}): Bot {
  return {
    id: 'bot-1',
    name: 'ping',
    category: 'Basic',
    description: 'Replies pong',
    code: 'from remoteterm import bot',
    enabled: true,
    admin_only: false,
    respond_to_dms: true,
    scope: { channels: 'all' },
    cooldown_seconds: 0,
    per_user_cooldown_seconds: 0,
    queue_threshold_seconds: 0,
    settings_schema: [],
    settings: {},
    ui_triggers: [],
    builtin_key: null,
    builtin_version: null,
    modified: false,
    last_error: null,
    sort_order: 0,
    created_at: 0,
    updated_at: 0,
    declared_keywords: ['ping'],
    declared_crons: [],
    declared_events: [],
    declared_webhooks: [],
    is_legacy: false,
    load_error: null,
    runs_24h: 0,
    ...overrides,
  };
}

const engineStatus: BotEngineStatus = {
  settings: {
    command_prefix: '!',
    require_prefix: false,
    mention_mode: 'also',
    global_reply_seconds: 0,
    per_user_seconds: 0,
    tx_spacing_seconds: 0,
    max_response_hops: 3,
    default_language: 'en',
    auto_detect_language: false,
    banned_users: [],
    profanity_mode: 'off',
    admin_users: [],
  },
  disabled_until_restart: false,
  disabled_by_env: false,
  total_bots: 1,
  enabled_bots: 1,
  erroring_bots: 0,
  runs_24h: 0,
};

// jsdom setup stubs matchMedia with matches:false, so the component sees the
// below-lg (mobile) layout by default. Tests for the desktop path override it.
function setMatchMedia(matches: boolean) {
  Object.defineProperty(window, 'matchMedia', {
    configurable: true,
    writable: true,
    value: (query: string) => ({
      matches,
      media: query,
      onchange: null,
      addListener: () => {},
      removeListener: () => {},
      addEventListener: () => {},
      removeEventListener: () => {},
      dispatchEvent: () => false,
    }),
  });
}

async function renderWorkspace(bots: Bot[] = [makeBot()]) {
  vi.spyOn(api, 'getBots').mockResolvedValue(bots);
  vi.spyOn(api, 'getBotEngine').mockResolvedValue(engineStatus);
  const onOpenBot = vi.fn();
  const onCloseBot = vi.fn();
  render(
    <BotsView
      botId={null}
      channels={[]}
      contacts={[]}
      onOpenBot={onOpenBot}
      onCloseBot={onCloseBot}
    />
  );
  const list = await waitFor(() => {
    const container = screen.getByTestId('bot-list');
    expect(within(container).getByText('Replies pong')).toBeInTheDocument();
    return container;
  });
  return { onOpenBot, list };
}

describe('BotsView bot list', () => {
  afterEach(() => {
    vi.restoreAllMocks();
    setMatchMedia(false);
  });

  it('opens the editor with a single tap below the lg breakpoint', async () => {
    setMatchMedia(false);
    const { onOpenBot, list } = await renderWorkspace();

    fireEvent.click(within(list).getByText('Replies pong'));

    expect(onOpenBot).toHaveBeenCalledWith('bot-1');
  });

  it('offers a visible per-row edit button that opens the editor', async () => {
    setMatchMedia(false);
    const { onOpenBot, list } = await renderWorkspace();

    fireEvent.click(within(list).getByRole('button', { name: 'Edit ping' }));

    expect(onOpenBot).toHaveBeenCalledWith('bot-1');
  });

  it('toggles a bot with the enable switch without opening the editor', async () => {
    setMatchMedia(false);
    const bot = makeBot();
    const updateSpy = vi.spyOn(api, 'updateBot').mockResolvedValue({ ...bot, enabled: false });
    const { onOpenBot, list } = await renderWorkspace([bot]);

    const toggle = within(list).getByRole('switch', { name: 'Enable ping' });
    expect(toggle).toHaveAttribute('aria-checked', 'true');

    fireEvent.click(toggle);

    await waitFor(() => {
      expect(updateSpy).toHaveBeenCalledWith('bot-1', { enabled: false });
    });
    expect(onOpenBot).not.toHaveBeenCalled();
    expect(within(list).getByRole('switch', { name: 'Enable ping' })).toHaveAttribute(
      'aria-checked',
      'false'
    );
  });

  it('only selects the row on click at the lg breakpoint and up', async () => {
    setMatchMedia(true);
    const { onOpenBot, list } = await renderWorkspace();

    fireEvent.click(within(list).getByText('Replies pong'));
    expect(onOpenBot).not.toHaveBeenCalled();

    fireEvent.doubleClick(within(list).getByText('Replies pong'));
    expect(onOpenBot).toHaveBeenCalledWith('bot-1');
  });
});
