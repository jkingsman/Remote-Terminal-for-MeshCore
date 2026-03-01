import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { SettingsModal } from '../components/SettingsModal';
import type {
  AppSettings,
  AppSettingsUpdate,
  HealthStatus,
  RadioConfig,
  RadioConfigUpdate,
  StatisticsResponse,
} from '../types';
import type { SettingsSection } from '../components/settings/settingsConstants';
import {
  LAST_VIEWED_CONVERSATION_KEY,
  REOPEN_LAST_CONVERSATION_KEY,
} from '../utils/lastViewedConversation';
import { api } from '../api';

const baseConfig: RadioConfig = {
  public_key: 'aa'.repeat(32),
  name: 'TestNode',
  lat: 1,
  lon: 2,
  tx_power: 17,
  max_tx_power: 22,
  radio: {
    freq: 910.525,
    bw: 62.5,
    sf: 7,
    cr: 5,
  },
};

const baseHealth: HealthStatus = {
  status: 'connected',
  radio_connected: true,
  connection_info: 'Serial: /dev/ttyUSB0',
  database_size_mb: 1.2,
  oldest_undecrypted_timestamp: null,
  mqtt_status: null,
  community_mqtt_status: null,
  bots_disabled: false,
};

const baseSettings: AppSettings = {
  max_radio_contacts: 200,
  favorites: [],
  auto_decrypt_dm_on_advert: false,
  sidebar_sort_order: 'recent',
  last_message_times: {},
  preferences_migrated: false,
  advert_interval: 0,
  last_advert_time: 0,
  bots: [],
  mqtt_broker_host: '',
  mqtt_broker_port: 1883,
  mqtt_username: '',
  mqtt_password: '',
  mqtt_use_tls: false,
  mqtt_tls_insecure: false,
  mqtt_topic_prefix: 'meshcore',
  mqtt_publish_messages: false,
  mqtt_publish_raw_packets: false,
  apprise_enabled: false,
  apprise_url: '',
  apprise_mode: 'all',
  apprise_preserve_identity: true,
  apprise_targets: [],
  community_mqtt_enabled: false,
  community_mqtt_iata: '',
  community_mqtt_broker_host: 'mqtt-us-v1.letsmesh.net',
  community_mqtt_broker_port: 443,
  community_mqtt_email: '',
  flood_scope: '',
  blocked_keys: [],
  blocked_names: [],
};

function renderModal(overrides?: {
  appSettings?: AppSettings;
  health?: HealthStatus;
  onSaveAppSettings?: (update: AppSettingsUpdate) => Promise<void>;
  onRefreshAppSettings?: () => Promise<void>;
  onSave?: (update: RadioConfigUpdate) => Promise<void>;
  onClose?: () => void;
  onSetPrivateKey?: (key: string) => Promise<void>;
  onReboot?: () => Promise<void>;
  open?: boolean;
  pageMode?: boolean;
  externalSidebarNav?: boolean;
  desktopSection?: SettingsSection;
  mobile?: boolean;
}) {
  setMatchMedia(overrides?.mobile ?? false);

  const onSaveAppSettings = overrides?.onSaveAppSettings ?? vi.fn(async () => {});
  const onRefreshAppSettings = overrides?.onRefreshAppSettings ?? vi.fn(async () => {});
  const onSave = overrides?.onSave ?? vi.fn(async (_update: RadioConfigUpdate) => {});
  const onClose = overrides?.onClose ?? vi.fn();
  const onSetPrivateKey = overrides?.onSetPrivateKey ?? vi.fn(async () => {});
  const onReboot = overrides?.onReboot ?? vi.fn(async () => {});

  const commonProps = {
    open: overrides?.open ?? true,
    pageMode: overrides?.pageMode,
    config: baseConfig,
    health: overrides?.health ?? baseHealth,
    appSettings: overrides?.appSettings ?? baseSettings,
    onClose,
    onSave,
    onSaveAppSettings,
    onSetPrivateKey,
    onReboot,
    onAdvertise: vi.fn(async () => {}),
    onHealthRefresh: vi.fn(async () => {}),
    onRefreshAppSettings,
  };

  const view = overrides?.externalSidebarNav
    ? render(
        <SettingsModal
          {...commonProps}
          externalSidebarNav
          desktopSection={overrides.desktopSection ?? 'radio'}
        />
      )
    : render(<SettingsModal {...commonProps} />);

  return {
    onSaveAppSettings,
    onRefreshAppSettings,
    onSave,
    onClose,
    onSetPrivateKey,
    onReboot,
    view,
  };
}

function setMatchMedia(matches: boolean) {
  Object.defineProperty(window, 'matchMedia', {
    writable: true,
    value: vi.fn().mockImplementation(() => ({
      matches,
      media: '(max-width: 767px)',
      onchange: null,
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
      addListener: vi.fn(),
      removeListener: vi.fn(),
      dispatchEvent: vi.fn(),
    })),
  });
}

