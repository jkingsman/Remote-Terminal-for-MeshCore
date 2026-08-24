import { render, screen, waitFor, fireEvent, within } from '@testing-library/react';
import { describe, expect, it, vi, beforeEach } from 'vitest';
import { SettingsFanoutSection } from '../components/settings/SettingsFanoutSection';
import type { HealthStatus, FanoutConfig } from '../types';

// Mock the api module
vi.mock('../api', () => ({
  api: {
    getFanoutConfigs: vi.fn(),
    createFanoutConfig: vi.fn(),
    updateFanoutConfig: vi.fn(),
    deleteFanoutConfig: vi.fn(),
    getChannels: vi.fn(),
    getContacts: vi.fn(),
    getSettings: vi.fn(),
    getRadioConfig: vi.fn(),
  },
}));

import { api } from '../api';

const mockedApi = vi.mocked(api);

const baseHealth: HealthStatus = {
  status: 'connected',
  radio_connected: true,
  radio_initializing: false,
  connection_info: 'Serial: /dev/ttyUSB0',
  database_size_mb: 1.2,
  oldest_undecrypted_timestamp: null,
  fanout_statuses: {},
  bots_disabled: false,
};

const webhookConfig: FanoutConfig = {
  id: 'wh-1',
  type: 'webhook',
  name: 'Test Hook',
  enabled: true,
  config: { url: 'https://example.com/hook', method: 'POST', headers: {} },
  scope: { messages: 'all', raw_packets: 'none' },
  sort_order: 0,
  created_at: 1000,
};

function renderSection(overrides?: { health?: HealthStatus }) {
  return render(
    <SettingsFanoutSection
      health={overrides?.health ?? baseHealth}
      onHealthRefresh={vi.fn(async () => {})}
    />
  );
}

function renderSectionWithRefresh(
  onHealthRefresh: () => Promise<void>,
  overrides?: { health?: HealthStatus }
) {
  return render(
    <SettingsFanoutSection
      health={overrides?.health ?? baseHealth}
      onHealthRefresh={onHealthRefresh}
    />
  );
}

function startsWithAccessibleName(name: string) {
  const escaped = name.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
  return new RegExp(`^${escaped}(?:\\s|$)`);
}

async function openCreateIntegrationDialog() {
  await waitFor(() => {
    expect(screen.getByRole('button', { name: 'Add Integration' })).toBeInTheDocument();
  });
  fireEvent.click(screen.getByRole('button', { name: 'Add Integration' }));
  return screen.findByRole('dialog', { name: 'Create Integration' });
}

function selectCreateIntegration(name: string) {
  const dialog = screen.getByRole('dialog', { name: 'Create Integration' });
  fireEvent.click(within(dialog).getByRole('button', { name: startsWithAccessibleName(name) }));
}

function confirmCreateIntegration() {
  const dialog = screen.getByRole('dialog', { name: 'Create Integration' });
  fireEvent.click(within(dialog).getByRole('button', { name: 'Create' }));
}

beforeEach(() => {
  vi.clearAllMocks();
  vi.spyOn(window, 'confirm').mockReturnValue(true);
  mockedApi.getFanoutConfigs.mockResolvedValue([]);
  mockedApi.getChannels.mockResolvedValue([]);
  mockedApi.getContacts.mockResolvedValue([]);
  mockedApi.getSettings.mockResolvedValue({
    max_radio_contacts: 200,
    auto_decrypt_dm_on_advert: true,
    last_message_times: {},
    advert_interval: 0,
    last_advert_time: 0,
    flood_scope: '',
    known_regions: [],
    blocked_keys: [],
    blocked_names: [],
    discovery_blocked_types: [],
    tracked_telemetry_repeaters: [],
    tracked_telemetry_contacts: [],
    auto_resend_channel: false,
    telemetry_interval_hours: 8,
    telemetry_routed_hourly: false,
  });
  mockedApi.getRadioConfig.mockResolvedValue({
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
});

describe('SettingsFanoutSection', () => {
  it('shows add integration dialog with all integration types', async () => {
    renderSection();
    const dialog = await openCreateIntegrationDialog();

    const optionButtons = within(dialog)
      .getAllByRole('button')
      .filter((button) => button.hasAttribute('aria-pressed'));
    expect(optionButtons).toHaveLength(12);
    expect(within(dialog).getByRole('button', { name: 'Close' })).toBeInTheDocument();
    expect(within(dialog).getByRole('button', { name: 'Create' })).toBeInTheDocument();
    expect(
      within(dialog).getByRole('button', { name: startsWithAccessibleName('Private MQTT') })
    ).toBeInTheDocument();
    expect(
      within(dialog).getByRole('button', { name: startsWithAccessibleName('MeshRank') })
    ).toBeInTheDocument();
    expect(
      within(dialog).getByRole('button', { name: startsWithAccessibleName('LetsMesh (US)') })
    ).toBeInTheDocument();
    expect(
      within(dialog).getByRole('button', { name: startsWithAccessibleName('LetsMesh (EU)') })
    ).toBeInTheDocument();
    expect(
      within(dialog).getByRole('button', {
        name: startsWithAccessibleName('Community MQTT/meshcoretomqtt'),
      })
    ).toBeInTheDocument();
    expect(
      within(dialog).getByRole('button', { name: startsWithAccessibleName('Webhook') })
    ).toBeInTheDocument();
    expect(
      within(dialog).getByRole('button', { name: startsWithAccessibleName('Apprise') })
    ).toBeInTheDocument();
    expect(
      within(dialog).getByRole('button', { name: startsWithAccessibleName('Amazon SQS') })
    ).toBeInTheDocument();
    expect(
      within(dialog).getByRole('button', { name: startsWithAccessibleName('Discord Bridge') })
    ).toBeInTheDocument();
    expect(
      within(dialog).getByRole('button', { name: startsWithAccessibleName('Telegram Bridge') })
    ).toBeInTheDocument();
    expect(
      within(dialog).getByRole('button', { name: startsWithAccessibleName('Map Upload') })
    ).toBeInTheDocument();
    expect(within(dialog).getByRole('heading', { level: 3 })).toBeInTheDocument();

    const genericCommunityIndex = optionButtons.findIndex((button) =>
      button.textContent?.startsWith('Community MQTT/meshcoretomqtt')
    );
    const meshRankIndex = optionButtons.findIndex((button) =>
      button.textContent?.startsWith('MeshRank')
    );
    expect(genericCommunityIndex).toBeGreaterThan(-1);
    expect(meshRankIndex).toBeGreaterThan(-1);
    expect(genericCommunityIndex).toBeLessThan(meshRankIndex);
  });

  it('no longer offers the Python Bot integration type', async () => {
    renderSection();
    const dialog = await openCreateIntegrationDialog();
    expect(
      within(dialog).queryByRole('button', { name: startsWithAccessibleName('Python Bot') })
    ).not.toBeInTheDocument();
  });

  it('shows the bots-have-moved banner on the list view', async () => {
    renderSection();
    await waitFor(() => {
      expect(screen.getByText('Python bots have moved.')).toBeInTheDocument();
      expect(screen.getByText(/now live in the Bots workspace/)).toBeInTheDocument();
    });
  });

  it('new Discord Bridge draft shows mapping and moderation fields', async () => {
    renderSection();
    await openCreateIntegrationDialog();
    selectCreateIntegration('Discord Bridge');
    confirmCreateIntegration();

    await waitFor(() => {
      expect(screen.getByText('← Back to list')).toBeInTheDocument();
      expect(screen.getByLabelText('Name')).toHaveValue('Discord Bridge #1');
      expect(screen.getByText('Bridged channels → Discord webhooks')).toBeInTheDocument();
      expect(screen.getByRole('button', { name: '+ Add mapping' })).toBeInTheDocument();
      expect(screen.getByLabelText('Profanity filter')).toBeInTheDocument();
      expect(screen.getByText("Also bridge this app's own outgoing messages")).toBeInTheDocument();
    });
    // Scope is fixed server-side — no scope selector for bridges.
    expect(screen.queryByText('Forward raw packets')).not.toBeInTheDocument();
    expect(mockedApi.createFanoutConfig).not.toHaveBeenCalled();
  });

  it('new Telegram Bridge draft shows token and chat mapping fields', async () => {
    renderSection();
    await openCreateIntegrationDialog();
    selectCreateIntegration('Telegram Bridge');
    confirmCreateIntegration();

    await waitFor(() => {
      expect(screen.getByLabelText('Name')).toHaveValue('Telegram Bridge #1');
      expect(screen.getByLabelText('Bot API token')).toBeInTheDocument();
      expect(screen.getByText('Bridged channels → Telegram chats')).toBeInTheDocument();
      expect(screen.getByRole('button', { name: '+ Add mapping' })).toBeInTheDocument();
    });

    fireEvent.click(screen.getByRole('button', { name: '+ Add mapping' }));
    await waitFor(() => {
      expect(screen.getByLabelText('Mapping 1 channel')).toBeInTheDocument();
      expect(screen.getByLabelText('Mapping 1 chat id')).toBeInTheDocument();
    });
  });

  it('lists existing configs after load', async () => {
    mockedApi.getFanoutConfigs.mockResolvedValue([webhookConfig]);
    renderSection();
    await waitFor(() => {
      expect(screen.getByText('Test Hook')).toBeInTheDocument();
    });
  });

  it('shows an error info button and dialog when the integration has a retained error', async () => {
    mockedApi.getFanoutConfigs.mockResolvedValue([webhookConfig]);
    renderSection({
      health: {
        ...baseHealth,
        fanout_statuses: {
          'wh-1': {
            name: 'Test Hook',
            type: 'webhook',
            status: 'error',
            last_error: 'HTTP 500',
          },
        },
      },
    });

    await waitFor(() => {
      expect(screen.getByText('Test Hook')).toBeInTheDocument();
    });

    fireEvent.click(screen.getByRole('button', { name: 'View error details for Test Hook' }));

    expect(screen.getByRole('dialog', { name: 'Test Hook Error' })).toBeInTheDocument();
    expect(screen.getByText('HTTP 500')).toBeInTheDocument();
  });

  it('does not show an error info button when the integration has no retained error', async () => {
    mockedApi.getFanoutConfigs.mockResolvedValue([webhookConfig]);
    renderSection({
      health: {
        ...baseHealth,
        fanout_statuses: {
          'wh-1': {
            name: 'Test Hook',
            type: 'webhook',
            status: 'connected',
          },
        },
      },
    });

    await waitFor(() => {
      expect(screen.getByText('Test Hook')).toBeInTheDocument();
    });

    expect(
      screen.queryByRole('button', { name: 'View error details for Test Hook' })
    ).not.toBeInTheDocument();
  });

  it('navigates to edit view when clicking edit', async () => {
    mockedApi.getFanoutConfigs.mockResolvedValue([webhookConfig]);
    renderSection();
    await waitFor(() => {
      expect(screen.getByText('Test Hook')).toBeInTheDocument();
    });

    const editBtn = screen.getByRole('button', { name: 'Edit' });
    fireEvent.click(editBtn);

    await waitFor(() => {
      expect(screen.getByText('← Back to list')).toBeInTheDocument();
    });
  });

  it('save as enabled returns to list even if health refresh fails', async () => {
    mockedApi.getFanoutConfigs.mockResolvedValue([webhookConfig]);
    mockedApi.updateFanoutConfig.mockResolvedValue({ ...webhookConfig, enabled: true });
    const failingRefresh = vi.fn(async () => {
      throw new Error('refresh failed');
    });

    renderSectionWithRefresh(failingRefresh);
    await waitFor(() => expect(screen.getByText('Test Hook')).toBeInTheDocument());

    fireEvent.click(screen.getByRole('button', { name: 'Edit' }));
    await waitFor(() => expect(screen.getByText('← Back to list')).toBeInTheDocument());

    fireEvent.click(screen.getByRole('button', { name: 'Save as Enabled' }));

    await waitFor(() => expect(screen.queryByText('← Back to list')).not.toBeInTheDocument());
    expect(screen.getByText('Test Hook')).toBeInTheDocument();
  });

  it('calls toggle enabled on checkbox click', async () => {
    mockedApi.getFanoutConfigs.mockResolvedValue([webhookConfig]);
    mockedApi.updateFanoutConfig.mockResolvedValue({ ...webhookConfig, enabled: false });
    renderSection();
    await waitFor(() => {
      expect(screen.getByText('Test Hook')).toBeInTheDocument();
    });

    const checkbox = screen.getByRole('checkbox');
    fireEvent.click(checkbox);

    await waitFor(() => {
      expect(mockedApi.updateFanoutConfig).toHaveBeenCalledWith('wh-1', { enabled: false });
    });
  });

  it('webhook with persisted "none" scope renders "All messages" selected', async () => {
    const wh: FanoutConfig = {
      ...webhookConfig,
      scope: { messages: 'none', raw_packets: 'none' },
    };
    mockedApi.getFanoutConfigs.mockResolvedValue([wh]);
    renderSection();
    await waitFor(() => expect(screen.getByText('Test Hook')).toBeInTheDocument());

    fireEvent.click(screen.getByRole('button', { name: 'Edit' }));
    await waitFor(() => expect(screen.getByText('← Back to list')).toBeInTheDocument());

    // "none" is not a valid mode without raw packets — should fall back to "all"
    const allRadio = screen.getByLabelText('All messages');
    expect(allRadio).toBeChecked();
  });

  it('does not show "No messages" scope option for webhook', async () => {
    const wh: FanoutConfig = {
      ...webhookConfig,
      scope: { messages: 'all', raw_packets: 'none' },
    };
    mockedApi.getFanoutConfigs.mockResolvedValue([wh]);
    renderSection();
    await waitFor(() => expect(screen.getByText('Test Hook')).toBeInTheDocument());

    fireEvent.click(screen.getByRole('button', { name: 'Edit' }));
    await waitFor(() => expect(screen.getByText('← Back to list')).toBeInTheDocument());

    expect(screen.getByText('All messages')).toBeInTheDocument();
    expect(screen.queryByText('No messages')).not.toBeInTheDocument();
  });

  it('shows empty scope warning when "only" mode has nothing selected', async () => {
    const wh: FanoutConfig = {
      ...webhookConfig,
      scope: { messages: { channels: [], contacts: [] }, raw_packets: 'none' },
    };
    mockedApi.getFanoutConfigs.mockResolvedValue([wh]);
    renderSection();
    await waitFor(() => expect(screen.getByText('Test Hook')).toBeInTheDocument());

    fireEvent.click(screen.getByRole('button', { name: 'Edit' }));
    await waitFor(() => expect(screen.getByText('← Back to list')).toBeInTheDocument());

    expect(screen.getByText(/will not forward any data/)).toBeInTheDocument();
  });

  it('shows warning for private MQTT when both scope axes are off', async () => {
    const mqtt: FanoutConfig = {
      id: 'mqtt-1',
      type: 'mqtt_private',
      name: 'My MQTT',
      enabled: true,
      config: { broker_host: 'localhost', broker_port: 1883 },
      scope: { messages: 'none', raw_packets: 'none' },
      sort_order: 0,
      created_at: 1000,
    };
    mockedApi.getFanoutConfigs.mockResolvedValue([mqtt]);
    renderSection();
    await waitFor(() => expect(screen.getByText('My MQTT')).toBeInTheDocument());

    fireEvent.click(screen.getByRole('button', { name: 'Edit' }));
    await waitFor(() => expect(screen.getByText('← Back to list')).toBeInTheDocument());

    expect(screen.getByText(/will not forward any data/)).toBeInTheDocument();
  });

  it('private MQTT shows raw packets toggle and No messages option', async () => {
    const mqtt: FanoutConfig = {
      id: 'mqtt-1',
      type: 'mqtt_private',
      name: 'My MQTT',
      enabled: true,
      config: { broker_host: 'localhost', broker_port: 1883 },
      scope: { messages: 'all', raw_packets: 'all' },
      sort_order: 0,
      created_at: 1000,
    };
    mockedApi.getFanoutConfigs.mockResolvedValue([mqtt]);
    renderSection();
    await waitFor(() => expect(screen.getByText('My MQTT')).toBeInTheDocument());

    fireEvent.click(screen.getByRole('button', { name: 'Edit' }));
    await waitFor(() => expect(screen.getByText('← Back to list')).toBeInTheDocument());

    expect(screen.getByText('Forward raw packets')).toBeInTheDocument();
    expect(screen.getByText('No messages')).toBeInTheDocument();
  });

  it('private MQTT hides warning when raw packets enabled but messages off', async () => {
    const mqtt: FanoutConfig = {
      id: 'mqtt-1',
      type: 'mqtt_private',
      name: 'My MQTT',
      enabled: true,
      config: { broker_host: 'localhost', broker_port: 1883 },
      scope: { messages: 'none', raw_packets: 'all' },
      sort_order: 0,
      created_at: 1000,
    };
    mockedApi.getFanoutConfigs.mockResolvedValue([mqtt]);
    renderSection();
    await waitFor(() => expect(screen.getByText('My MQTT')).toBeInTheDocument());

    fireEvent.click(screen.getByRole('button', { name: 'Edit' }));
    await waitFor(() => expect(screen.getByText('← Back to list')).toBeInTheDocument());

    expect(screen.queryByText(/will not forward any data/)).not.toBeInTheDocument();
  });

  it('navigates to create view when clicking add button', async () => {
    renderSection();
    await openCreateIntegrationDialog();
    selectCreateIntegration('Webhook');
    confirmCreateIntegration();

    await waitFor(() => {
      expect(screen.getByText('← Back to list')).toBeInTheDocument();
      expect(screen.getByLabelText('Name')).toHaveValue('Webhook #1');
      // Should show the URL input for webhook type
      expect(screen.getByLabelText(/URL/)).toBeInTheDocument();
    });

    expect(mockedApi.createFanoutConfig).not.toHaveBeenCalled();
  });

  it('new SQS draft shows queue url fields and sensible defaults', async () => {
    renderSection();
    await openCreateIntegrationDialog();
    selectCreateIntegration('Amazon SQS');
    confirmCreateIntegration();

    await waitFor(() => {
      expect(screen.getByText('← Back to list')).toBeInTheDocument();
      expect(screen.getByLabelText('Name')).toHaveValue('Amazon SQS #1');
      expect(screen.getByLabelText('Queue URL')).toBeInTheDocument();
      expect(screen.getByText('Forward raw packets')).toBeInTheDocument();
    });
  });

  it('backing out of a new draft does not create an integration', async () => {
    renderSection();
    await openCreateIntegrationDialog();
    selectCreateIntegration('Webhook');
    confirmCreateIntegration();
    await waitFor(() => expect(screen.getByText('← Back to list')).toBeInTheDocument());

    fireEvent.click(screen.getByText('← Back to list'));

    expect(window.confirm).toHaveBeenCalledWith('Leave without saving?');
    await waitFor(() => expect(screen.queryByText('← Back to list')).not.toBeInTheDocument());
    expect(mockedApi.createFanoutConfig).not.toHaveBeenCalled();
  });

  it('back to list does not ask for confirmation when an existing integration is unchanged', async () => {
    mockedApi.getFanoutConfigs.mockResolvedValue([webhookConfig]);
    renderSection();
    await waitFor(() => expect(screen.getByText('Test Hook')).toBeInTheDocument());

    fireEvent.click(screen.getByRole('button', { name: 'Edit' }));
    await waitFor(() => expect(screen.getByText('← Back to list')).toBeInTheDocument());

    fireEvent.click(screen.getByText('← Back to list'));

    expect(window.confirm).not.toHaveBeenCalled();
    await waitFor(() => expect(screen.queryByText('← Back to list')).not.toBeInTheDocument());
  });

  it('back to list asks for confirmation after editing an existing integration', async () => {
    mockedApi.getFanoutConfigs.mockResolvedValue([webhookConfig]);
    renderSection();
    await waitFor(() => expect(screen.getByText('Test Hook')).toBeInTheDocument());

    fireEvent.click(screen.getByRole('button', { name: 'Edit' }));
    await waitFor(() => expect(screen.getByText('← Back to list')).toBeInTheDocument());

    fireEvent.change(screen.getByLabelText('URL'), {
      target: { value: 'https://example.com/new' },
    });
    fireEvent.click(screen.getByText('← Back to list'));

    expect(window.confirm).toHaveBeenCalledWith('Leave without saving?');
    await waitFor(() => expect(screen.queryByText('← Back to list')).not.toBeInTheDocument());
  });

  it('back to list stays on the edit screen when confirmation is cancelled after edits', async () => {
    vi.mocked(window.confirm).mockReturnValue(false);
    mockedApi.getFanoutConfigs.mockResolvedValue([webhookConfig]);
    renderSection();
    await waitFor(() => expect(screen.getByText('Test Hook')).toBeInTheDocument());

    fireEvent.click(screen.getByRole('button', { name: 'Edit' }));
    await waitFor(() => expect(screen.getByText('← Back to list')).toBeInTheDocument());

    fireEvent.change(screen.getByLabelText('URL'), {
      target: { value: 'https://example.com/new' },
    });
    fireEvent.click(screen.getByText('← Back to list'));

    expect(window.confirm).toHaveBeenCalledWith('Leave without saving?');
    expect(screen.getByText('← Back to list')).toBeInTheDocument();
  });

  it('saving a new draft creates the integration on demand', async () => {
    const createdWebhook: FanoutConfig = {
      id: 'wh-new',
      type: 'webhook',
      name: 'Webhook #1',
      enabled: false,
      config: { url: '', method: 'POST', headers: {}, hmac_secret: '', hmac_header: '' },
      scope: { messages: 'all', raw_packets: 'none' },
      sort_order: 0,
      created_at: 2000,
    };
    mockedApi.createFanoutConfig.mockResolvedValue(createdWebhook);
    mockedApi.getFanoutConfigs.mockResolvedValueOnce([]).mockResolvedValueOnce([createdWebhook]);

    renderSection();
    await openCreateIntegrationDialog();
    selectCreateIntegration('Webhook');
    confirmCreateIntegration();
    await waitFor(() => expect(screen.getByText('← Back to list')).toBeInTheDocument());

    fireEvent.click(screen.getByRole('button', { name: 'Save as Disabled' }));

    await waitFor(() =>
      expect(mockedApi.createFanoutConfig).toHaveBeenCalledWith({
        type: 'webhook',
        name: 'Webhook #1',
        config: { url: '', method: 'POST', headers: {}, hmac_secret: '', hmac_header: '' },
        scope: { messages: 'all', raw_packets: 'none' },
        enabled: false,
      })
    );
  });

  it('creates Apprise with outgoing forwarding disabled by default', async () => {
    const createdApprise: FanoutConfig = {
      id: 'ap-new',
      type: 'apprise',
      name: 'Apprise #1',
      enabled: true,
      config: {
        urls: '',
        preserve_identity: true,
        include_outgoing: false,
        markdown_format: true,
        body_format_dm: '**DM:** {sender_name}: {text} **via:** [{hops_backticked}]',
        body_format_channel:
          '**{channel_name}:** {sender_name}: {text} **via:** [{hops_backticked}]',
      },
      scope: { messages: 'all', raw_packets: 'none' },
      sort_order: 0,
      created_at: 2000,
    };
    mockedApi.createFanoutConfig.mockResolvedValue(createdApprise);
    mockedApi.getFanoutConfigs.mockResolvedValueOnce([]).mockResolvedValueOnce([createdApprise]);

    renderSection();
    await openCreateIntegrationDialog();
    selectCreateIntegration('Apprise');
    confirmCreateIntegration();
    await waitFor(() => expect(screen.getByText('← Back to list')).toBeInTheDocument());

    expect(screen.getByLabelText(/Forward RemoteTerm-sent messages/)).not.toBeChecked();
    expect(
      screen.getByText(/Outgoing messages carry no routing path or signal data/)
    ).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: 'Save as Enabled' }));

    await waitFor(() =>
      expect(mockedApi.createFanoutConfig).toHaveBeenCalledWith({
        type: 'apprise',
        name: 'Apprise #1',
        config: {
          urls: '',
          preserve_identity: true,
          include_outgoing: false,
          markdown_format: true,
          body_format_dm: '**DM:** {sender_name}: {text} **via:** [{hops_backticked}]',
          body_format_channel:
            '**{channel_name}:** {sender_name}: {text} **via:** [{hops_backticked}]',
        },
        scope: { messages: 'all', raw_packets: 'none' },
        enabled: true,
      })
    );
  });

  it('can enable outgoing forwarding for an existing Apprise integration', async () => {
    const appriseConfig: FanoutConfig = {
      id: 'ap-1',
      type: 'apprise',
      name: 'Apprise Feed',
      enabled: true,
      config: {
        urls: 'discord://abc',
        preserve_identity: true,
        markdown_format: true,
      },
      scope: { messages: 'all', raw_packets: 'none' },
      sort_order: 0,
      created_at: 1000,
    };
    mockedApi.getFanoutConfigs.mockResolvedValue([appriseConfig]);
    mockedApi.updateFanoutConfig.mockResolvedValue({
      ...appriseConfig,
      config: { ...appriseConfig.config, include_outgoing: true },
    });

    renderSection();
    await waitFor(() => expect(screen.getByText('Apprise Feed')).toBeInTheDocument());

    fireEvent.click(screen.getByRole('button', { name: 'Edit' }));
    await waitFor(() => expect(screen.getByText('← Back to list')).toBeInTheDocument());

    const includeOutgoing = screen.getByLabelText(/Forward RemoteTerm-sent messages/);
    expect(includeOutgoing).not.toBeChecked();
    fireEvent.click(includeOutgoing);
    fireEvent.click(screen.getByRole('button', { name: 'Save as Enabled' }));

    await waitFor(() =>
      expect(mockedApi.updateFanoutConfig).toHaveBeenCalledWith('ap-1', {
        name: 'Apprise Feed',
        config: {
          urls: 'discord://abc',
          preserve_identity: true,
          markdown_format: true,
          include_outgoing: true,
        },
        scope: { messages: 'all', raw_packets: 'none' },
        enabled: true,
      })
    );
  });

  it('new draft names increment within the integration type', async () => {
    mockedApi.getFanoutConfigs.mockResolvedValue([
      webhookConfig,
      {
        ...webhookConfig,
        id: 'wh-2',
        name: 'Another Hook',
      },
    ]);
    renderSection();
    await waitFor(() => expect(screen.getByText('Test Hook')).toBeInTheDocument());

    await openCreateIntegrationDialog();
    selectCreateIntegration('Webhook');
    confirmCreateIntegration();
    await waitFor(() => expect(screen.getByLabelText('Name')).toHaveValue('Webhook #3'));
  });

  it('clicking a list name allows inline rename and saves on blur', async () => {
    const renamedWebhook = { ...webhookConfig, name: 'Renamed Hook' };
    mockedApi.getFanoutConfigs
      .mockResolvedValueOnce([webhookConfig])
      .mockResolvedValueOnce([renamedWebhook]);
    mockedApi.updateFanoutConfig.mockResolvedValue(renamedWebhook);

    renderSection();
    await waitFor(() => expect(screen.getByText('Test Hook')).toBeInTheDocument());

    fireEvent.click(screen.getByRole('button', { name: 'Test Hook' }));
    const inlineInput = screen.getByLabelText('Edit name for Test Hook');
    fireEvent.change(inlineInput, { target: { value: 'Renamed Hook' } });
    fireEvent.blur(inlineInput);

    await waitFor(() =>
      expect(mockedApi.updateFanoutConfig).toHaveBeenCalledWith('wh-1', { name: 'Renamed Hook' })
    );
    await waitFor(() => expect(screen.getByText('Renamed Hook')).toBeInTheDocument());
  });

  it('escape cancels inline rename without saving', async () => {
    mockedApi.getFanoutConfigs.mockResolvedValue([webhookConfig]);
    renderSection();
    await waitFor(() => expect(screen.getByText('Test Hook')).toBeInTheDocument());

    fireEvent.click(screen.getByRole('button', { name: 'Test Hook' }));
    const inlineInput = screen.getByLabelText('Edit name for Test Hook');
    fireEvent.change(inlineInput, { target: { value: 'Cancelled Hook' } });
    fireEvent.keyDown(inlineInput, { key: 'Escape' });

    await waitFor(() => expect(screen.getByText('Test Hook')).toBeInTheDocument());
    expect(mockedApi.updateFanoutConfig).not.toHaveBeenCalledWith('wh-1', {
      name: 'Cancelled Hook',
    });
  });

  it('community MQTT editor exposes packet topic template', async () => {
    const communityConfig: FanoutConfig = {
      id: 'comm-1',
      type: 'mqtt_community',
      name: 'Community Feed',
      enabled: false,
      config: {
        broker_host: 'mqtt-us-v1.letsmesh.net',
        broker_port: 443,
        transport: 'tcp',
        use_tls: true,
        tls_verify: true,
        auth_mode: 'token',
        iata: 'LAX',
        email: '',
        token_audience: 'meshrank.net',
        topic_template: 'mesh2mqtt/{IATA}/node/{PUBLIC_KEY}',
      },
      scope: { messages: 'none', raw_packets: 'all' },
      sort_order: 0,
      created_at: 1000,
    };
    mockedApi.getFanoutConfigs.mockResolvedValue([communityConfig]);
    renderSection();
    await waitFor(() => expect(screen.getByText('Community Feed')).toBeInTheDocument());

    fireEvent.click(screen.getByRole('button', { name: 'Edit' }));
    await waitFor(() => expect(screen.getByText('← Back to list')).toBeInTheDocument());

    expect(screen.getByLabelText('Packet Topic Template')).toHaveValue(
      'mesh2mqtt/{IATA}/node/{PUBLIC_KEY}'
    );
    expect(screen.getByLabelText('Transport')).toHaveValue('tcp');
    expect(screen.getByLabelText('Authentication')).toHaveValue('token');
    expect(screen.getByLabelText('Token Audience')).toHaveValue('meshrank.net');
    expect(screen.getByText(/LetsMesh uses/)).toBeInTheDocument();
  });

  it('existing community MQTT config without auth_mode defaults to token in the editor', async () => {
    const communityConfig: FanoutConfig = {
      id: 'comm-legacy',
      type: 'mqtt_community',
      name: 'Legacy Community MQTT',
      enabled: false,
      config: {
        broker_host: 'mqtt-us-v1.letsmesh.net',
        broker_port: 443,
        transport: 'websockets',
        use_tls: true,
        tls_verify: true,
        iata: 'LAX',
        email: 'user@example.com',
        token_audience: '',
        topic_template: 'meshcore/{IATA}/{PUBLIC_KEY}/packets',
      },
      scope: { messages: 'none', raw_packets: 'all' },
      sort_order: 0,
      created_at: 1000,
    };
    mockedApi.getFanoutConfigs.mockResolvedValue([communityConfig]);
    renderSection();
    await waitFor(() => expect(screen.getByText('Legacy Community MQTT')).toBeInTheDocument());

    fireEvent.click(screen.getByRole('button', { name: 'Edit' }));
    await waitFor(() => expect(screen.getByText('← Back to list')).toBeInTheDocument());

    expect(screen.getByLabelText('Authentication')).toHaveValue('token');
    expect(screen.getByLabelText('Token Audience')).toBeInTheDocument();
  });

  it('community MQTT token audience can be cleared back to blank', async () => {
    const communityConfig: FanoutConfig = {
      id: 'comm-1',
      type: 'mqtt_community',
      name: 'Community Feed',
      enabled: false,
      config: {
        broker_host: 'mqtt-us-v1.letsmesh.net',
        broker_port: 443,
        transport: 'websockets',
        use_tls: true,
        tls_verify: true,
        auth_mode: 'token',
        iata: 'LAX',
        email: '',
        token_audience: 'meshrank.net',
        topic_template: 'meshcore/{IATA}/{PUBLIC_KEY}/packets',
      },
      scope: { messages: 'none', raw_packets: 'all' },
      sort_order: 0,
      created_at: 1000,
    };
    mockedApi.getFanoutConfigs.mockResolvedValue([communityConfig]);
    renderSection();
    await waitFor(() => expect(screen.getByText('Community Feed')).toBeInTheDocument());

    fireEvent.click(screen.getByRole('button', { name: 'Edit' }));
    await waitFor(() => expect(screen.getByText('← Back to list')).toBeInTheDocument());

    const audienceInput = screen.getByLabelText('Token Audience');
    fireEvent.change(audienceInput, { target: { value: '' } });

    expect(audienceInput).toHaveValue('');
  });

  it('existing community MQTT defaults can be cleared while editing and normalize on save', async () => {
    const communityConfig: FanoutConfig = {
      id: 'comm-1',
      type: 'mqtt_community',
      name: 'Community Feed',
      enabled: false,
      config: {
        broker_host: 'mqtt-us-v1.letsmesh.net',
        broker_port: 443,
        transport: 'websockets',
        use_tls: true,
        tls_verify: true,
        auth_mode: 'token',
        iata: 'LAX',
        email: '',
        token_audience: '',
        topic_template: 'meshcore/{IATA}/{PUBLIC_KEY}/packets',
      },
      scope: { messages: 'none', raw_packets: 'all' },
      sort_order: 0,
      created_at: 1000,
    };
    mockedApi.getFanoutConfigs.mockResolvedValue([communityConfig]);
    mockedApi.updateFanoutConfig.mockResolvedValue({
      ...communityConfig,
      enabled: true,
    });

    renderSection();
    await waitFor(() => expect(screen.getByText('Community Feed')).toBeInTheDocument());

    fireEvent.click(screen.getByRole('button', { name: 'Edit' }));
    await waitFor(() => expect(screen.getByText('← Back to list')).toBeInTheDocument());

    const hostInput = screen.getByLabelText('Broker Host') as HTMLInputElement;
    const portInput = screen.getByLabelText('Broker Port') as HTMLInputElement;
    const topicTemplateInput = screen.getByLabelText('Packet Topic Template') as HTMLInputElement;

    fireEvent.change(hostInput, { target: { value: '' } });
    fireEvent.change(portInput, { target: { value: '' } });
    fireEvent.change(topicTemplateInput, { target: { value: '' } });

    expect(hostInput.value).toBe('');
    expect(portInput.value).toBe('');
    expect(topicTemplateInput.value).toBe('');

    fireEvent.click(screen.getByRole('button', { name: 'Save as Enabled' }));

    await waitFor(() =>
      expect(mockedApi.updateFanoutConfig).toHaveBeenCalledWith('comm-1', {
        name: 'Community Feed',
        config: {
          broker_host: 'mqtt-us-v1.letsmesh.net',
          broker_port: 443,
          transport: 'websockets',
          use_tls: true,
          tls_verify: true,
          auth_mode: 'token',
          iata: 'LAX',
          email: '',
          token_audience: '',
          topic_template: 'meshcore/{IATA}/{PUBLIC_KEY}/packets',
        },
        scope: { messages: 'none', raw_packets: 'all' },
        enabled: true,
      })
    );
  });

  it('community MQTT can be configured for no auth', async () => {
    const communityConfig: FanoutConfig = {
      id: 'comm-1',
      type: 'mqtt_community',
      name: 'Community Feed',
      enabled: false,
      config: {
        broker_host: 'meshrank.net',
        broker_port: 8883,
        transport: 'tcp',
        use_tls: true,
        tls_verify: true,
        auth_mode: 'none',
        iata: 'LAX',
        topic_template: 'meshrank/uplink/ROOM/{PUBLIC_KEY}/packets',
      },
      scope: { messages: 'none', raw_packets: 'all' },
      sort_order: 0,
      created_at: 1000,
    };
    mockedApi.getFanoutConfigs.mockResolvedValue([communityConfig]);
    renderSection();
    await waitFor(() => expect(screen.getByText('Community Feed')).toBeInTheDocument());

    fireEvent.click(screen.getByRole('button', { name: 'Edit' }));
    await waitFor(() => expect(screen.getByText('← Back to list')).toBeInTheDocument());

    expect(screen.getByLabelText('Authentication')).toHaveValue('none');
    expect(screen.queryByLabelText('Token Audience')).not.toBeInTheDocument();
  });

  it('community MQTT list shows configured packet topic', async () => {
    const communityConfig: FanoutConfig = {
      id: 'comm-1',
      type: 'mqtt_community',
      name: 'Community Feed',
      enabled: false,
      config: {
        broker_host: 'mqtt-us-v1.letsmesh.net',
        broker_port: 443,
        transport: 'websockets',
        use_tls: true,
        tls_verify: true,
        auth_mode: 'token',
        iata: 'LAX',
        email: '',
        token_audience: 'mqtt-us-v1.letsmesh.net',
        topic_template: 'mesh2mqtt/{IATA}/node/{PUBLIC_KEY}',
      },
      scope: { messages: 'none', raw_packets: 'all' },
      sort_order: 0,
      created_at: 1000,
    };
    mockedApi.getFanoutConfigs.mockResolvedValue([communityConfig]);
    renderSection();

    const group = await screen.findByRole('group', { name: 'Integration Community Feed' });
    expect(
      within(group).getByText(
        (_, element) => element?.textContent === 'Broker: mqtt-us-v1.letsmesh.net:443'
      )
    ).toBeInTheDocument();
    expect(within(group).getByText('mesh2mqtt/{IATA}/node/{PUBLIC_KEY}')).toBeInTheDocument();
    expect(screen.queryByText('Region: LAX')).not.toBeInTheDocument();
  });

  it('MeshRank preset pre-fills the broker settings and asks for the topic template', async () => {
    renderSection();
    await openCreateIntegrationDialog();
    selectCreateIntegration('MeshRank');
    confirmCreateIntegration();

    await waitFor(() => expect(screen.getByText('← Back to list')).toBeInTheDocument());

    expect(screen.getByLabelText('Name')).toHaveValue('MeshRank');
    expect(screen.getByLabelText('Packet Topic Template')).toHaveValue('');
    expect(screen.queryByLabelText('Broker Host')).not.toBeInTheDocument();
  });

  it('private MQTT fields can be cleared while editing and normalize defaults on create', async () => {
    const createdConfig: FanoutConfig = {
      id: 'mqtt-private-1',
      type: 'mqtt_private',
      name: 'Private MQTT 1',
      enabled: true,
      config: {
        broker_host: 'broker.local',
        broker_port: 1883,
        username: '',
        password: '',
        use_tls: false,
        tls_insecure: false,
        topic_prefix: 'meshcore',
      },
      scope: { messages: 'all', raw_packets: 'all' },
      sort_order: 0,
      created_at: 2000,
    };
    mockedApi.createFanoutConfig.mockResolvedValue(createdConfig);
    mockedApi.getFanoutConfigs.mockResolvedValueOnce([]).mockResolvedValueOnce([createdConfig]);

    renderSection();
    await openCreateIntegrationDialog();
    selectCreateIntegration('Private MQTT');
    confirmCreateIntegration();
    await waitFor(() => expect(screen.getByText('← Back to list')).toBeInTheDocument());

    fireEvent.change(screen.getByLabelText('Broker Host'), { target: { value: 'broker.local' } });

    const portInput = screen.getByLabelText('Broker Port') as HTMLInputElement;
    const prefixInput = screen.getByLabelText('Topic Prefix') as HTMLInputElement;
    fireEvent.change(portInput, { target: { value: '' } });
    fireEvent.change(prefixInput, { target: { value: '' } });

    expect(portInput.value).toBe('');
    expect(prefixInput.value).toBe('');

    fireEvent.click(screen.getByRole('button', { name: 'Save as Enabled' }));

    await waitFor(() =>
      expect(mockedApi.createFanoutConfig).toHaveBeenCalledWith({
        type: 'mqtt_private',
        name: 'Private MQTT #1',
        config: {
          broker_host: 'broker.local',
          broker_port: 1883,
          username: '',
          password: '',
          use_tls: false,
          tls_insecure: false,
          topic_prefix: 'meshcore',
        },
        scope: { messages: 'all', raw_packets: 'all' },
        enabled: true,
      })
    );
  });

  it('creates MeshRank preset as a regular mqtt_community config', async () => {
    const createdConfig: FanoutConfig = {
      id: 'comm-meshrank',
      type: 'mqtt_community',
      name: 'MeshRank',
      enabled: true,
      config: {
        broker_host: 'meshrank.net',
        broker_port: 8883,
        transport: 'tcp',
        use_tls: true,
        tls_verify: true,
        auth_mode: 'none',
        username: '',
        password: '',
        iata: 'XYZ',
        email: '',
        token_audience: '',
        topic_template: 'meshrank/uplink/B435F6D5F7896B74C6B995FE221C2C1F/{PUBLIC_KEY}/packets',
      },
      scope: { messages: 'none', raw_packets: 'all' },
      sort_order: 0,
      created_at: 2000,
    };
    mockedApi.createFanoutConfig.mockResolvedValue(createdConfig);
    mockedApi.getFanoutConfigs.mockResolvedValueOnce([]).mockResolvedValueOnce([createdConfig]);

    renderSection();
    await openCreateIntegrationDialog();
    selectCreateIntegration('MeshRank');
    confirmCreateIntegration();
    await waitFor(() => expect(screen.getByText('← Back to list')).toBeInTheDocument());

    fireEvent.change(screen.getByLabelText('Packet Topic Template'), {
      target: {
        value: 'meshrank/uplink/B435F6D5F7896B74C6B995FE221C2C1F/{PUBLIC_KEY}/packets',
      },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Save as Enabled' }));

    await waitFor(() =>
      expect(mockedApi.createFanoutConfig).toHaveBeenCalledWith({
        type: 'mqtt_community',
        name: 'MeshRank',
        config: {
          broker_host: 'meshrank.net',
          broker_port: 8883,
          transport: 'tcp',
          use_tls: true,
          tls_verify: true,
          auth_mode: 'none',
          username: '',
          password: '',
          iata: 'XYZ',
          email: '',
          token_audience: '',
          topic_template: 'meshrank/uplink/B435F6D5F7896B74C6B995FE221C2C1F/{PUBLIC_KEY}/packets',
        },
        scope: { messages: 'none', raw_packets: 'all' },
        enabled: true,
      })
    );
  });

  it('shows Home Assistant topic summary with device-key-derived node ids', async () => {
    mockedApi.getContacts.mockResolvedValue([
      {
        public_key: 'bb'.repeat(32),
        name: 'Alice',
        type: 1,
        flags: 0,
        direct_path: null,
        direct_path_len: -1,
        direct_path_hash_mode: -1,
        direct_path_updated_at: null,
        route_override_path: null,
        route_override_len: null,
        route_override_hash_mode: null,
        last_advert: null,
        lat: null,
        lon: null,
        last_seen: null,
        on_radio: false,
        last_contacted: null,
        first_seen: null,
        last_read_at: null,
        favorite: false,
      },
      {
        public_key: 'cc'.repeat(32),
        name: 'Repeater One',
        type: 2,
        flags: 0,
        direct_path: null,
        direct_path_len: -1,
        direct_path_hash_mode: -1,
        direct_path_updated_at: null,
        route_override_path: null,
        route_override_len: null,
        route_override_hash_mode: null,
        last_advert: null,
        lat: null,
        lon: null,
        last_seen: null,
        on_radio: false,
        last_contacted: null,
        first_seen: null,
        last_read_at: null,
        favorite: false,
      },
    ]);
    mockedApi.getSettings.mockResolvedValue({
      max_radio_contacts: 200,
      auto_decrypt_dm_on_advert: true,
      last_message_times: {},
      advert_interval: 0,
      last_advert_time: 0,
      flood_scope: '',
      known_regions: [],
      blocked_keys: [],
      blocked_names: [],
      discovery_blocked_types: [],
      tracked_telemetry_repeaters: ['cc'.repeat(32)],
      tracked_telemetry_contacts: [],
      auto_resend_channel: false,
      telemetry_interval_hours: 8,
      telemetry_routed_hourly: false,
    });

    renderSection();
    await openCreateIntegrationDialog();
    selectCreateIntegration('Home Assistant MQTT Discovery');
    confirmCreateIntegration();

    expect(await screen.findByText('Published topic summary')).toBeInTheDocument();

    fireEvent.click(await screen.findByLabelText(/Alice/));
    fireEvent.click(await screen.findByLabelText(/Repeater One/));

    await waitFor(() => {
      expect(screen.getAllByText('node id aaaaaaaaaaaa').length).toBeGreaterThanOrEqual(2);
      expect(screen.getByText('node id bbbbbbbbbbbb')).toBeInTheDocument();
      expect(screen.getByText('node id cccccccccccc')).toBeInTheDocument();
    });

    expect(screen.getByText('meshcore/aaaaaaaaaaaa/health')).toBeInTheDocument();
    expect(screen.getByText('meshcore/aaaaaaaaaaaa/events/message')).toBeInTheDocument();
    expect(screen.getByText('meshcore/bbbbbbbbbbbb/gps')).toBeInTheDocument();
    expect(screen.getByText('meshcore/cccccccccccc/telemetry')).toBeInTheDocument();
  });

  it('LetsMesh (US) preset pre-fills the expected broker defaults', async () => {
    const createdConfig: FanoutConfig = {
      id: 'comm-letsmesh-us',
      type: 'mqtt_community',
      name: 'LetsMesh (US)',
      enabled: false,
      config: {
        broker_host: 'mqtt-us-v1.letsmesh.net',
        broker_port: 443,
        transport: 'websockets',
        use_tls: true,
        tls_verify: true,
        auth_mode: 'token',
        username: '',
        password: '',
        iata: 'LAX',
        email: 'user@example.com',
        token_audience: 'mqtt-us-v1.letsmesh.net',
        topic_template: 'meshcore/{IATA}/{PUBLIC_KEY}/packets',
      },
      scope: { messages: 'none', raw_packets: 'all' },
      sort_order: 0,
      created_at: 2000,
    };
    mockedApi.createFanoutConfig.mockResolvedValue(createdConfig);
    mockedApi.getFanoutConfigs.mockResolvedValueOnce([]).mockResolvedValueOnce([createdConfig]);

    renderSection();
    await openCreateIntegrationDialog();
    selectCreateIntegration('LetsMesh (US)');
    confirmCreateIntegration();
    await waitFor(() => expect(screen.getByText('← Back to list')).toBeInTheDocument());

    expect(screen.getByLabelText('Name')).toHaveValue('LetsMesh (US)');
    expect(screen.queryByLabelText('Authentication')).not.toBeInTheDocument();
    expect(screen.queryByLabelText('Packet Topic Template')).not.toBeInTheDocument();

    fireEvent.change(screen.getByLabelText('Email'), { target: { value: 'user@example.com' } });
    fireEvent.change(screen.getByLabelText('Region Code (IATA)'), { target: { value: 'lax' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save as Disabled' }));

    await waitFor(() =>
      expect(mockedApi.createFanoutConfig).toHaveBeenCalledWith({
        type: 'mqtt_community',
        name: 'LetsMesh (US)',
        config: {
          broker_host: 'mqtt-us-v1.letsmesh.net',
          broker_port: 443,
          transport: 'websockets',
          use_tls: true,
          tls_verify: true,
          auth_mode: 'token',
          username: '',
          password: '',
          iata: 'LAX',
          email: 'user@example.com',
          token_audience: 'mqtt-us-v1.letsmesh.net',
          topic_template: 'meshcore/{IATA}/{PUBLIC_KEY}/packets',
        },
        scope: { messages: 'none', raw_packets: 'all' },
        enabled: false,
      })
    );
  });

  it('map upload geofence radius can be cleared while editing and normalizes to zero', async () => {
    const createdConfig: FanoutConfig = {
      id: 'map-1',
      type: 'map_upload',
      name: 'Map Upload 1',
      enabled: true,
      config: {
        api_url: '',
        dry_run: true,
        geofence_enabled: true,
        geofence_radius_km: 0,
      },
      scope: { messages: 'none', raw_packets: 'all' },
      sort_order: 0,
      created_at: 2000,
    };
    mockedApi.createFanoutConfig.mockResolvedValue(createdConfig);
    mockedApi.getFanoutConfigs.mockResolvedValueOnce([]).mockResolvedValueOnce([createdConfig]);

    renderSection();
    await openCreateIntegrationDialog();
    selectCreateIntegration('Map Upload');
    confirmCreateIntegration();
    await waitFor(() => expect(screen.getByText('← Back to list')).toBeInTheDocument());

    fireEvent.click(screen.getByText('Enable Geofence'));
    const radiusInput = screen.getByLabelText('Radius (km)') as HTMLInputElement;

    fireEvent.change(radiusInput, { target: { value: '100' } });
    fireEvent.change(radiusInput, { target: { value: '' } });

    expect(radiusInput.value).toBe('');

    fireEvent.click(screen.getByRole('button', { name: 'Save as Enabled' }));

    await waitFor(() =>
      expect(mockedApi.createFanoutConfig).toHaveBeenCalledWith({
        type: 'map_upload',
        name: 'Map Upload #1',
        config: {
          api_url: '',
          dry_run: true,
          geofence_enabled: true,
          geofence_radius_km: 0,
        },
        scope: { messages: 'none', raw_packets: 'all' },
        enabled: true,
      })
    );
  });

  it('LetsMesh (EU) preset saves the EU broker defaults', async () => {
    const createdConfig: FanoutConfig = {
      id: 'comm-letsmesh-eu',
      type: 'mqtt_community',
      name: 'LetsMesh (EU)',
      enabled: true,
      config: {
        broker_host: 'mqtt-eu-v1.letsmesh.net',
        broker_port: 443,
        transport: 'websockets',
        use_tls: true,
        tls_verify: true,
        auth_mode: 'token',
        username: '',
        password: '',
        iata: 'AMS',
        email: 'user@example.com',
        token_audience: 'mqtt-eu-v1.letsmesh.net',
        topic_template: 'meshcore/{IATA}/{PUBLIC_KEY}/packets',
      },
      scope: { messages: 'none', raw_packets: 'all' },
      sort_order: 0,
      created_at: 2000,
    };
    mockedApi.createFanoutConfig.mockResolvedValue(createdConfig);
    mockedApi.getFanoutConfigs.mockResolvedValueOnce([]).mockResolvedValueOnce([createdConfig]);

    renderSection();
    await openCreateIntegrationDialog();
    selectCreateIntegration('LetsMesh (EU)');
    confirmCreateIntegration();
    await waitFor(() => expect(screen.getByText('← Back to list')).toBeInTheDocument());

    fireEvent.change(screen.getByLabelText('Email'), { target: { value: 'user@example.com' } });
    fireEvent.change(screen.getByLabelText('Region Code (IATA)'), { target: { value: 'ams' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save as Enabled' }));

    await waitFor(() =>
      expect(mockedApi.createFanoutConfig).toHaveBeenCalledWith({
        type: 'mqtt_community',
        name: 'LetsMesh (EU)',
        config: {
          broker_host: 'mqtt-eu-v1.letsmesh.net',
          broker_port: 443,
          transport: 'websockets',
          use_tls: true,
          tls_verify: true,
          auth_mode: 'token',
          username: '',
          password: '',
          iata: 'AMS',
          email: 'user@example.com',
          token_audience: 'mqtt-eu-v1.letsmesh.net',
          topic_template: 'meshcore/{IATA}/{PUBLIC_KEY}/packets',
        },
        scope: { messages: 'none', raw_packets: 'all' },
        enabled: true,
      })
    );
  });

  it('generic Community MQTT entry still opens the full editor', async () => {
    renderSection();
    await openCreateIntegrationDialog();
    selectCreateIntegration('Community MQTT/meshcoretomqtt');
    confirmCreateIntegration();

    await waitFor(() => expect(screen.getByText('← Back to list')).toBeInTheDocument());

    expect(screen.getByLabelText('Name')).toHaveValue('Community Sharing #1');
    expect(screen.getByLabelText('Broker Host')).toBeInTheDocument();
    expect(screen.getByLabelText('Authentication')).toBeInTheDocument();
    expect(screen.getByLabelText('Packet Topic Template')).toBeInTheDocument();
  });

  it('private MQTT list shows broker and topic summary', async () => {
    const privateConfig: FanoutConfig = {
      id: 'mqtt-1',
      type: 'mqtt_private',
      name: 'Private Broker',
      enabled: true,
      config: { broker_host: 'broker.local', broker_port: 1883, topic_prefix: 'meshcore' },
      scope: { messages: 'all', raw_packets: 'all' },
      sort_order: 0,
      created_at: 1000,
    };
    mockedApi.getFanoutConfigs.mockResolvedValue([privateConfig]);
    renderSection();

    const group = await screen.findByRole('group', { name: 'Integration Private Broker' });
    expect(
      within(group).getByText((_, element) => element?.textContent === 'Broker: broker.local:1883')
    ).toBeInTheDocument();
    expect(
      within(group).getByText('meshcore/dm:<pubkey>, meshcore/gm:<channel>, meshcore/raw/...')
    ).toBeInTheDocument();
  });

  it('webhook list shows destination URL', async () => {
    const config: FanoutConfig = {
      id: 'wh-1',
      type: 'webhook',
      name: 'Webhook Feed',
      enabled: true,
      config: { url: 'https://example.com/hook', method: 'POST', headers: {} },
      scope: { messages: 'all', raw_packets: 'none' },
      sort_order: 0,
      created_at: 1000,
    };
    mockedApi.getFanoutConfigs.mockResolvedValue([config]);
    renderSection();

    const group = await screen.findByRole('group', { name: 'Integration Webhook Feed' });
    expect(within(group).getByText('https://example.com/hook')).toBeInTheDocument();
  });

  it('apprise list shows compact target summary', async () => {
    const config: FanoutConfig = {
      id: 'ap-1',
      type: 'apprise',
      name: 'Apprise Feed',
      enabled: true,
      config: {
        urls: 'discord://abc\nmailto://one@example.com\nmailto://two@example.com',
        preserve_identity: true,
        include_path: true,
      },
      scope: { messages: 'all', raw_packets: 'none' },
      sort_order: 0,
      created_at: 1000,
    };
    mockedApi.getFanoutConfigs.mockResolvedValue([config]);
    renderSection();

    const group = await screen.findByRole('group', { name: 'Integration Apprise Feed' });
    expect(
      within(group).getByText(/discord:\/\/\*{8}, mailto:\/\/\*{8}, mailto:\/\/\*{8}/)
    ).toBeInTheDocument();
  });

  it('sqs list shows queue url summary', async () => {
    const config: FanoutConfig = {
      id: 'sqs-1',
      type: 'sqs',
      name: 'Queue Feed',
      enabled: true,
      config: {
        queue_url: 'https://sqs.us-east-1.amazonaws.com/123456789012/mesh-events',
        region_name: 'us-east-1',
      },
      scope: { messages: 'all', raw_packets: 'none' },
      sort_order: 0,
      created_at: 1000,
    };
    mockedApi.getFanoutConfigs.mockResolvedValue([config]);
    renderSection();

    const group = await screen.findByRole('group', { name: 'Integration Queue Feed' });
    expect(
      within(group).getByText('https://sqs.us-east-1.amazonaws.com/123456789012/mesh-events')
    ).toBeInTheDocument();
  });

  it('groups integrations by type and sorts entries alphabetically within each group', async () => {
    mockedApi.getFanoutConfigs.mockResolvedValue([
      {
        ...webhookConfig,
        id: 'wh-b',
        name: 'Zulu Hook',
      },
      {
        ...webhookConfig,
        id: 'wh-a',
        name: 'Alpha Hook',
      },
      {
        id: 'ap-1',
        type: 'apprise',
        name: 'Bravo Alerts',
        enabled: true,
        config: { urls: 'discord://abc', preserve_identity: true, include_path: true },
        scope: { messages: 'all', raw_packets: 'none' },
        sort_order: 0,
        created_at: 1000,
      },
    ]);
    renderSection();

    const webhookGroup = await screen.findByRole('region', { name: 'Webhook integrations' });
    const appriseGroup = screen.getByRole('region', { name: 'Apprise integrations' });

    expect(
      screen.queryByRole('region', { name: 'Private MQTT integrations' })
    ).not.toBeInTheDocument();
    expect(within(webhookGroup).getByText('Alpha Hook')).toBeInTheDocument();
    expect(within(webhookGroup).getByText('Zulu Hook')).toBeInTheDocument();
    expect(within(appriseGroup).getByText('Bravo Alerts')).toBeInTheDocument();

    const alpha = within(webhookGroup).getByText('Alpha Hook');
    const zulu = within(webhookGroup).getByText('Zulu Hook');
    expect(alpha.compareDocumentPosition(zulu) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });
});