function openRadioSection() {
  const radioToggle = screen.getByRole('button', { name: /Radio/i });
  fireEvent.click(radioToggle);
}

function openLocalSection() {
  const localToggle = screen.getByRole('button', { name: /Local Configuration/i });
  fireEvent.click(localToggle);
}

function openMqttSection() {
  const mqttToggle = screen.getByRole('button', { name: /MQTT/i });
  fireEvent.click(mqttToggle);
}

function expandPrivateMqtt() {
  fireEvent.click(screen.getByText('Private MQTT Broker'));
}

function expandCommunityMqtt() {
  fireEvent.click(screen.getByText('Community Analytics'));
}

function openDatabaseSection() {
  const databaseToggle = screen.getByRole('button', { name: /Database/i });
  fireEvent.click(databaseToggle);
}

describe('SettingsModal', () => {
  afterEach(() => {
    vi.restoreAllMocks();
    localStorage.clear();
    window.location.hash = '';
  });

  it('refreshes app settings when opened', async () => {
    const { onRefreshAppSettings } = renderModal();

    await waitFor(() => {
      expect(onRefreshAppSettings).toHaveBeenCalledTimes(1);
    });
  });

  it('refreshes app settings in page mode even when open is false', async () => {
    const { onRefreshAppSettings } = renderModal({ open: false, pageMode: true });

    await waitFor(() => {
      expect(onRefreshAppSettings).toHaveBeenCalledTimes(1);
    });
  });

  it('does not render when closed outside page mode', () => {
    renderModal({ open: false });
    expect(screen.queryByLabelText('Preset')).not.toBeInTheDocument();
  });

  it('shows favorite-first contact sync helper text in radio tab', async () => {
    renderModal();
    openRadioSection();

    expect(
      screen.getByText(
        /Favorite contacts load first, then recent non-repeater contacts until this\s+limit is reached/i
      )
    ).toBeInTheDocument();
  });

  it('saves changed max contacts value through onSaveAppSettings', async () => {
    const { onSaveAppSettings } = renderModal();
    openRadioSection();

    const maxContactsInput = screen.getByLabelText('Max Contacts on Radio');
    fireEvent.change(maxContactsInput, { target: { value: '250' } });

    // Click the "Save Settings" button in the Flood & Advert Control section
    const saveButtons = screen.getAllByRole('button', { name: 'Save Settings' });
    fireEvent.click(saveButtons[0]);

    await waitFor(() => {
      expect(onSaveAppSettings).toHaveBeenCalledWith({ max_radio_contacts: 250 });
    });
  });

  it('does not save max contacts when unchanged', async () => {
    const { onSaveAppSettings } = renderModal({
      appSettings: { ...baseSettings, max_radio_contacts: 200 },
    });
    openRadioSection();

    // Click the "Save Settings" button in the Flood & Advert Control section
    const saveButtons = screen.getAllByRole('button', { name: 'Save Settings' });
    fireEvent.click(saveButtons[0]);

    await waitFor(() => {
      expect(onSaveAppSettings).not.toHaveBeenCalled();
    });
  });

  it('renders selected section from external sidebar nav on desktop mode', async () => {
    renderModal({
      externalSidebarNav: true,
      desktopSection: 'bot',
    });

    expect(screen.getByText('No bots configured')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /Local Configuration/i })).not.toBeInTheDocument();
    expect(screen.queryByLabelText('Preset')).not.toBeInTheDocument();
  });

  it('toggles sections in mobile accordion mode', () => {
    renderModal({ mobile: true });
    const localToggle = screen.getAllByRole('button', { name: /Local Configuration/i })[0];

    expect(screen.queryByLabelText('Preset')).not.toBeInTheDocument();
    expect(screen.queryByLabelText('Local label text')).not.toBeInTheDocument();

    fireEvent.click(localToggle);
    expect(screen.getByLabelText('Local label text')).toBeInTheDocument();

    fireEvent.click(localToggle);
    expect(screen.queryByLabelText('Local label text')).not.toBeInTheDocument();
  });

  it('clears stale errors when switching external desktop sections', async () => {
    const onSaveAppSettings = vi.fn(async () => {
      throw new Error('Save failed');
    });

    const { view } = renderModal({
      externalSidebarNav: true,
      desktopSection: 'database',
      onSaveAppSettings,
    });

    fireEvent.click(screen.getByRole('button', { name: 'Save Settings' }));
    await waitFor(() => {
      expect(screen.getByText('Save failed')).toBeInTheDocument();
    });

    view.rerender(
      <SettingsModal
        open
        externalSidebarNav
        desktopSection="bot"
        config={baseConfig}
        health={baseHealth}
        appSettings={baseSettings}
        onClose={vi.fn()}
        onSave={vi.fn(async () => {})}
        onSaveAppSettings={onSaveAppSettings}
        onSetPrivateKey={vi.fn(async () => {})}
        onReboot={vi.fn(async () => {})}
        onAdvertise={vi.fn(async () => {})}
        onHealthRefresh={vi.fn(async () => {})}
        onRefreshAppSettings={vi.fn(async () => {})}
      />
    );

    expect(screen.queryByText('Save failed')).not.toBeInTheDocument();
  });

  it('does not call onClose after save/reboot flows in page mode', async () => {
    const onClose = vi.fn();
    const onSave = vi.fn(async () => {});
    const onSetPrivateKey = vi.fn(async () => {});
    const onReboot = vi.fn(async () => {});

    renderModal({
      pageMode: true,
      onClose,
      onSave,
      onSetPrivateKey,
      onReboot,
    });
    openRadioSection();

    fireEvent.click(screen.getByRole('button', { name: 'Save Radio Config & Reboot' }));
    await waitFor(() => {
      expect(onSave).toHaveBeenCalledTimes(1);
      expect(onReboot).toHaveBeenCalledTimes(1);
    });
    expect(onClose).not.toHaveBeenCalled();

    fireEvent.change(screen.getByLabelText('Set Private Key (write-only)'), {
      target: { value: 'a'.repeat(64) },
    });
    fireEvent.click(screen.getByRole('button', { name: 'Set Private Key & Reboot' }));

    await waitFor(() => {
      expect(onSetPrivateKey).toHaveBeenCalledWith('a'.repeat(64));
      expect(onReboot).toHaveBeenCalledTimes(2);
    });
    expect(onClose).not.toHaveBeenCalled();
  });

  it('stores and clears reopen-last-conversation preference locally', () => {
    window.location.hash = '#raw';
    renderModal();
    openLocalSection();

    const checkbox = screen.getByLabelText('Reopen to last viewed channel/conversation');
    expect(checkbox).not.toBeChecked();

    fireEvent.click(checkbox);

    expect(localStorage.getItem(REOPEN_LAST_CONVERSATION_KEY)).toBe('1');
    expect(localStorage.getItem(LAST_VIEWED_CONVERSATION_KEY)).toContain('"type":"raw"');

    fireEvent.click(checkbox);

    expect(localStorage.getItem(REOPEN_LAST_CONVERSATION_KEY)).toBeNull();
    expect(localStorage.getItem(LAST_VIEWED_CONVERSATION_KEY)).toBeNull();
  });

  it('purges decrypted raw packets via maintenance endpoint action', async () => {
    const runMaintenanceSpy = vi.spyOn(api, 'runMaintenance').mockResolvedValue({
      packets_deleted: 12,
      vacuumed: true,
    });

    renderModal();
    openDatabaseSection();

    fireEvent.click(screen.getByRole('button', { name: 'Purge Archival Raw Packets' }));

    await waitFor(() => {
      expect(runMaintenanceSpy).toHaveBeenCalledWith({ purgeLinkedRawPackets: true });
    });
  });

  it('renders statistics section with fetched data', async () => {
    const mockStats: StatisticsResponse = {
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
    };

    vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response(JSON.stringify(mockStats), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      })
    );

    renderModal({
      externalSidebarNav: true,
      desktopSection: 'statistics',
    });

    await waitFor(() => {
      expect(screen.getByText('Network')).toBeInTheDocument();
    });

    // Verify key labels are present
    expect(screen.getByText('Contacts')).toBeInTheDocument();
    expect(screen.getByText('Repeaters')).toBeInTheDocument();
    expect(screen.getByText('Direct Messages')).toBeInTheDocument();
    expect(screen.getByText('Channel Messages')).toBeInTheDocument();
    expect(screen.getByText('Sent (Outgoing)')).toBeInTheDocument();
    expect(screen.getByText('Total stored')).toBeInTheDocument();
    expect(screen.getByText('Decrypted')).toBeInTheDocument();
    expect(screen.getByText('Undecrypted')).toBeInTheDocument();
    expect(screen.getByText('Contacts heard')).toBeInTheDocument();
    expect(screen.getByText('Repeaters heard')).toBeInTheDocument();

    // Busiest channels
    expect(screen.getByText('general')).toBeInTheDocument();
    expect(screen.getByText('42 msgs')).toBeInTheDocument();
  });

  it('renders MQTT section with form inputs', () => {
    renderModal();
    openMqttSection();
    expandPrivateMqtt();

    // Publish checkboxes always visible
    expect(screen.getByText('Publish Messages')).toBeInTheDocument();
    expect(screen.getByText('Publish Raw Packets')).toBeInTheDocument();

    // Broker config hidden until a publish option is enabled
    expect(screen.queryByLabelText('Broker Host')).not.toBeInTheDocument();

    // Enable one publish option to reveal broker config
    fireEvent.click(screen.getByText('Publish Messages'));
    expect(screen.getByLabelText('Broker Host')).toBeInTheDocument();
    expect(screen.getByLabelText('Broker Port')).toBeInTheDocument();
    expect(screen.getByLabelText('Username')).toBeInTheDocument();
    expect(screen.getByLabelText('Password')).toBeInTheDocument();
    expect(screen.getByLabelText('Topic Prefix')).toBeInTheDocument();
  });

  it('saves MQTT settings through onSaveAppSettings', async () => {
    const { onSaveAppSettings } = renderModal({
      appSettings: { ...baseSettings, mqtt_publish_messages: true },
    });
    openMqttSection();
    expandPrivateMqtt();

    const hostInput = screen.getByLabelText('Broker Host');
    fireEvent.change(hostInput, { target: { value: 'mqtt.example.com' } });

    fireEvent.click(screen.getByRole('button', { name: 'Save MQTT Settings' }));

    await waitFor(() => {
      expect(onSaveAppSettings).toHaveBeenCalledWith(
        expect.objectContaining({
          mqtt_broker_host: 'mqtt.example.com',
          mqtt_broker_port: 1883,
        })
      );
    });
  });

  it('shows MQTT disabled status when mqtt_status is null', () => {
    renderModal({
      appSettings: {
        ...baseSettings,
        mqtt_broker_host: 'broker.local',
      },
    });
    openMqttSection();

    // Both MQTT and community MQTT show "Disabled" when null status
    const disabledElements = screen.getAllByText('Disabled');
    expect(disabledElements.length).toBeGreaterThanOrEqual(1);
  });

  it('shows MQTT connected status badge', () => {
    renderModal({
      appSettings: {
        ...baseSettings,
        mqtt_broker_host: 'broker.local',
      },
      health: {
        ...baseHealth,
        mqtt_status: 'connected',
      },
    });
    openMqttSection();

    expect(screen.getByText('Connected')).toBeInTheDocument();
  });

  it('renders community sharing section in MQTT tab', () => {
    renderModal();
    openMqttSection();
    expandCommunityMqtt();

    expect(screen.getByText('Community Analytics')).toBeInTheDocument();
    expect(screen.getByText('Enable Community Analytics')).toBeInTheDocument();
  });

  it('shows IATA input only when community sharing is enabled', () => {
    renderModal({
      appSettings: {
        ...baseSettings,
        community_mqtt_enabled: false,
      },
    });
    openMqttSection();
    expandCommunityMqtt();

    expect(screen.queryByLabelText('Region Code (IATA)')).not.toBeInTheDocument();

    // Enable community sharing
    fireEvent.click(screen.getByText('Enable Community Analytics'));
    expect(screen.getByLabelText('Region Code (IATA)')).toBeInTheDocument();
  });

  it('includes community MQTT fields in save payload', async () => {
    const { onSaveAppSettings } = renderModal({
      appSettings: {
        ...baseSettings,
        community_mqtt_enabled: true,
        community_mqtt_iata: 'DEN',
      },
    });
    openMqttSection();

    fireEvent.click(screen.getByRole('button', { name: 'Save MQTT Settings' }));

    await waitFor(() => {
      expect(onSaveAppSettings).toHaveBeenCalledWith(
        expect.objectContaining({
          community_mqtt_enabled: true,
          community_mqtt_iata: 'DEN',
        })
      );
    });
  });

  it('shows community MQTT connected status badge', () => {
    renderModal({
      appSettings: {
        ...baseSettings,
        community_mqtt_enabled: true,
      },
      health: {
        ...baseHealth,
        community_mqtt_status: 'connected',
      },
    });
    openMqttSection();

    // Community Analytics sub-section should show Connected
    const communitySection = screen.getByText('Community Analytics').closest('div');
    expect(communitySection).not.toBeNull();
    // Both MQTT and community could show "Connected" — check count
    const connectedElements = screen.getAllByText('Connected');
    expect(connectedElements.length).toBeGreaterThanOrEqual(1);
  });

  it('fetches statistics when expanded in mobile external-nav mode', async () => {
    const mockStats: StatisticsResponse = {
      busiest_channels_24h: [],
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
    };

    const fetchSpy = vi.spyOn(globalThis, 'fetch').mockResolvedValue(
      new Response(JSON.stringify(mockStats), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      })
    );

    renderModal({
      mobile: true,
      externalSidebarNav: true,
      desktopSection: 'radio',
    });

    expect(fetchSpy).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole('button', { name: /Statistics/i }));

    await waitFor(() => {
      expect(fetchSpy).toHaveBeenCalledWith('/api/statistics', expect.any(Object));
    });

    await waitFor(() => {
      expect(screen.getByText('Network')).toBeInTheDocument();
    });
  });
});
