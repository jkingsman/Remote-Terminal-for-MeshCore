import { useState, useEffect, useCallback, useMemo, useRef, type ReactNode } from 'react';
import { ChevronDown, Info } from 'lucide-react';
import { Input } from '../ui/input';
import { Label } from '../ui/label';
import { Button } from '../ui/button';
import { Separator } from '../ui/separator';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '../ui/dialog';
import { toast } from '../ui/sonner';
import { cn } from '@/lib/utils';
import { api } from '../../api';
import type { Channel, Contact, FanoutConfig, HealthStatus } from '../../types';

const TYPE_LABELS: Record<string, string> = {
  mqtt_private: 'Private MQTT',
  mqtt_community: 'Community Sharing',
  mqtt_ha: 'Home Assistant',
  discord_bridge: 'Discord Bridge',
  telegram_bridge: 'Telegram Bridge',
  webhook: 'Webhook',
  apprise: 'Apprise',
  sqs: 'Amazon SQS',
  map_upload: 'Map Upload',
};

const DEFAULT_COMMUNITY_PACKET_TOPIC_TEMPLATE = 'meshcore/{IATA}/{PUBLIC_KEY}/packets';
const DEFAULT_COMMUNITY_BROKER_HOST = 'mqtt-us-v1.letsmesh.net';
const DEFAULT_COMMUNITY_BROKER_HOST_EU = 'mqtt-eu-v1.letsmesh.net';
const DEFAULT_COMMUNITY_BROKER_PORT = 443;
const DEFAULT_COMMUNITY_TRANSPORT = 'websockets';
const DEFAULT_COMMUNITY_AUTH_MODE = 'token';
const DEFAULT_MESHRANK_BROKER_HOST = 'meshrank.net';
const DEFAULT_MESHRANK_BROKER_PORT = 8883;
const DEFAULT_MESHRANK_TRANSPORT = 'tcp';
const DEFAULT_MESHRANK_AUTH_MODE = 'none';
const DEFAULT_MESHRANK_IATA = 'XYZ';

function createCommunityConfigDefaults(
  overrides: Partial<Record<string, unknown>> = {}
): Record<string, unknown> {
  return {
    broker_host: DEFAULT_COMMUNITY_BROKER_HOST,
    broker_port: DEFAULT_COMMUNITY_BROKER_PORT,
    transport: DEFAULT_COMMUNITY_TRANSPORT,
    use_tls: true,
    tls_verify: true,
    auth_mode: DEFAULT_COMMUNITY_AUTH_MODE,
    username: '',
    password: '',
    iata: '',
    email: '',
    token_audience: '',
    topic_template: DEFAULT_COMMUNITY_PACKET_TOPIC_TEMPLATE,
    ...overrides,
  };
}

type DraftType =
  | 'mqtt_private'
  | 'mqtt_ha'
  | 'mqtt_community'
  | 'mqtt_community_meshrank'
  | 'mqtt_community_letsmesh_us'
  | 'mqtt_community_letsmesh_eu'
  | 'webhook'
  | 'apprise'
  | 'sqs'
  | 'discord_bridge'
  | 'telegram_bridge'
  | 'map_upload';

type CreateIntegrationDefinition = {
  value: DraftType;
  savedType: string;
  label: string;
  section: string;
  description: string;
  defaultName: string;
  nameMode: 'counted' | 'fixed';
  defaults: {
    config: Record<string, unknown>;
    scope: Record<string, unknown>;
  };
};

const CREATE_INTEGRATION_DEFINITIONS: readonly CreateIntegrationDefinition[] = [
  {
    value: 'mqtt_private',
    savedType: 'mqtt_private',
    label: 'Private MQTT',
    section: 'Private Forwarding',
    description:
      'Customizable-scope forwarding of all or some messages to an MQTT broker of your choosing, in raw and/or decrypted form.',
    defaultName: 'Private MQTT',
    nameMode: 'counted',
    defaults: {
      config: {
        broker_host: '',
        broker_port: 1883,
        username: '',
        password: '',
        use_tls: false,
        tls_insecure: false,
        topic_prefix: 'meshcore',
      },
      scope: { messages: 'all', raw_packets: 'all' },
    },
  },
  {
    value: 'mqtt_ha',
    savedType: 'mqtt_ha',
    label: 'Home Assistant MQTT Discovery',
    section: 'Private Forwarding',
    description:
      "Publishes MQTT Discovery payloads so mesh devices appear natively in Home Assistant. Requires HA's built-in MQTT integration connected to the same broker. Select specific contacts for GPS tracking and repeaters for telemetry sensors.",
    defaultName: 'Home Assistant',
    nameMode: 'fixed',
    defaults: {
      config: {
        broker_host: '',
        broker_port: 1883,
        username: '',
        password: '',
        use_tls: false,
        tls_insecure: false,
        topic_prefix: 'meshcore',
        tracked_contacts: [],
        tracked_repeaters: [],
      },
      scope: { messages: 'all', raw_packets: 'none' },
    },
  },
  {
    value: 'mqtt_community',
    savedType: 'mqtt_community',
    label: 'Community MQTT/meshcoretomqtt',
    section: 'Community Sharing',
    description:
      'MeshcoreToMQTT-compatible raw-packet feed publishing, compatible with community aggregators (in other words, make your companion radio also serve as an observer node). Superset of other Community MQTT presets.',
    defaultName: 'Community MQTT',
    nameMode: 'counted',
    defaults: {
      config: createCommunityConfigDefaults(),
      scope: { messages: 'none', raw_packets: 'all' },
    },
  },
  {
    value: 'mqtt_community_meshrank',
    savedType: 'mqtt_community',
    label: 'MeshRank',
    section: 'Community Sharing',
    description:
      'A community MQTT config preconfigured for MeshRank, requiring only the provided topic from your MeshRank configuration. A subset of the primary Community MQTT/meshcoretomqtt configuration; you are free to edit all configuration after creation.',
    defaultName: 'MeshRank',
    nameMode: 'fixed',
    defaults: {
      config: createCommunityConfigDefaults({
        broker_host: DEFAULT_MESHRANK_BROKER_HOST,
        broker_port: DEFAULT_MESHRANK_BROKER_PORT,
        transport: DEFAULT_MESHRANK_TRANSPORT,
        auth_mode: DEFAULT_MESHRANK_AUTH_MODE,
        iata: DEFAULT_MESHRANK_IATA,
        email: '',
        token_audience: '',
        topic_template: '',
      }),
      scope: { messages: 'none', raw_packets: 'all' },
    },
  },
  {
    value: 'mqtt_community_letsmesh_us',
    savedType: 'mqtt_community',
    label: 'LetsMesh (US)',
    section: 'Community Sharing',
    description:
      'A community MQTT config preconfigured for the LetsMesh US-ingest endpoint, requiring only your email and IATA region code. Good to use with an additional EU configuration for redundancy. A subset of the primary Community MQTT/meshcoretomqtt configuration; you are free to edit all configuration after creation.',
    defaultName: 'LetsMesh (US)',
    nameMode: 'fixed',
    defaults: {
      config: createCommunityConfigDefaults({
        broker_host: DEFAULT_COMMUNITY_BROKER_HOST,
        token_audience: DEFAULT_COMMUNITY_BROKER_HOST,
      }),
      scope: { messages: 'none', raw_packets: 'all' },
    },
  },
  {
    value: 'mqtt_community_letsmesh_eu',
    savedType: 'mqtt_community',
    label: 'LetsMesh (EU)',
    section: 'Community Sharing',
    description:
      'A community MQTT config preconfigured for the LetsMesh EU-ingest endpoint, requiring only your email and IATA region code. Good to use with an additional US configuration for redundancy. A subset of the primary Community MQTT/meshcoretomqtt configuration; you are free to edit all configuration after creation.',
    defaultName: 'LetsMesh (EU)',
    nameMode: 'fixed',
    defaults: {
      config: createCommunityConfigDefaults({
        broker_host: DEFAULT_COMMUNITY_BROKER_HOST_EU,
        token_audience: DEFAULT_COMMUNITY_BROKER_HOST_EU,
      }),
      scope: { messages: 'none', raw_packets: 'all' },
    },
  },
  {
    value: 'webhook',
    savedType: 'webhook',
    label: 'Webhook',
    section: 'Automation',
    description:
      'Generic webhook for decrypted channel/DM messages with customizable verb, method, and optional HMAC signature.',
    defaultName: 'Webhook',
    nameMode: 'counted',
    defaults: {
      config: {
        url: '',
        method: 'POST',
        headers: {},
        hmac_secret: '',
        hmac_header: '',
      },
      scope: { messages: 'all', raw_packets: 'none' },
    },
  },
  {
    value: 'apprise',
    savedType: 'apprise',
    label: 'Apprise',
    section: 'Automation',
    description:
      'A wide-ranging generic fanout, capable of forwarding decrypted channel/DM messages to Discord, Telegram, email, SMS, and many others.',
    defaultName: 'Apprise',
    nameMode: 'counted',
    defaults: {
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
    },
  },
  {
    value: 'sqs',
    savedType: 'sqs',
    label: 'Amazon SQS',
    section: 'Private Forwarding',
    description: 'Send full or scope-customized raw or decrypted packets to an SQS',
    defaultName: 'Amazon SQS',
    nameMode: 'counted',
    defaults: {
      config: {
        queue_url: '',
        region_name: '',
        endpoint_url: '',
        access_key_id: '',
        secret_access_key: '',
        session_token: '',
      },
      scope: { messages: 'all', raw_packets: 'none' },
    },
  },
  {
    value: 'discord_bridge',
    savedType: 'discord_bridge',
    label: 'Discord Bridge',
    section: 'Bridges',
    description: 'One-way bridge: mesh channels → Discord webhooks. DMs are never bridged.',
    defaultName: 'Discord Bridge',
    nameMode: 'counted',
    defaults: {
      config: { mappings: [], bridge_bot_responses: false, filter_profanity: 'off' },
      scope: { messages: 'all', raw_packets: 'none' },
    },
  },
  {
    value: 'telegram_bridge',
    savedType: 'telegram_bridge',
    label: 'Telegram Bridge',
    section: 'Bridges',
    description: 'One-way bridge: mesh channels → Telegram chats. DMs are never bridged.',
    defaultName: 'Telegram Bridge',
    nameMode: 'counted',
    defaults: {
      config: {
        api_token: '',
        mappings: [],
        bridge_bot_responses: false,
        filter_profanity: 'off',
      },
      scope: { messages: 'all', raw_packets: 'none' },
    },
  },
  {
    value: 'map_upload',
    savedType: 'map_upload',
    label: 'Map Upload',
    section: 'Community Sharing',
    description:
      'Upload repeaters and room servers to map.meshcore.io or a compatible map API endpoint.',
    defaultName: 'Map Upload',
    nameMode: 'counted',
    defaults: {
      config: {
        api_url: '',
        dry_run: true,
      },
      scope: { messages: 'none', raw_packets: 'all' },
    },
  },
];

const CREATE_INTEGRATION_DEFINITIONS_BY_VALUE = Object.fromEntries(
  CREATE_INTEGRATION_DEFINITIONS.map((definition) => [definition.value, definition])
) as Record<DraftType, CreateIntegrationDefinition>;

function getNumberInputValue(value: unknown, fallback: number): string | number {
  if (value === '') return '';
  if (typeof value === 'string') return value;
  if (typeof value === 'number' && Number.isFinite(value)) return value;
  return fallback;
}

function getOptionalNumberInputValue(value: unknown): string | number {
  if (value === '') return '';
  if (typeof value === 'string') return value;
  if (typeof value === 'number' && Number.isFinite(value)) return value;
  return '';
}

function parseIntegerInputValue(value: string): number | string {
  if (value === '') return '';
  const parsed = Number.parseInt(value, 10);
  return Number.isNaN(parsed) ? value : parsed;
}

function parseFloatInputValue(value: string): number | string {
  if (value === '') return '';
  const parsed = Number.parseFloat(value);
  return Number.isNaN(parsed) ? value : parsed;
}

function normalizeIntegrationConfigForSave(
  configType: string,
  config: Record<string, unknown>
): Record<string, unknown> {
  const normalized = { ...config };

  if (configType === 'mqtt_private') {
    const port = normalized.broker_port;
    if (port === '' || port === undefined || port === null) {
      normalized.broker_port = 1883;
    } else if (typeof port === 'string') {
      const parsed = Number.parseInt(port, 10);
      normalized.broker_port = Number.isNaN(parsed) ? 1883 : parsed;
    }

    const topicPrefix = String(normalized.topic_prefix ?? '').trim();
    normalized.topic_prefix = topicPrefix || 'meshcore';
  }

  if (configType === 'mqtt_community') {
    const brokerHost = String(normalized.broker_host ?? '').trim();
    normalized.broker_host = brokerHost || DEFAULT_COMMUNITY_BROKER_HOST;

    const port = normalized.broker_port;
    if (port === '' || port === undefined || port === null) {
      normalized.broker_port = DEFAULT_COMMUNITY_BROKER_PORT;
    } else if (typeof port === 'string') {
      const parsed = Number.parseInt(port, 10);
      normalized.broker_port = Number.isNaN(parsed) ? DEFAULT_COMMUNITY_BROKER_PORT : parsed;
    }

    const topicTemplate = String(normalized.topic_template ?? '').trim();
    normalized.topic_template = topicTemplate || DEFAULT_COMMUNITY_PACKET_TOPIC_TEMPLATE;
  }

  if (configType === 'map_upload') {
    const radius = normalized.geofence_radius_km;
    if (radius === '' || radius === undefined || radius === null) {
      normalized.geofence_radius_km = 0;
    } else if (typeof radius === 'string') {
      const parsed = Number.parseFloat(radius);
      normalized.geofence_radius_km = Number.isNaN(parsed) ? 0 : parsed;
    }
  }

  return normalized;
}

function isDraftType(value: string): value is DraftType {
  return value in CREATE_INTEGRATION_DEFINITIONS_BY_VALUE;
}

function getCreateIntegrationDefinition(draftType: DraftType) {
  return CREATE_INTEGRATION_DEFINITIONS_BY_VALUE[draftType];
}

function normalizeDraftName(draftType: DraftType, name: string, configs: FanoutConfig[]) {
  const definition = getCreateIntegrationDefinition(draftType);
  if (name) return name;
  if (definition.nameMode === 'fixed') return definition.defaultName;
  return getDefaultIntegrationName(definition.savedType, configs);
}

function normalizeDraftConfig(draftType: DraftType, config: Record<string, unknown>) {
  if (draftType === 'mqtt_community_meshrank') {
    const topicTemplate = String(config.topic_template || '').trim();
    if (!topicTemplate) {
      throw new Error('MeshRank packet topic is required');
    }

    return normalizeIntegrationConfigForSave('mqtt_community', {
      ...config,
      broker_host: DEFAULT_MESHRANK_BROKER_HOST,
      broker_port: DEFAULT_MESHRANK_BROKER_PORT,
      transport: DEFAULT_MESHRANK_TRANSPORT,
      auth_mode: DEFAULT_MESHRANK_AUTH_MODE,
      use_tls: true,
      tls_verify: true,
      iata: DEFAULT_MESHRANK_IATA,
      email: '',
      token_audience: '',
      topic_template: topicTemplate,
      username: '',
      password: '',
    });
  }

  if (draftType === 'mqtt_community_letsmesh_us' || draftType === 'mqtt_community_letsmesh_eu') {
    const brokerHost =
      draftType === 'mqtt_community_letsmesh_eu'
        ? DEFAULT_COMMUNITY_BROKER_HOST_EU
        : DEFAULT_COMMUNITY_BROKER_HOST;
    return normalizeIntegrationConfigForSave('mqtt_community', {
      ...config,
      broker_host: brokerHost,
      broker_port: DEFAULT_COMMUNITY_BROKER_PORT,
      transport: DEFAULT_COMMUNITY_TRANSPORT,
      auth_mode: DEFAULT_COMMUNITY_AUTH_MODE,
      use_tls: true,
      tls_verify: true,
      token_audience: brokerHost,
      topic_template: (config.topic_template as string) || DEFAULT_COMMUNITY_PACKET_TOPIC_TEMPLATE,
      username: '',
      password: '',
    });
  }

  return normalizeIntegrationConfigForSave(
    getCreateIntegrationDefinition(draftType).savedType,
    config
  );
}

function normalizeDraftScope(draftType: DraftType, scope: Record<string, unknown>) {
  if (getCreateIntegrationDefinition(draftType).savedType === 'mqtt_community') {
    return { messages: 'none', raw_packets: 'all' };
  }
  return scope;
}

function cloneDraftDefaults(draftType: DraftType) {
  const recipe = getCreateIntegrationDefinition(draftType);
  return {
    config: structuredClone(recipe.defaults.config),
    scope: structuredClone(recipe.defaults.scope),
  };
}

function CreateIntegrationDialog({
  open,
  options,
  selectedType,
  onOpenChange,
  onSelect,
  onCreate,
}: {
  open: boolean;
  options: readonly CreateIntegrationDefinition[];
  selectedType: DraftType | null;
  onOpenChange: (open: boolean) => void;
  onSelect: (type: DraftType) => void;
  onCreate: () => void;
}) {
  const selectedOption =
    options.find((option) => option.value === selectedType) ?? options[0] ?? null;
  const listRef = useRef<HTMLDivElement | null>(null);
  const [showScrollHint, setShowScrollHint] = useState(false);

  const updateScrollHint = useCallback(() => {
    const container = listRef.current;
    if (!container) {
      setShowScrollHint(false);
      return;
    }
    setShowScrollHint(container.scrollTop + container.clientHeight < container.scrollHeight - 8);
  }, []);

  useEffect(() => {
    if (!open) return;
    const frame = window.requestAnimationFrame(updateScrollHint);
    window.addEventListener('resize', updateScrollHint);
    return () => {
      window.cancelAnimationFrame(frame);
      window.removeEventListener('resize', updateScrollHint);
    };
  }, [open, options, updateScrollHint]);

  const sectionedOptions = [...new Set(options.map((o) => o.section))]
    .map((section) => ({
      section,
      options: options.filter((option) => option.section === section),
    }))
    .filter((group) => group.options.length > 0);

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent
        aria-describedby={undefined}
        hideCloseButton
        className="flex max-h-[calc(100dvh-2rem)] w-[96vw] max-w-[960px] flex-col overflow-hidden p-0 sm:rounded-xl"
      >
        <DialogHeader className="border-b border-border px-5 py-4">
          <DialogTitle>Create Integration</DialogTitle>
        </DialogHeader>

        <div className="grid min-h-0 flex-1 grid-cols-1 overflow-hidden md:grid-cols-[240px_minmax(0,1fr)]">
          <div className="relative border-b border-border bg-muted/20 md:border-b-0 md:border-r">
            <div
              ref={listRef}
              onScroll={updateScrollHint}
              className="max-h-56 overflow-y-auto p-2 md:max-h-[420px]"
            >
              <div className="space-y-4">
                {sectionedOptions.map((group) => (
                  <div key={group.section} className="space-y-1.5">
                    <div className="px-2 text-[0.6875rem] font-semibold uppercase tracking-wider text-muted-foreground">
                      {group.section}
                    </div>
                    {group.options.map((option) => {
                      const selected = option.value === selectedOption?.value;
                      return (
                        <button
                          key={option.value}
                          type="button"
                          className={cn(
                            'w-full rounded-md border px-3 py-2 text-left transition-colors',
                            selected
                              ? 'border-primary bg-accent text-foreground'
                              : 'border-transparent bg-transparent hover:bg-accent/70'
                          )}
                          aria-pressed={selected}
                          onClick={() => onSelect(option.value)}
                        >
                          <div className="text-sm font-medium">{option.label}</div>
                        </button>
                      );
                    })}
                  </div>
                ))}
              </div>
            </div>

            {showScrollHint && (
              <div className="pointer-events-none absolute inset-x-0 bottom-0 flex justify-center bg-gradient-to-t from-background via-background/85 to-transparent px-4 pb-2 pt-8">
                <div className="rounded-full border border-border/80 bg-background/95 px-2 py-1 text-muted-foreground shadow-sm">
                  <ChevronDown className="h-4 w-4" aria-hidden="true" />
                </div>
              </div>
            )}
          </div>

          <div className="min-h-0 space-y-4 overflow-y-auto px-5 py-5 md:min-h-[280px] md:max-h-[420px]">
            {selectedOption ? (
              <>
                <div className="space-y-1.5">
                  <div className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">
                    {selectedOption.section}
                  </div>
                  <h3 className="text-lg font-semibold">{selectedOption.label}</h3>
                </div>

                <p className="text-sm leading-6 text-muted-foreground">
                  {selectedOption.description}
                </p>
              </>
            ) : (
              <div className="flex h-full items-center justify-center text-sm text-muted-foreground">
                No integration types are currently available.
              </div>
            )}
          </div>
        </div>

        <DialogFooter className="gap-2 border-t border-border px-5 py-4 sm:justify-end">
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            Close
          </Button>
          <Button onClick={onCreate} disabled={!selectedOption}>
            Create
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

function getDetailTypeLabel(detailType: string) {
  if (isDraftType(detailType)) return getCreateIntegrationDefinition(detailType).label;
  return TYPE_LABELS[detailType] || detailType;
}

function fanoutDraftHasUnsavedChanges(
  original: FanoutConfig | null,
  current: {
    name: string;
    config: Record<string, unknown>;
    scope: Record<string, unknown>;
  }
) {
  if (!original) return false;
  return (
    current.name !== original.name ||
    JSON.stringify(current.config) !== JSON.stringify(original.config) ||
    JSON.stringify(current.scope) !== JSON.stringify(original.scope)
  );
}

function formatBrokerSummary(
  config: Record<string, unknown>,
  defaults: { host: string; port: number }
) {
  const host = (config.broker_host as string) || defaults.host;
  const port = typeof config.broker_port === 'number' ? config.broker_port : defaults.port;
  return `${host}:${port}`;
}

function formatPrivateTopicSummary(config: Record<string, unknown>) {
  const prefix = (config.topic_prefix as string) || 'meshcore';
  return `${prefix}/dm:<pubkey>, ${prefix}/gm:<channel>, ${prefix}/raw/...`;
}

function censorAppriseUrl(url: string): string {
  const protoMatch = url.match(/^[a-zA-Z][a-zA-Z0-9+.-]*:\/\//);
  if (protoMatch) return `${protoMatch[0]}********`;
  return '********';
}

function formatAppriseTargets(urls: string | undefined) {
  const targets = (urls || '')
    .split('\n')
    .map((line) => line.trim())
    .filter(Boolean);
  if (targets.length === 0) return 'No targets configured';

  return targets.map(censorAppriseUrl).join(', ');
}

function formatSqsQueueSummary(config: Record<string, unknown>) {
  const queueUrl = ((config.queue_url as string) || '').trim();
  if (!queueUrl) return 'No queue configured';
  return queueUrl;
}

function getDefaultIntegrationName(type: string, configs: FanoutConfig[]) {
  const label = TYPE_LABELS[type] || type;
  const nextIndex = configs.filter((cfg) => cfg.type === type).length + 1;
  return `${label} #${nextIndex}`;
}

function getStatusLabel(status: string | undefined, type?: string) {
  if (status === 'connected')
    return type === 'webhook' ||
      type === 'apprise' ||
      type === 'map_upload' ||
      type === 'discord_bridge' ||
      type === 'telegram_bridge'
      ? 'Active'
      : 'Connected';
  if (status === 'error') return 'Error';
  if (status === 'disconnected') return 'Disconnected';
  return 'Inactive';
}

function getStatusColor(status: string | undefined, enabled?: boolean) {
  if (enabled === false) return 'bg-muted-foreground';
  if (status === 'connected')
    return 'bg-status-connected shadow-[0_0_6px_hsl(var(--status-connected)/0.5)]';
  if (status === 'error' || status === 'disconnected')
    return 'bg-destructive shadow-[0_0_6px_hsl(var(--destructive)/0.5)]';
  return 'bg-muted-foreground';
}

function MqttPrivateConfigEditor({
  config,
  scope,
  onChange,
  onScopeChange,
}: {
  config: Record<string, unknown>;
  scope: Record<string, unknown>;
  onChange: (config: Record<string, unknown>) => void;
  onScopeChange: (scope: Record<string, unknown>) => void;
}) {
  return (
    <div className="space-y-3">
      <p className="text-[0.8125rem] text-muted-foreground">
        Forward mesh data to your own MQTT broker for home automation, logging, or alerting.
      </p>

      <div className="rounded-md border border-warning/50 bg-warning/10 px-3 py-2 text-xs text-warning">
        Outgoing messages (DMs and group messages) will be reported to private MQTT brokers in
        decrypted/plaintext form.
      </div>

      <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
        <div className="space-y-2">
          <Label htmlFor="fanout-mqtt-host">Broker Host</Label>
          <Input
            id="fanout-mqtt-host"
            type="text"
            placeholder="e.g. 192.168.1.100"
            value={(config.broker_host as string) || ''}
            onChange={(e) => onChange({ ...config, broker_host: e.target.value })}
          />
        </div>
        <div className="space-y-2">
          <Label htmlFor="fanout-mqtt-port">Broker Port</Label>
          <Input
            id="fanout-mqtt-port"
            type="number"
            min="1"
            max="65535"
            value={getNumberInputValue(config.broker_port, 1883)}
            onChange={(e) =>
              onChange({ ...config, broker_port: parseIntegerInputValue(e.target.value) })
            }
          />
        </div>
      </div>

      <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
        <div className="space-y-2">
          <Label htmlFor="fanout-mqtt-user">Username</Label>
          <Input
            id="fanout-mqtt-user"
            type="text"
            placeholder="Optional"
            value={(config.username as string) || ''}
            onChange={(e) => onChange({ ...config, username: e.target.value })}
          />
        </div>
        <div className="space-y-2">
          <Label htmlFor="fanout-mqtt-pass">Password</Label>
          <Input
            id="fanout-mqtt-pass"
            type="password"
            placeholder="Optional"
            value={(config.password as string) || ''}
            onChange={(e) => onChange({ ...config, password: e.target.value })}
          />
        </div>
      </div>

      <label className="flex items-center gap-3 cursor-pointer">
        <input
          type="checkbox"
          checked={!!config.use_tls}
          onChange={(e) => onChange({ ...config, use_tls: e.target.checked })}
          className="h-4 w-4 rounded border-border"
        />
        <span className="text-sm">Use TLS</span>
      </label>

      {!!config.use_tls && (
        <label className="flex items-center gap-3 cursor-pointer ml-7">
          <input
            type="checkbox"
            checked={!!config.tls_insecure}
            onChange={(e) => onChange({ ...config, tls_insecure: e.target.checked })}
            className="h-4 w-4 rounded border-border"
          />
          <span className="text-sm">Skip certificate verification</span>
        </label>
      )}

      <Separator />

      <div className="space-y-2">
        <Label htmlFor="fanout-mqtt-prefix">Topic Prefix</Label>
        <Input
          id="fanout-mqtt-prefix"
          type="text"
          placeholder="meshcore"
          value={(config.topic_prefix as string | undefined) ?? ''}
          onChange={(e) => onChange({ ...config, topic_prefix: e.target.value })}
        />
      </div>

      <Separator />

      <ScopeSelector scope={scope} onChange={onScopeChange} showRawPackets />
    </div>
  );
}

function MqttHaConfigEditor({
  config,
  scope,
  onChange,
  onScopeChange,
}: {
  config: Record<string, unknown>;
  scope: Record<string, unknown>;
  onChange: (config: Record<string, unknown>) => void;
  onScopeChange: (scope: Record<string, unknown>) => void;
}) {
  const [contacts, setContacts] = useState<Contact[]>([]);
  const [trackedRepeaters, setTrackedRepeaters] = useState<string[]>([]);
  const [contactSearch, setContactSearch] = useState('');
  const [radioConfig, setRadioConfig] = useState<{ public_key: string; name: string } | null>(null);

  useEffect(() => {
    (async () => {
      const all: Contact[] = [];
      const pageSize = 1000;
      let offset = 0;
      while (true) {
        const page = await api.getContacts(pageSize, offset);
        all.push(...page);
        if (page.length < pageSize) break;
        offset += pageSize;
      }
      setContacts(all);
    })().catch(console.error);

    api
      .getRadioConfig()
      .then((radio) => setRadioConfig({ public_key: radio.public_key, name: radio.name }))
      .catch(console.error);

    api
      .getSettings()
      .then((s) => setTrackedRepeaters(s.tracked_telemetry_repeaters ?? []))
      .catch(console.error);
  }, []);

  const selectedContacts = (config.tracked_contacts as string[]) || [];
  const selectedRepeaters = (config.tracked_repeaters as string[]) || [];

  const contactOptions = useMemo(
    () => contacts.filter((c) => c.type === 0 || c.type === 1 || c.type === 3),
    [contacts]
  );

  const repeaterOptions = useMemo(
    () => contacts.filter((c) => c.type === 2 && trackedRepeaters.includes(c.public_key)),
    [contacts, trackedRepeaters]
  );

  const contactSearchLower = contactSearch.toLowerCase().trim();
  const filteredContacts = useMemo(() => {
    const matches = contactOptions.filter((c) => {
      if (!contactSearchLower) return true;
      const name = (c.name || '').toLowerCase();
      const key = c.public_key.toLowerCase();
      return name.includes(contactSearchLower) || key.startsWith(contactSearchLower);
    });
    // Selected contacts sort to top
    return matches.sort((a, b) => {
      const aSelected = selectedContacts.includes(a.public_key) ? 0 : 1;
      const bSelected = selectedContacts.includes(b.public_key) ? 0 : 1;
      if (aSelected !== bSelected) return aSelected - bSelected;
      return (a.name || a.public_key).localeCompare(b.name || b.public_key);
    });
  }, [contactOptions, contactSearchLower, selectedContacts]);

  const selectedContactDetails = contactOptions.filter((c) =>
    selectedContacts.includes(c.public_key)
  );
  const selectedRepeaterDetails = repeaterOptions.filter((c) =>
    selectedRepeaters.includes(c.public_key)
  );
  const prefix = ((config.topic_prefix as string) || 'meshcore').trim() || 'meshcore';

  const nodeIdForKey = useCallback((publicKey: string) => publicKey.slice(0, 12).toLowerCase(), []);

  const topicSummary = useMemo(() => {
    const items: Array<{
      kind: 'radio' | 'event' | 'repeater' | 'contact';
      label: string;
      publicKey: string;
      nodeId: string;
      topics: string[];
    }> = [];

    if (radioConfig?.public_key) {
      const nodeId = nodeIdForKey(radioConfig.public_key);
      items.push({
        kind: 'radio',
        label: radioConfig.name || radioConfig.public_key.slice(0, 12),
        publicKey: radioConfig.public_key,
        nodeId,
        topics: [`${prefix}/${nodeId}/health`],
      });
      items.push({
        kind: 'event',
        label: radioConfig.name || radioConfig.public_key.slice(0, 12),
        publicKey: radioConfig.public_key,
        nodeId,
        topics: [`${prefix}/${nodeId}/events/message`],
      });
    }

    for (const repeater of selectedRepeaterDetails) {
      const nodeId = nodeIdForKey(repeater.public_key);
      items.push({
        kind: 'repeater',
        label: repeater.name || repeater.public_key.slice(0, 12),
        publicKey: repeater.public_key,
        nodeId,
        topics: [`${prefix}/${nodeId}/telemetry`],
      });
    }

    for (const contact of selectedContactDetails) {
      const nodeId = nodeIdForKey(contact.public_key);
      items.push({
        kind: 'contact',
        label: contact.name || contact.public_key.slice(0, 12),
        publicKey: contact.public_key,
        nodeId,
        topics: [`${prefix}/${nodeId}/gps`],
      });
    }

    return items;
  }, [nodeIdForKey, prefix, radioConfig, selectedContactDetails, selectedRepeaterDetails]);

  const kindLabel: Record<(typeof topicSummary)[number]['kind'], string> = {
    radio: 'Local radio state',
    event: 'Message events',
    repeater: 'Repeater telemetry',
    contact: 'Contact GPS',
  };
  const localRadioNodeId = radioConfig?.public_key
    ? nodeIdForKey(radioConfig.public_key)
    : '<radio_node_id>';
  const exampleRepeaterNodeId =
    selectedRepeaterDetails.length > 0
      ? nodeIdForKey(selectedRepeaterDetails[0].public_key)
      : '<repeater_node_id>';
  const exampleContactNodeId =
    selectedContactDetails.length > 0
      ? nodeIdForKey(selectedContactDetails[0].public_key)
      : '<contact_node_id>';

  const toggleTrackedContact = (key: string) => {
    const current = [...selectedContacts];
    const idx = current.indexOf(key);
    if (idx >= 0) current.splice(idx, 1);
    else current.push(key);
    onChange({ ...config, tracked_contacts: current });
  };

  const toggleTrackedRepeater = (key: string) => {
    const current = [...selectedRepeaters];
    const idx = current.indexOf(key);
    if (idx >= 0) current.splice(idx, 1);
    else current.push(key);
    onChange({ ...config, tracked_repeaters: current });
  };

  return (
    <div className="space-y-3">
      <div className="space-y-3 rounded-lg border border-primary/20 bg-primary/5 p-4">
        <div className="space-y-1">
          <h3 className="text-base font-semibold tracking-tight">Home Assistant MQTT Discovery</h3>
          <p className="text-sm text-muted-foreground">
            Publish discovery configs and MeshCore state to your MQTT broker so Home Assistant
            creates native devices, sensors, GPS trackers, and message events automatically.
          </p>
        </div>

        <div className="grid gap-2 md:grid-cols-3">
          <div className="rounded-md border border-border/70 bg-background/80 p-3">
            <div className="text-sm font-medium text-foreground">1. Same broker</div>
            <p className="mt-1 text-[0.8125rem] text-muted-foreground">
              Home Assistant&apos;s built-in MQTT integration must point at the same broker
              configured below.
            </p>
          </div>
          <div className="rounded-md border border-border/70 bg-background/80 p-3">
            <div className="text-sm font-medium text-foreground">2. Pick what to expose</div>
            <p className="mt-1 text-[0.8125rem] text-muted-foreground">
              Choose repeaters for telemetry sensors and contacts for GPS tracker entities.
            </p>
          </div>
          <div className="rounded-md border border-border/70 bg-background/80 p-3">
            <div className="text-sm font-medium text-foreground">3. Automate in HA</div>
            <p className="mt-1 text-[0.8125rem] text-muted-foreground">
              Radio health and message events publish continuously; repeater and contact data update
              when new data is heard or collected.
            </p>
          </div>
        </div>

        <p className="text-[0.8125rem] text-muted-foreground">
          Uses{' '}
          <span
            role="link"
            tabIndex={0}
            className="underline cursor-pointer hover:text-primary transition-colors"
            onClick={() =>
              window.open(
                'https://www.home-assistant.io/integrations/mqtt/#mqtt-discovery',
                '_blank'
              )
            }
            onKeyDown={(e) => {
              if (e.key === 'Enter')
                window.open(
                  'https://www.home-assistant.io/integrations/mqtt/#mqtt-discovery',
                  '_blank'
                );
            }}
          >
            MQTT Discovery
          </span>{' '}
          and the topic conventions documented in{' '}
          <span
            role="link"
            tabIndex={0}
            className="underline cursor-pointer hover:text-primary transition-colors"
            onClick={() =>
              window.open(
                'https://github.com/jkingsman/Remote-Terminal-for-MeshCore/blob/main/README_HA.md',
                '_blank'
              )
            }
            onKeyDown={(e) => {
              if (e.key === 'Enter')
                window.open(
                  'https://github.com/jkingsman/Remote-Terminal-for-MeshCore/blob/main/README_HA.md',
                  '_blank'
                );
            }}
          >
            README_HA.md
          </span>
          .
        </p>
      </div>

      <details className="group">
        <summary className="text-sm font-medium text-foreground cursor-pointer select-none flex items-center gap-1">
          <ChevronDown className="h-3 w-3 transition-transform group-open:rotate-0 -rotate-90" />
          What gets created in Home Assistant
        </summary>
        <div className="mt-2 space-y-2 text-sm text-muted-foreground rounded-md border border-border bg-muted/20 p-3">
          <div>
            <span className="font-medium text-foreground">Local radio device</span> (always)
            <span className="ml-1">&mdash; updates every 60s</span>
            <ul className="mt-0.5 ml-4 list-disc space-y-0.5">
              <li>
                <code className="text-[0.6875rem]">
                  {`binary_sensor.meshcore_${localRadioNodeId}_connected`}
                </code>{' '}
                &mdash; radio online/offline
              </li>
              <li>
                <code className="text-[0.6875rem]">
                  {`sensor.meshcore_${localRadioNodeId}_noise_floor`}
                </code>{' '}
                &mdash; radio noise floor (dBm)
              </li>
            </ul>
          </div>

          <div>
            <span className="font-medium text-foreground">Per tracked repeater</span> &mdash;
            updates on telemetry collect cycle (~8h) or manual dashboard fetch. Entity IDs shown use
            one repeater for example; these sensors are created for each selected repeater.
            <ul className="mt-0.5 ml-4 list-disc space-y-0.5">
              <li>
                <code className="text-[0.6875rem]">
                  {`sensor.meshcore_${exampleRepeaterNodeId}_battery_voltage`}
                </code>{' '}
                (V)
              </li>
              <li>
                <code className="text-[0.6875rem]">
                  {`sensor.meshcore_${exampleRepeaterNodeId}_noise_floor`}
                </code>
                ,{' '}
                <code className="text-[0.6875rem]">
                  {`sensor.meshcore_${exampleRepeaterNodeId}_last_rssi`}
                </code>
                ,{' '}
                <code className="text-[0.6875rem]">
                  {`sensor.meshcore_${exampleRepeaterNodeId}_last_snr`}
                </code>{' '}
                (dBm/dB)
              </li>
              <li>
                <code className="text-[0.6875rem]">
                  {`sensor.meshcore_${exampleRepeaterNodeId}_packets_received`}
                </code>
                ,{' '}
                <code className="text-[0.6875rem]">
                  {`sensor.meshcore_${exampleRepeaterNodeId}_packets_sent`}
                </code>
              </li>
              <li>
                <code className="text-[0.6875rem]">
                  {`sensor.meshcore_${exampleRepeaterNodeId}_uptime`}
                </code>{' '}
                (seconds)
              </li>
              <li>
                <code className="text-[0.6875rem]">
                  {`sensor.meshcore_${exampleRepeaterNodeId}_lpp_temperature_ch1`}
                </code>
                ,{' '}
                <code className="text-[0.6875rem]">
                  {`sensor.meshcore_${exampleRepeaterNodeId}_lpp_humidity_ch1`}
                </code>
                , etc. &mdash; CayenneLPP sensors (auto-detected from repeater)
              </li>
            </ul>
          </div>

          <div>
            <span className="font-medium text-foreground">Per tracked contact</span> &mdash; updates
            passively when advertisements with GPS are heard, and from any GPS reading in the
            contact&apos;s CayenneLPP telemetry. Shown for one contact; a tracker is created for
            each selected contact.
            <ul className="mt-0.5 ml-4 list-disc space-y-0.5">
              <li>
                <code className="text-[0.6875rem]">
                  {`device_tracker.meshcore_${exampleContactNodeId}`}
                </code>{' '}
                &mdash; latitude/longitude (plus altitude when telemetry provides it)
              </li>
              <li>
                <code className="text-[0.6875rem]">
                  {`sensor.meshcore_${exampleContactNodeId}_lpp_temperature_ch1`}
                </code>
                , etc. &mdash; CayenneLPP sensors (when the contact is tracked for telemetry; GPS
                goes to the tracker above)
              </li>
            </ul>
          </div>

          <div>
            <span className="font-medium text-foreground">Message events</span> &mdash; fires for
            each message matching the scope below
            <ul className="mt-0.5 ml-4 list-disc space-y-0.5">
              <li>
                <code className="text-[0.6875rem]">
                  {`event.meshcore_${localRadioNodeId}_messages`}
                </code>{' '}
                &mdash; trigger automations on sender, channel, or message content
              </li>
            </ul>
          </div>

          <p className="text-[0.6875rem] mt-1.5">
            Entity IDs use the first 12 characters of the node&apos;s public key. Entities are
            removed from HA when this integration is disabled or deleted. State topics are published
            under{' '}
            <code className="text-[0.6875rem]">{prefix}/&lt;node_id&gt;/health|telemetry|gps</code>.
          </p>
        </div>
      </details>

      <details className="group">
        <summary className="text-sm font-medium text-foreground cursor-pointer select-none flex items-center gap-1">
          <ChevronDown className="h-3 w-3 transition-transform group-open:rotate-0 -rotate-90" />
          Published topic summary
        </summary>
        <div className="mt-2 space-y-2 rounded-md border border-border bg-muted/20 p-3">
          <p className="text-xs text-muted-foreground">
            Home Assistant device and entity IDs are keyed off the first 12 characters of each
            node&apos;s public key, not the display name. Those same 12 characters are used in the
            MQTT state topics below.
          </p>
          {topicSummary.length === 0 ? (
            <p className="text-xs text-muted-foreground italic">
              No topic previews available yet. Connect to a radio to resolve the local radio key,
              and select contacts or repeaters above to preview their published topics.
            </p>
          ) : (
            <div className="space-y-2">
              {topicSummary.map((item) => (
                <div
                  key={`${item.kind}-${item.publicKey}`}
                  className="rounded border border-border/70 bg-background/70 p-2"
                >
                  <div className="flex flex-wrap items-center gap-x-2 gap-y-1 text-xs">
                    <span className="font-medium text-foreground">{kindLabel[item.kind]}</span>
                    <span className="text-foreground">{item.label}</span>
                    <span className="font-mono text-[0.6875rem] text-muted-foreground">
                      node id {item.nodeId}
                    </span>
                  </div>
                  <div className="mt-1 text-[0.6875rem] text-muted-foreground font-mono break-all">
                    key {item.publicKey}
                  </div>
                  {item.topics.map((topic) => (
                    <div
                      key={topic}
                      className="mt-1 rounded bg-muted px-2 py-1 text-[0.6875rem] font-mono text-foreground break-all"
                    >
                      {topic}
                    </div>
                  ))}
                </div>
              ))}
            </div>
          )}
          <p className="text-[0.6875rem] text-muted-foreground">
            Discovery config topics are also published under{' '}
            <code className="text-[0.6875rem]">homeassistant/.../config</code>, but the topics above
            are the primary runtime state and event topics.
          </p>
        </div>
      </details>

      <Separator />

      <h3 className="text-base font-semibold tracking-tight">MQTT Broker</h3>

      <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
        <div className="space-y-2">
          <Label htmlFor="fanout-ha-host">Broker Host</Label>
          <Input
            id="fanout-ha-host"
            type="text"
            placeholder="e.g. 192.168.1.100"
            value={(config.broker_host as string) || ''}
            onChange={(e) => onChange({ ...config, broker_host: e.target.value })}
          />
        </div>
        <div className="space-y-2">
          <Label htmlFor="fanout-ha-port">Broker Port</Label>
          <Input
            id="fanout-ha-port"
            type="number"
            min="1"
            max="65535"
            value={getNumberInputValue(config.broker_port, 1883)}
            onChange={(e) =>
              onChange({ ...config, broker_port: parseIntegerInputValue(e.target.value) })
            }
          />
        </div>
      </div>

      <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
        <div className="space-y-2">
          <Label htmlFor="fanout-ha-user">Username</Label>
          <Input
            id="fanout-ha-user"
            type="text"
            placeholder="Optional"
            value={(config.username as string) || ''}
            onChange={(e) => onChange({ ...config, username: e.target.value })}
          />
        </div>
        <div className="space-y-2">
          <Label htmlFor="fanout-ha-pass">Password</Label>
          <Input
            id="fanout-ha-pass"
            type="password"
            placeholder="Optional"
            value={(config.password as string) || ''}
            onChange={(e) => onChange({ ...config, password: e.target.value })}
          />
        </div>
      </div>

      <label className="flex items-center gap-3 cursor-pointer">
        <input
          type="checkbox"
          checked={!!config.use_tls}
          onChange={(e) => onChange({ ...config, use_tls: e.target.checked })}
          className="h-4 w-4 rounded border-border"
        />
        <span className="text-sm">Use TLS</span>
      </label>

      {!!config.use_tls && (
        <label className="flex items-center gap-3 cursor-pointer ml-7">
          <input
            type="checkbox"
            checked={!!config.tls_insecure}
            onChange={(e) => onChange({ ...config, tls_insecure: e.target.checked })}
            className="h-4 w-4 rounded border-border"
          />
          <span className="text-sm">Skip certificate verification</span>
        </label>
      )}

      <div className="space-y-2">
        <Label htmlFor="fanout-ha-prefix">Topic Prefix</Label>
        <Input
          id="fanout-ha-prefix"
          type="text"
          placeholder="meshcore"
          value={(config.topic_prefix as string | undefined) ?? ''}
          onChange={(e) => onChange({ ...config, topic_prefix: e.target.value })}
        />
        <p className="text-[0.6875rem] text-muted-foreground">
          State updates publish under <code className="text-[0.6875rem]">{prefix}/</code>. Discovery
          configs always use the <code className="text-[0.6875rem]">homeassistant/</code> prefix.
        </p>
      </div>

      <Separator />

      <div className="space-y-2">
        <h3 className="text-base font-semibold tracking-tight">GPS Tracked Contacts</h3>
        <p className="text-[0.8125rem] text-muted-foreground">
          Each selected contact becomes a <code className="text-[0.6875rem]">device_tracker</code>{' '}
          in HA, updated whenever an advertisement with GPS coordinates is heard. Useful for
          tracking mobile nodes on an HA map dashboard.
        </p>

        {selectedContactDetails.length > 0 && (
          <div className="flex flex-wrap gap-1.5">
            {selectedContactDetails.map((c) => (
              <span
                key={c.public_key}
                className="inline-flex items-center gap-1 text-[0.6875rem] px-2 py-0.5 rounded-full bg-primary/10 text-primary"
              >
                {c.name || c.public_key.slice(0, 12)}
                <button
                  type="button"
                  className="ml-0.5 hover:text-destructive transition-colors"
                  onClick={() => toggleTrackedContact(c.public_key)}
                  aria-label={`Remove ${c.name || c.public_key.slice(0, 12)}`}
                >
                  &times;
                </button>
              </span>
            ))}
          </div>
        )}

        {contactOptions.length === 0 ? (
          <p className="text-[0.8125rem] text-muted-foreground italic">No contacts available.</p>
        ) : (
          <>
            <Input
              type="text"
              placeholder={`Search ${contactOptions.length} contacts...`}
              value={contactSearch}
              onChange={(e) => setContactSearch(e.target.value)}
              className="h-8 text-sm"
            />
            <div className="max-h-48 overflow-y-auto space-y-1 rounded border border-border p-2">
              {filteredContacts.length === 0 ? (
                <p className="text-[0.8125rem] text-muted-foreground italic py-1">
                  No contacts match &ldquo;{contactSearch}&rdquo;
                </p>
              ) : (
                filteredContacts.map((c) => (
                  <label
                    key={c.public_key}
                    className="flex items-center gap-2 cursor-pointer text-sm"
                  >
                    <input
                      type="checkbox"
                      checked={selectedContacts.includes(c.public_key)}
                      onChange={() => toggleTrackedContact(c.public_key)}
                      className="h-3.5 w-3.5 rounded border-border"
                    />
                    <span className="truncate">{c.name || c.public_key.slice(0, 12)}</span>
                    <span className="text-[0.625rem] text-muted-foreground ml-auto font-mono shrink-0">
                      {c.public_key.slice(0, 12)}
                    </span>
                  </label>
                ))
              )}
            </div>
          </>
        )}
      </div>

      <Separator />

      <div className="space-y-2">
        <h3 className="text-base font-semibold tracking-tight">Telemetry Tracked Repeaters</h3>
        <p className="text-[0.8125rem] text-muted-foreground">
          Each selected repeater becomes an HA device with sensors for battery voltage, RSSI, SNR,
          noise floor, packet counts, and uptime. Data updates whenever telemetry is collected
          (auto-collect runs every ~8 hours, or on manual dashboard fetch). Only repeaters already
          in the auto-telemetry tracking list appear here (add new repeaters by logging into the
          repeater and opting in at the bottom of the page).
        </p>
        {trackedRepeaters.length === 0 ? (
          <div className="rounded-md border border-muted bg-muted/30 px-3 py-2 text-[0.8125rem] text-muted-foreground">
            No repeaters are being auto-tracked for telemetry. Add repeaters to the auto-telemetry
            tracking list in the Radio section first, then return here to select which ones to
            expose to HA.
          </div>
        ) : repeaterOptions.length === 0 ? (
          <p className="text-[0.8125rem] text-muted-foreground italic">
            Auto-tracked repeaters not found in contact list.
          </p>
        ) : (
          <div className="max-h-40 overflow-y-auto space-y-1 rounded border border-border p-2">
            {repeaterOptions.map((c) => (
              <label key={c.public_key} className="flex items-center gap-2 cursor-pointer text-sm">
                <input
                  type="checkbox"
                  checked={selectedRepeaters.includes(c.public_key)}
                  onChange={() => toggleTrackedRepeater(c.public_key)}
                  className="h-3.5 w-3.5 rounded border-border"
                />
                <span className="truncate">{c.name || c.public_key.slice(0, 12)}</span>
                <span className="text-[0.625rem] text-muted-foreground ml-auto font-mono">
                  {c.public_key.slice(0, 12)}
                </span>
              </label>
            ))}
          </div>
        )}
      </div>

      <Separator />

      <div className="space-y-2">
        <h3 className="text-base font-semibold tracking-tight">Message Events</h3>
        <p className="text-[0.8125rem] text-muted-foreground">
          Matching messages fire an{' '}
          <code className="text-[0.6875rem]">{`event.meshcore_${localRadioNodeId}_messages`}</code>{' '}
          entity in HA with sender, text, channel, and direction attributes. Use HA automations to
          trigger actions on specific messages, channels, or contacts.
        </p>
      </div>
      <ScopeSelector scope={scope} onChange={onScopeChange} />
    </div>
  );
}

function MqttCommunityConfigEditor({
  config,
  onChange,
}: {
  config: Record<string, unknown>;
  onChange: (config: Record<string, unknown>) => void;
}) {
  const authMode = (config.auth_mode as string) || DEFAULT_COMMUNITY_AUTH_MODE;

  return (
    <div className="space-y-3">
      <p className="text-[0.8125rem] text-muted-foreground">
        Advanced community MQTT editor. Use this for manual meshcoretomqtt-compatible setups or for
        modifying a saved preset after creation. Only raw RF packets are shared &mdash; never
        decrypted messages.
      </p>

      <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
        <div className="space-y-2">
          <Label htmlFor="fanout-comm-host">Broker Host</Label>
          <Input
            id="fanout-comm-host"
            type="text"
            placeholder={DEFAULT_COMMUNITY_BROKER_HOST}
            value={(config.broker_host as string | undefined) ?? ''}
            onChange={(e) => onChange({ ...config, broker_host: e.target.value })}
          />
        </div>
        <div className="space-y-2">
          <Label htmlFor="fanout-comm-port">Broker Port</Label>
          <Input
            id="fanout-comm-port"
            type="number"
            min="1"
            max="65535"
            value={getNumberInputValue(config.broker_port, DEFAULT_COMMUNITY_BROKER_PORT)}
            onChange={(e) =>
              onChange({
                ...config,
                broker_port: parseIntegerInputValue(e.target.value),
              })
            }
          />
        </div>
      </div>

      <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
        <div className="space-y-2">
          <Label htmlFor="fanout-comm-transport">Transport</Label>
          <select
            id="fanout-comm-transport"
            value={(config.transport as string) || DEFAULT_COMMUNITY_TRANSPORT}
            onChange={(e) => onChange({ ...config, transport: e.target.value })}
            className="w-full rounded-md border border-input bg-background px-3 py-2 text-sm"
          >
            <option value="websockets">WebSockets</option>
            <option value="tcp">TCP</option>
          </select>
        </div>
        <div className="space-y-2">
          <Label htmlFor="fanout-comm-auth-mode">Authentication</Label>
          <select
            id="fanout-comm-auth-mode"
            value={authMode}
            onChange={(e) => onChange({ ...config, auth_mode: e.target.value })}
            className="w-full rounded-md border border-input bg-background px-3 py-2 text-sm"
          >
            <option value="token">Token</option>
            <option value="none">None</option>
            <option value="password">Username / Password</option>
          </select>
          <p className="text-[0.8125rem] text-muted-foreground">
            LetsMesh uses <code>token</code> auth. MeshRank uses <code>none</code>.
          </p>
        </div>
      </div>

      {((config.transport as string) || DEFAULT_COMMUNITY_TRANSPORT) === 'websockets' && (
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
          <div className="space-y-2">
            <Label htmlFor="fanout-comm-ws-path">WebSocket Path</Label>
            <Input
              id="fanout-comm-ws-path"
              type="text"
              placeholder="/"
              value={(config.websocket_path as string | undefined) ?? ''}
              onChange={(e) => onChange({ ...config, websocket_path: e.target.value })}
            />
            <p className="text-[0.8125rem] text-muted-foreground">
              Defaults to <code>/</code> — use <code>/mqtt</code> for brokers that require a path
            </p>
          </div>
        </div>
      )}

      {authMode === 'token' && (
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
          <div className="space-y-2">
            <Label htmlFor="fanout-comm-token-audience">Token Audience</Label>
            <Input
              id="fanout-comm-token-audience"
              type="text"
              placeholder={(config.broker_host as string) || DEFAULT_COMMUNITY_BROKER_HOST}
              value={(config.token_audience as string | undefined) ?? ''}
              onChange={(e) => onChange({ ...config, token_audience: e.target.value })}
            />
            <p className="text-[0.8125rem] text-muted-foreground">
              Defaults to the broker host when blank
            </p>
          </div>
          <div className="space-y-2">
            <Label htmlFor="fanout-comm-email">Owner Email (optional)</Label>
            <Input
              id="fanout-comm-email"
              type="email"
              placeholder="you@example.com"
              value={(config.email as string) || ''}
              onChange={(e) => onChange({ ...config, email: e.target.value })}
            />
            <p className="text-[0.8125rem] text-muted-foreground">
              Used to claim your node on the community aggregator
            </p>
          </div>
        </div>
      )}

      {authMode === 'password' && (
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
          <div className="space-y-2">
            <Label htmlFor="fanout-comm-username">Username</Label>
            <Input
              id="fanout-comm-username"
              type="text"
              value={(config.username as string) || ''}
              onChange={(e) => onChange({ ...config, username: e.target.value })}
            />
          </div>
          <div className="space-y-2">
            <Label htmlFor="fanout-comm-password">Password</Label>
            <Input
              id="fanout-comm-password"
              type="password"
              value={(config.password as string) || ''}
              onChange={(e) => onChange({ ...config, password: e.target.value })}
            />
          </div>
        </div>
      )}

      <div className="space-y-2">
        <label className="flex items-center gap-3 cursor-pointer">
          <input
            type="checkbox"
            checked={config.use_tls === undefined ? true : !!config.use_tls}
            onChange={(e) => onChange({ ...config, use_tls: e.target.checked })}
            className="h-4 w-4 rounded border-border"
          />
          <span className="text-sm">Use TLS</span>
        </label>

        <label className="flex items-center gap-3 cursor-pointer ml-7">
          <input
            type="checkbox"
            checked={config.tls_verify === undefined ? true : !!config.tls_verify}
            onChange={(e) => onChange({ ...config, tls_verify: e.target.checked })}
            className="h-4 w-4 rounded border-border"
            disabled={config.use_tls === undefined ? false : !config.use_tls}
          />
          <span className="text-sm">Verify TLS certificates</span>
        </label>
      </div>

      <div className="space-y-2">
        <Label htmlFor="fanout-comm-iata">Region Code (IATA)</Label>
        <Input
          id="fanout-comm-iata"
          type="text"
          maxLength={3}
          placeholder="e.g. DEN, LAX, NYC"
          value={(config.iata as string) || ''}
          onChange={(e) => onChange({ ...config, iata: e.target.value.toUpperCase() })}
          className="w-32"
        />
        <p className="text-[0.8125rem] text-muted-foreground">
          Your nearest airport&apos;s IATA code (required)
        </p>
      </div>

      <div className="space-y-2">
        <Label htmlFor="fanout-comm-topic-template">Packet Topic Template</Label>
        <Input
          id="fanout-comm-topic-template"
          type="text"
          placeholder={DEFAULT_COMMUNITY_PACKET_TOPIC_TEMPLATE}
          value={(config.topic_template as string | undefined) ?? ''}
          onChange={(e) => onChange({ ...config, topic_template: e.target.value })}
        />
        <p className="text-[0.8125rem] text-muted-foreground">
          Use <code>{'{IATA}'}</code> and <code>{'{PUBLIC_KEY}'}</code>. Default:{' '}
          <code>{DEFAULT_COMMUNITY_PACKET_TOPIC_TEMPLATE}</code>
        </p>
      </div>
    </div>
  );
}

function MeshRankConfigEditor({
  config,
  onChange,
}: {
  config: Record<string, unknown>;
  onChange: (config: Record<string, unknown>) => void;
}) {
  return (
    <div className="space-y-3">
      <p className="text-[0.8125rem] text-muted-foreground">
        Pre-filled MeshRank setup. This saves as a regular Community MQTT integration once created,
        but only asks for the MeshRank packet topic you were given.
      </p>

      <div className="rounded-md border border-input bg-muted/40 px-3 py-2 text-xs text-muted-foreground">
        Broker <code>{DEFAULT_MESHRANK_BROKER_HOST}</code> on port{' '}
        <code>{DEFAULT_MESHRANK_BROKER_PORT}</code> via <code>{DEFAULT_MESHRANK_TRANSPORT}</code>,
        auth <code>{DEFAULT_MESHRANK_AUTH_MODE}</code>, TLS on, certificate verification on, region
        code fixed to <code>{DEFAULT_MESHRANK_IATA}</code>.
      </div>

      <div className="space-y-2">
        <Label htmlFor="fanout-meshrank-topic-template">Packet Topic Template</Label>
        <Input
          id="fanout-meshrank-topic-template"
          type="text"
          placeholder="meshrank/uplink/B435F6D5F7896B74C6B995FE221C2C1F/{PUBLIC_KEY}/packets"
          value={(config.topic_template as string) || ''}
          onChange={(e) =>
            onChange({
              ...config,
              iata: DEFAULT_MESHRANK_IATA,
              topic_template: e.target.value,
            })
          }
        />
        <p className="text-[0.8125rem] text-muted-foreground">
          Paste the full topic template from your MeshRank config, for example{' '}
          <code>meshrank/uplink/B435F6D5F7896B74C6B995FE221C2C1F/{'{PUBLIC_KEY}'}/packets</code>.
        </p>
      </div>
    </div>
  );
}

function LetsMeshConfigEditor({
  config,
  onChange,
  brokerHost,
}: {
  config: Record<string, unknown>;
  onChange: (config: Record<string, unknown>) => void;
  brokerHost: string;
}) {
  return (
    <div className="space-y-3">
      <p className="text-[0.8125rem] text-muted-foreground">
        Pre-filled LetsMesh setup. This saves as a regular Community MQTT integration once created,
        but only asks for the values LetsMesh expects from you.
      </p>

      <div className="rounded-md border border-input bg-muted/40 px-3 py-2 text-xs text-muted-foreground">
        Broker <code>{brokerHost}</code> on port <code>{DEFAULT_COMMUNITY_BROKER_PORT}</code> via{' '}
        <code>{DEFAULT_COMMUNITY_TRANSPORT}</code>, auth <code>{DEFAULT_COMMUNITY_AUTH_MODE}</code>,
        TLS on, certificate verification on, token audience fixed to <code>{brokerHost}</code>.
      </div>

      <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
        <div className="space-y-2">
          <Label htmlFor="fanout-letsmesh-email">Email</Label>
          <Input
            id="fanout-letsmesh-email"
            type="email"
            placeholder="you@example.com"
            value={(config.email as string) || ''}
            onChange={(e) =>
              onChange({ ...config, email: e.target.value, broker_host: brokerHost })
            }
          />
        </div>
        <div className="space-y-2">
          <Label htmlFor="fanout-letsmesh-iata">Region Code (IATA)</Label>
          <Input
            id="fanout-letsmesh-iata"
            type="text"
            maxLength={3}
            placeholder="e.g. DEN, LAX, NYC"
            value={(config.iata as string) || ''}
            onChange={(e) =>
              onChange({
                ...config,
                broker_host: brokerHost,
                token_audience: brokerHost,
                iata: e.target.value.toUpperCase(),
              })
            }
            className="w-32"
          />
        </div>
      </div>
    </div>
  );
}

/** Shared trailing fields for the Discord/Telegram bridge editors. */
function BridgeCommonFields({
  idPrefix,
  config,
  onChange,
}: {
  idPrefix: string;
  config: Record<string, unknown>;
  onChange: (config: Record<string, unknown>) => void;
}) {
  return (
    <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
      <div className="space-y-2">
        <Label htmlFor={`${idPrefix}-profanity`}>Profanity filter</Label>
        <select
          id={`${idPrefix}-profanity`}
          value={(config.filter_profanity as string) || 'off'}
          onChange={(e) => onChange({ ...config, filter_profanity: e.target.value })}
          className="w-full rounded-md border border-input bg-background px-3 py-2 text-sm"
        >
          <option value="off">off</option>
          <option value="censor">censor</option>
          <option value="drop">drop</option>
        </select>
      </div>
      <label className="flex items-center gap-2.5 cursor-pointer sm:mt-7">
        <input
          type="checkbox"
          checked={Boolean(config.bridge_bot_responses)}
          onChange={(e) => onChange({ ...config, bridge_bot_responses: e.target.checked })}
          className="w-4 h-4 rounded border-input accent-primary"
        />
        <span className="text-[0.8125rem]">Also bridge this app's own outgoing messages</span>
      </label>
    </div>
  );
}

function getBridgeMappings(config: Record<string, unknown>): Record<string, unknown>[] {
  return Array.isArray(config.mappings) ? (config.mappings as Record<string, unknown>[]) : [];
}

function DiscordBridgeConfigEditor({
  config,
  onChange,
}: {
  config: Record<string, unknown>;
  onChange: (config: Record<string, unknown>) => void;
}) {
  const [channels, setChannels] = useState<Channel[]>([]);

  useEffect(() => {
    api.getChannels().then(setChannels).catch(console.error);
  }, []);

  const mappings = getBridgeMappings(config);

  const updateMapping = (index: number, patch: Record<string, unknown>) => {
    const next = mappings.map((m, i) => (i === index ? { ...m, ...patch } : m));
    onChange({ ...config, mappings: next });
  };

  return (
    <div className="space-y-3">
      <p className="text-[0.8125rem] text-muted-foreground">
        One-way: mesh → Discord. DMs are never bridged. Uses the message fanout bus, same as MQTT.
      </p>

      <div className="space-y-2">
        <Label>Bridged channels → Discord webhooks</Label>
        {mappings.length === 0 && <p className="text-xs text-muted-foreground">No mappings yet.</p>}
        {mappings.map((mapping, index) => (
          <div key={index} className="flex items-start gap-2">
            <select
              value={(mapping.channel_key as string) || ''}
              onChange={(e) => updateMapping(index, { channel_key: e.target.value })}
              className="w-44 rounded-md border border-input bg-background px-3 py-2 text-sm"
              aria-label={`Mapping ${index + 1} channel`}
            >
              <option value="">Select channel…</option>
              {channels.map((c) => (
                <option key={c.key} value={c.key}>
                  {c.name}
                </option>
              ))}
            </select>
            <textarea
              value={
                Array.isArray(mapping.webhook_urls)
                  ? (mapping.webhook_urls as string[]).join('\n')
                  : ''
              }
              onChange={(e) =>
                updateMapping(index, {
                  webhook_urls: e.target.value
                    .split('\n')
                    .map((line) => line.trim())
                    .filter(Boolean),
                })
              }
              placeholder={'https://discord.com/api/webhooks/… (one per line)'}
              className="flex-1 rounded-md border border-input bg-background px-3 py-2 text-sm font-mono min-h-[60px]"
              aria-label={`Mapping ${index + 1} webhook URLs`}
            />
            <Button
              type="button"
              variant="ghost"
              size="sm"
              className="h-9 px-2 text-xs text-destructive hover:bg-destructive/10"
              onClick={() =>
                onChange({ ...config, mappings: mappings.filter((_, i) => i !== index) })
              }
            >
              Remove
            </Button>
          </div>
        ))}
        <Button
          type="button"
          variant="outline"
          size="sm"
          onClick={() =>
            onChange({
              ...config,
              mappings: [...mappings, { channel_key: '', webhook_urls: [] }],
            })
          }
        >
          + Add mapping
        </Button>
      </div>

      <Separator />

      <BridgeCommonFields idPrefix="fanout-discord-bridge" config={config} onChange={onChange} />
    </div>
  );
}

function TelegramBridgeConfigEditor({
  config,
  onChange,
}: {
  config: Record<string, unknown>;
  onChange: (config: Record<string, unknown>) => void;
}) {
  const [channels, setChannels] = useState<Channel[]>([]);

  useEffect(() => {
    api.getChannels().then(setChannels).catch(console.error);
  }, []);

  const mappings = getBridgeMappings(config);

  const updateMapping = (index: number, patch: Record<string, unknown>) => {
    const next = mappings.map((m, i) => (i === index ? { ...m, ...patch } : m));
    onChange({ ...config, mappings: next });
  };

  return (
    <div className="space-y-3">
      <p className="text-[0.8125rem] text-muted-foreground">
        One-way: mesh → Telegram. DMs are never bridged. Uses the message fanout bus, same as MQTT.
      </p>

      <div className="space-y-2">
        <Label htmlFor="fanout-telegram-bridge-token">Bot API token</Label>
        <Input
          id="fanout-telegram-bridge-token"
          type="password"
          placeholder="123456:ABC-DEF…"
          value={(config.api_token as string) || ''}
          onChange={(e) => onChange({ ...config, api_token: e.target.value })}
        />
      </div>

      <div className="space-y-2">
        <Label>Bridged channels → Telegram chats</Label>
        {mappings.length === 0 && <p className="text-xs text-muted-foreground">No mappings yet.</p>}
        {mappings.map((mapping, index) => (
          <div key={index} className="flex items-center gap-2">
            <select
              value={(mapping.channel_key as string) || ''}
              onChange={(e) => updateMapping(index, { channel_key: e.target.value })}
              className="w-44 rounded-md border border-input bg-background px-3 py-2 text-sm"
              aria-label={`Mapping ${index + 1} channel`}
            >
              <option value="">Select channel…</option>
              {channels.map((c) => (
                <option key={c.key} value={c.key}>
                  {c.name}
                </option>
              ))}
            </select>
            <Input
              value={(mapping.chat_id as string) || ''}
              onChange={(e) => updateMapping(index, { chat_id: e.target.value })}
              placeholder="chat id, e.g. -1001234567890"
              className="flex-1 font-mono"
              aria-label={`Mapping ${index + 1} chat id`}
            />
            <Button
              type="button"
              variant="ghost"
              size="sm"
              className="h-9 px-2 text-xs text-destructive hover:bg-destructive/10"
              onClick={() =>
                onChange({ ...config, mappings: mappings.filter((_, i) => i !== index) })
              }
            >
              Remove
            </Button>
          </div>
        ))}
        <Button
          type="button"
          variant="outline"
          size="sm"
          onClick={() =>
            onChange({
              ...config,
              mappings: [...mappings, { channel_key: '', chat_id: '' }],
            })
          }
        >
          + Add mapping
        </Button>
      </div>

      <Separator />

      <BridgeCommonFields idPrefix="fanout-telegram-bridge" config={config} onChange={onChange} />
    </div>
  );
}

function MapUploadConfigEditor({
  config,
  onChange,
}: {
  config: Record<string, unknown>;
  onChange: (config: Record<string, unknown>) => void;
}) {
  const isDryRun = config.dry_run !== false;
  const [radioLat, setRadioLat] = useState<number | null>(null);
  const [radioLon, setRadioLon] = useState<number | null>(null);

  useEffect(() => {
    api
      .getRadioConfig()
      .then((rc) => {
        setRadioLat(rc.lat ?? 0);
        setRadioLon(rc.lon ?? 0);
      })
      .catch(() => {
        setRadioLat(0);
        setRadioLon(0);
      });
  }, []);

  const radioLatLonConfigured =
    radioLat !== null && radioLon !== null && !(radioLat === 0 && radioLon === 0);

  return (
    <div className="space-y-3">
      <p className="text-[0.8125rem] text-muted-foreground">
        Automatically upload heard repeater and room server advertisements to{' '}
        <a
          href="https://map.meshcore.io"
          target="_blank"
          rel="noopener noreferrer"
          className="underline hover:text-foreground"
        >
          map.meshcore.io
        </a>
        . Requires the radio&apos;s private key to be available (firmware must have{' '}
        <code>ENABLE_PRIVATE_KEY_EXPORT=1</code>). Only raw RF packets are shared &mdash; never
        decrypted messages.
      </p>

      <div className="rounded-md border border-warning/50 bg-warning/10 px-3 py-2 text-xs text-warning">
        <strong>Dry Run is {isDryRun ? 'ON' : 'OFF'}.</strong>{' '}
        {isDryRun
          ? 'No uploads will be sent. Check the backend logs to verify the payload looks correct before enabling live sends.'
          : 'Live uploads are enabled. Each advert is rate-limited to once per hour per node.'}
      </div>

      <label className="flex items-center gap-3 cursor-pointer">
        <input
          type="checkbox"
          checked={isDryRun}
          onChange={(e) => onChange({ ...config, dry_run: e.target.checked })}
          className="h-4 w-4 rounded border-border"
        />
        <div>
          <span className="text-sm font-medium">Dry Run (log only, no uploads)</span>
          <p className="text-[0.8125rem] text-muted-foreground">
            When enabled, upload payloads are logged at INFO level but not sent. Disable once you
            have confirmed the logged output looks correct.
          </p>
        </div>
      </label>

      <Separator />

      <div className="space-y-2">
        <Label htmlFor="fanout-map-api-url">API URL (optional)</Label>
        <Input
          id="fanout-map-api-url"
          type="url"
          placeholder="https://map.meshcore.io/api/v1/uploader/node"
          value={(config.api_url as string) || ''}
          onChange={(e) => onChange({ ...config, api_url: e.target.value })}
        />
        <p className="text-[0.8125rem] text-muted-foreground">
          Leave blank to use the default <code>map.meshcore.io</code> endpoint.
        </p>
      </div>

      <Separator />

      <label className="flex items-center gap-3 cursor-pointer">
        <input
          type="checkbox"
          checked={!!config.geofence_enabled}
          onChange={(e) => onChange({ ...config, geofence_enabled: e.target.checked })}
          className="h-4 w-4 rounded border-border"
        />
        <div>
          <span className="text-sm font-medium">Enable Geofence</span>
          <p className="text-[0.8125rem] text-muted-foreground">
            Only upload nodes whose location falls within the configured radius of your radio&apos;s
            own position. Helps exclude nodes with false or spoofed coordinates. Uses the
            latitude/longitude set in Radio Settings.
          </p>
        </div>
      </label>

      {!!config.geofence_enabled && (
        <div className="space-y-3 pl-7">
          {!radioLatLonConfigured && (
            <div className="rounded-md border border-warning/50 bg-warning/10 px-3 py-2 text-xs text-warning">
              Your radio does not currently have a latitude/longitude configured. Geofencing will be
              silently skipped until coordinates are set in{' '}
              <strong>Settings &rarr; Radio &rarr; Location</strong>.
            </div>
          )}
          {radioLatLonConfigured && (
            <p className="text-[0.8125rem] text-muted-foreground">
              Using radio position{' '}
              <code>
                {radioLat?.toFixed(5)}, {radioLon?.toFixed(5)}
              </code>{' '}
              as the geofence center. Update coordinates in Radio Settings to move the center.
            </p>
          )}
          <div className="space-y-2">
            <Label htmlFor="fanout-map-geofence-radius">Radius (km)</Label>
            <Input
              id="fanout-map-geofence-radius"
              type="number"
              min="0"
              step="any"
              placeholder="e.g. 100"
              value={getOptionalNumberInputValue(config.geofence_radius_km)}
              onChange={(e) =>
                onChange({
                  ...config,
                  geofence_radius_km: parseFloatInputValue(e.target.value),
                })
              }
            />
            <p className="text-[0.8125rem] text-muted-foreground">
              Nodes further than this distance from your radio&apos;s position will not be uploaded.
            </p>
          </div>
        </div>
      )}
    </div>
  );
}

type ScopeMode = 'all' | 'none' | 'only' | 'except';

function getScopeMode(value: unknown): ScopeMode {
  if (value === 'all') return 'all';
  if (value === 'none') return 'none';
  if (typeof value === 'object' && value !== null) {
    // Check if either channels or contacts uses the {except: [...]} shape
    const obj = value as Record<string, unknown>;
    const ch = obj.channels;
    const co = obj.contacts;
    if (
      (typeof ch === 'object' && ch !== null && !Array.isArray(ch)) ||
      (typeof co === 'object' && co !== null && !Array.isArray(co))
    ) {
      return 'except';
    }
    return 'only';
  }
  return 'all';
}

/** Extract the key list from a filter value, whether it's a plain list or {except: [...]} */
function getFilterKeys(filter: unknown): string[] {
  if (Array.isArray(filter)) return filter as string[];
  if (typeof filter === 'object' && filter !== null && 'except' in filter)
    return ((filter as Record<string, unknown>).except as string[]) ?? [];
  return [];
}

const MAX_SCOPE_PILL_DISPLAY = 32;

interface PillsSearchListItem {
  key: string;
  label: string;
  /** Optional trailing monospace hint (e.g. pubkey prefix) */
  trailing?: string;
}

/**
 * Search-and-pills picker for the generic fanout scope selector.
 * Shows selected items as removable pills (up to MAX_SCOPE_PILL_DISPLAY),
 * a search input, and a scrollable list of filtered items with checkboxes.
 * When more than MAX_SCOPE_PILL_DISPLAY items are selected, the pill row
 * collapses to a single informational badge to keep the interface clean.
 */
function PillsSearchList({
  label,
  labelSuffix,
  items,
  selectedKeys,
  onToggle,
  onAll,
  onNone,
  searchPlaceholder,
  emptyItemsMessage,
}: {
  label: string;
  labelSuffix: string;
  items: PillsSearchListItem[];
  selectedKeys: string[];
  onToggle: (key: string) => void;
  onAll: () => void;
  onNone: () => void;
  searchPlaceholder: string;
  emptyItemsMessage: string;
}) {
  const [search, setSearch] = useState('');
  const searchLower = search.toLowerCase().trim();

  const filtered = useMemo(() => {
    const matches = items.filter((it) => {
      if (!searchLower) return true;
      return (
        it.label.toLowerCase().includes(searchLower) || it.key.toLowerCase().startsWith(searchLower)
      );
    });
    // Selected items sort to top (mirrors the Home Assistant tracked-contacts picker)
    return matches.sort((a, b) => {
      const aSel = selectedKeys.includes(a.key) ? 0 : 1;
      const bSel = selectedKeys.includes(b.key) ? 0 : 1;
      if (aSel !== bSel) return aSel - bSel;
      return a.label.localeCompare(b.label);
    });
  }, [items, searchLower, selectedKeys]);

  const selectedDetails = useMemo(
    () => items.filter((it) => selectedKeys.includes(it.key)),
    [items, selectedKeys]
  );
  const overPillLimit = selectedDetails.length > MAX_SCOPE_PILL_DISPLAY;

  return (
    <div className="space-y-1">
      <div className="flex items-center justify-between">
        <Label className="text-xs">
          {label} <span className="text-muted-foreground font-normal">({labelSuffix})</span>
        </Label>
        <span className="flex gap-1">
          <button
            type="button"
            className="text-xs text-muted-foreground hover:text-foreground transition-colors"
            onClick={onAll}
          >
            All
          </button>
          <span className="text-xs text-muted-foreground">/</span>
          <button
            type="button"
            className="text-xs text-muted-foreground hover:text-foreground transition-colors"
            onClick={onNone}
          >
            None
          </button>
        </span>
      </div>

      {selectedDetails.length > 0 && (
        <div className="flex flex-wrap gap-1.5">
          {overPillLimit ? (
            <span className="inline-flex items-center text-[0.6875rem] px-2 py-0.5 rounded-full bg-muted text-muted-foreground">
              &gt;{MAX_SCOPE_PILL_DISPLAY} selections made; hiding selection preview to keep the
              interface clean
            </span>
          ) : (
            selectedDetails.map((it) => (
              <span
                key={it.key}
                className="inline-flex items-center gap-1 text-[0.6875rem] px-2 py-0.5 rounded-full bg-primary/10 text-primary"
              >
                {it.label}
                <button
                  type="button"
                  className="ml-0.5 hover:text-destructive transition-colors"
                  onClick={() => onToggle(it.key)}
                  aria-label={`Remove ${it.label}`}
                >
                  &times;
                </button>
              </span>
            ))
          )}
        </div>
      )}

      {items.length === 0 ? (
        <p className="text-[0.8125rem] text-muted-foreground italic">{emptyItemsMessage}</p>
      ) : (
        <>
          <Input
            type="text"
            placeholder={searchPlaceholder}
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            className="h-8 text-sm"
          />
          <div className="max-h-48 overflow-y-auto space-y-1 rounded border border-border p-2">
            {filtered.length === 0 ? (
              <p className="text-[0.8125rem] text-muted-foreground italic py-1">
                No {label.toLowerCase()} match &ldquo;{search}&rdquo;
              </p>
            ) : (
              filtered.map((it) => (
                <label key={it.key} className="flex items-center gap-2 cursor-pointer text-sm">
                  <input
                    type="checkbox"
                    checked={selectedKeys.includes(it.key)}
                    onChange={() => onToggle(it.key)}
                    className="h-3.5 w-3.5 rounded border-input accent-primary"
                  />
                  <span className="truncate">{it.label}</span>
                  {it.trailing && (
                    <span className="text-[0.625rem] text-muted-foreground ml-auto font-mono shrink-0">
                      {it.trailing}
                    </span>
                  )}
                </label>
              ))
            )}
          </div>
        </>
      )}
    </div>
  );
}

function ScopeSelector({
  scope,
  onChange,
  showRawPackets = false,
}: {
  scope: Record<string, unknown>;
  onChange: (scope: Record<string, unknown>) => void;
  showRawPackets?: boolean;
}) {
  const [channels, setChannels] = useState<Channel[]>([]);
  const [contacts, setContacts] = useState<Contact[]>([]);

  useEffect(() => {
    api.getChannels().then(setChannels).catch(console.error);

    // Paginate to fetch all contacts (API caps at 1000 per request)
    (async () => {
      const all: Contact[] = [];
      const pageSize = 1000;
      let offset = 0;

      while (true) {
        const page = await api.getContacts(pageSize, offset);
        all.push(...page);
        if (page.length < pageSize) break;
        offset += pageSize;
      }
      setContacts(all);
    })().catch(console.error);
  }, []);

  const messages = scope.messages ?? 'all';
  const rawMode = getScopeMode(messages);
  // When raw packets aren't offered, "none" is not a valid choice — treat as "all"
  const mode = !showRawPackets && rawMode === 'none' ? 'all' : rawMode;
  const isListMode = mode === 'only' || mode === 'except';

  const selectedChannels: string[] =
    isListMode && typeof messages === 'object' && messages !== null
      ? getFilterKeys((messages as Record<string, unknown>).channels)
      : [];
  const selectedContacts: string[] =
    isListMode && typeof messages === 'object' && messages !== null
      ? getFilterKeys((messages as Record<string, unknown>).contacts)
      : [];

  /** Wrap channel/contact key lists in the right shape for the current mode */
  const buildMessages = (chKeys: string[], coKeys: string[]) => {
    if (mode === 'except') {
      return {
        channels: { except: chKeys },
        contacts: { except: coKeys },
      };
    }
    return { channels: chKeys, contacts: coKeys };
  };

  const handleModeChange = (newMode: ScopeMode) => {
    if (newMode === 'all' || newMode === 'none') {
      onChange({ ...scope, messages: newMode });
    } else if (newMode === 'only') {
      onChange({ ...scope, messages: { channels: [], contacts: [] } });
    } else {
      onChange({
        ...scope,
        messages: { channels: { except: [] }, contacts: { except: [] } },
      });
    }
  };

  const toggleChannel = (key: string) => {
    const current = [...selectedChannels];
    const idx = current.indexOf(key);
    if (idx >= 0) current.splice(idx, 1);
    else current.push(key);
    onChange({ ...scope, messages: buildMessages(current, selectedContacts) });
  };

  const toggleContact = (key: string) => {
    const current = [...selectedContacts];
    const idx = current.indexOf(key);
    if (idx >= 0) current.splice(idx, 1);
    else current.push(key);
    onChange({ ...scope, messages: buildMessages(selectedChannels, current) });
  };

  // Exclude repeaters (2), rooms (3), and sensors (4)
  const filteredContacts = contacts.filter((c) => c.type === 0 || c.type === 1);

  const modeDescriptions: Record<ScopeMode, string> = {
    all: 'All messages',
    none: 'No messages',
    only: 'Only listed channels/contacts',
    except: 'All except listed channels/contacts',
  };

  const rawEnabled = showRawPackets && scope.raw_packets === 'all';

  // Warn when the effective scope matches nothing
  const messagesEffectivelyNone =
    mode === 'none' ||
    (mode === 'only' && selectedChannels.length === 0 && selectedContacts.length === 0) ||
    (mode === 'except' &&
      channels.length > 0 &&
      filteredContacts.length > 0 &&
      selectedChannels.length >= channels.length &&
      selectedContacts.length >= filteredContacts.length);
  const showEmptyScopeWarning = messagesEffectivelyNone && !rawEnabled;

  const listHint =
    mode === 'only'
      ? 'Newly added channels or contacts will not be automatically included.'
      : 'Newly added channels or contacts will be automatically included unless excluded here.';

  const checkboxLabel = mode === 'except' ? 'exclude' : 'include';

  const messageModes: ScopeMode[] = showRawPackets
    ? ['all', 'none', 'only', 'except']
    : ['all', 'only', 'except'];

  return (
    <div className="space-y-3">
      <h3 className="text-base font-semibold tracking-tight">Message Scope</h3>

      {showRawPackets && (
        <label className="flex items-center gap-3 cursor-pointer">
          <input
            type="checkbox"
            checked={rawEnabled}
            onChange={(e) => onChange({ ...scope, raw_packets: e.target.checked ? 'all' : 'none' })}
            className="h-4 w-4 rounded border-border"
          />
          <span className="text-sm">Forward raw packets</span>
        </label>
      )}

      <div className="space-y-1">
        {messageModes.map((m) => (
          <label key={m} className="flex items-center gap-2 cursor-pointer">
            <input
              type="radio"
              name="scope-mode"
              checked={mode === m}
              onChange={() => handleModeChange(m)}
              className="h-4 w-4 accent-primary"
            />
            <span className="text-sm">{modeDescriptions[m]}</span>
          </label>
        ))}
      </div>

      {showEmptyScopeWarning && (
        <div className="rounded-md border border-warning/50 bg-warning/10 px-3 py-2 text-xs text-warning">
          Nothing is selected &mdash; this integration will not forward any data.
        </div>
      )}

      {isListMode && (
        <>
          <p className="text-[0.8125rem] text-muted-foreground">{listHint}</p>

          {channels.length > 0 && (
            <PillsSearchList
              label="Channels"
              labelSuffix={checkboxLabel}
              items={channels.map((ch) => ({ key: ch.key, label: ch.name }))}
              selectedKeys={selectedChannels}
              onToggle={toggleChannel}
              onAll={() =>
                onChange({
                  ...scope,
                  messages: buildMessages(
                    channels.map((ch) => ch.key),
                    selectedContacts
                  ),
                })
              }
              onNone={() => onChange({ ...scope, messages: buildMessages([], selectedContacts) })}
              searchPlaceholder={`Search ${channels.length} channel${channels.length === 1 ? '' : 's'}...`}
              emptyItemsMessage="No channels available."
            />
          )}

          {filteredContacts.length > 0 && (
            <PillsSearchList
              label="Contacts"
              labelSuffix={checkboxLabel}
              items={filteredContacts.map((c) => ({
                key: c.public_key,
                label: c.name || c.public_key.slice(0, 12),
                trailing: c.public_key.slice(0, 12),
              }))}
              selectedKeys={selectedContacts}
              onToggle={toggleContact}
              onAll={() =>
                onChange({
                  ...scope,
                  messages: buildMessages(
                    selectedChannels,
                    filteredContacts.map((c) => c.public_key)
                  ),
                })
              }
              onNone={() => onChange({ ...scope, messages: buildMessages(selectedChannels, []) })}
              searchPlaceholder={`Search ${filteredContacts.length} contact${filteredContacts.length === 1 ? '' : 's'}...`}
              emptyItemsMessage="No contacts available."
            />
          )}
        </>
      )}
    </div>
  );
}

const APPRISE_DEFAULT_DM = '**DM:** {sender_name}: {text} **via:** [{hops_backticked}]';
const APPRISE_DEFAULT_CHANNEL =
  '**{channel_name}:** {sender_name}: {text} **via:** [{hops_backticked}]';
const APPRISE_DEFAULT_DM_PLAIN = 'DM: {sender_name}: {text} via: [{hops}]';
const APPRISE_DEFAULT_CHANNEL_PLAIN = '{channel_name}: {sender_name}: {text} via: [{hops}]';

const APPRISE_SAMPLE_VARS: Record<string, string> = {
  type: 'CHAN',
  text: 'hello world',
  sender_name: 'Alice',
  sender_key: 'a1b2c3d4e5f6',
  channel_name: '#general',
  conversation_key: 'abcdef1234567890',
  hops: '2a, 3b',
  hops_backticked: '`2a`, `3b`',
  hop_count: '2',
  rssi: '-95',
  snr: '6.5',
};

const APPRISE_SAMPLE_VARS_DM: Record<string, string> = {
  ...APPRISE_SAMPLE_VARS,
  type: 'PRIV',
  channel_name: '',
  conversation_key: 'a1b2c3d4e5f6',
};

function appriseApplyFormat(fmt: string, vars: Record<string, string>): string {
  let result = fmt;
  for (const [key, value] of Object.entries(vars)) {
    result = result.split(`{${key}}`).join(value);
  }
  return result;
}

/** Render a markdown-ish string into inline React elements (bold, italic, code). */
function appriseRenderMarkdown(s: string): ReactNode[] {
  const nodes: ReactNode[] = [];
  let key = 0;
  // Split on **bold**, __bold__, *italic*, _italic_, and `code` spans.
  // Longer delimiters first so ** and __ match before * and _.
  const parts = s.split(/(\*\*[^*]+\*\*|__[^_]+__|`[^`]+`|\*[^*]+\*|_[^_]+_)/g);
  for (const part of parts) {
    if (
      (part.startsWith('**') && part.endsWith('**')) ||
      (part.startsWith('__') && part.endsWith('__'))
    ) {
      nodes.push(
        <strong key={key++} className="font-bold">
          {part.slice(2, -2)}
        </strong>
      );
    } else if (
      (part.startsWith('*') && part.endsWith('*')) ||
      (part.startsWith('_') && part.endsWith('_'))
    ) {
      nodes.push(
        <em key={key++} className="italic">
          {part.slice(1, -1)}
        </em>
      );
    } else if (part.startsWith('`') && part.endsWith('`')) {
      nodes.push(
        <code key={key++} className="rounded bg-muted px-1 py-0.5 text-[0.6875rem] font-mono">
          {part.slice(1, -1)}
        </code>
      );
    } else if (part) {
      nodes.push(<span key={key++}>{part}</span>);
    }
  }
  return nodes;
}

function AppriseFormatPreview({
  format,
  vars,
  markdown = true,
}: {
  format: string;
  vars: Record<string, string>;
  markdown?: boolean;
}) {
  const raw = appriseApplyFormat(format, vars);
  return (
    <div className="rounded-md border border-border bg-muted/30 p-2 space-y-1.5">
      {markdown && (
        <div>
          <span className="text-[0.625rem] uppercase tracking-wider text-muted-foreground font-medium">
            Rendered (Discord, Slack, Telegram)
          </span>
          <p className="text-xs break-all">{appriseRenderMarkdown(raw)}</p>
        </div>
      )}
      <div>
        <span className="text-[0.625rem] uppercase tracking-wider text-muted-foreground font-medium">
          {markdown ? 'Raw (email, SMS)' : 'Preview'}
        </span>
        <p className="text-xs font-mono break-all text-muted-foreground">{raw}</p>
      </div>
    </div>
  );
}

function appriseIsDefault(value: unknown, defaultStr: string): boolean {
  if (value == null) return true;
  const s = String(value).trim();
  return s === '' || s === defaultStr;
}

function AppriseConfigEditor({
  config,
  scope,
  onChange,
  onScopeChange,
}: {
  config: Record<string, unknown>;
  scope: Record<string, unknown>;
  onChange: (config: Record<string, unknown>) => void;
  onScopeChange: (scope: Record<string, unknown>) => void;
}) {
  const markdown = config.markdown_format !== false;
  const defaultDm = markdown ? APPRISE_DEFAULT_DM : APPRISE_DEFAULT_DM_PLAIN;
  const defaultChan = markdown ? APPRISE_DEFAULT_CHANNEL : APPRISE_DEFAULT_CHANNEL_PLAIN;
  const dmFormat = ((config.body_format_dm as string) || '').trim() || defaultDm;
  const chanFormat = ((config.body_format_channel as string) || '').trim() || defaultChan;

  return (
    <div className="space-y-3">
      <p className="text-[0.8125rem] text-muted-foreground">
        Send push notifications via{' '}
        <a
          href="https://github.com/caronc/apprise"
          target="_blank"
          rel="noopener noreferrer"
          className="underline hover:text-foreground"
        >
          Apprise
        </a>{' '}
        when messages are received. Supports Discord, Slack, Telegram, email, and{' '}
        <a
          href="https://github.com/caronc/apprise/wiki#supported-notifications"
          target="_blank"
          rel="noopener noreferrer"
          className="underline hover:text-foreground"
        >
          100+ other services
        </a>
        .
      </p>

      <div className="space-y-2">
        <Label htmlFor="fanout-apprise-urls">Notification URLs</Label>
        <textarea
          id="fanout-apprise-urls"
          className="w-full rounded-md border border-input bg-background px-3 py-2 text-sm font-mono min-h-[80px]"
          placeholder={
            'discord://webhook_id/token\nslack://token_a/token_b/token_c\ntgram://bot_token/chat_id'
          }
          value={(config.urls as string) || ''}
          onChange={(e) => onChange({ ...config, urls: e.target.value })}
          rows={4}
        />
        <p className="text-[0.8125rem] text-muted-foreground">
          One URL per line. All URLs receive every matched notification. For Matrix room version 12
          (servername-less room IDs), append <code>?hsreq=no</code> to the URL.
        </p>
      </div>

      <label className="flex items-center gap-3 cursor-pointer">
        <input
          type="checkbox"
          checked={config.preserve_identity !== false}
          onChange={(e) => onChange({ ...config, preserve_identity: e.target.checked })}
          className="h-4 w-4 rounded border-border"
        />
        <div>
          <span className="text-sm">Preserve identity on Discord</span>
          <p className="text-[0.8125rem] text-muted-foreground">
            When enabled, Discord webhooks will use their configured name/avatar instead of
            overriding with MeshCore sender info.
          </p>
        </div>
      </label>

      <label className="flex items-center gap-3 cursor-pointer">
        <input
          type="checkbox"
          checked={config.include_outgoing === true}
          onChange={(e) => onChange({ ...config, include_outgoing: e.target.checked })}
          className="h-4 w-4 rounded border-border"
        />
        <div>
          <span className="text-sm">Forward RemoteTerm-sent messages</span>
          <p className="text-[0.8125rem] text-muted-foreground">
            Include DMs and channel messages sent by this RemoteTerm instance, including manual
            sends and bot replies. Outgoing messages carry no routing path or signal data, so
            path-related format fields render as direct and RSSI/SNR are empty.
          </p>
        </div>
      </label>

      <Separator />

      <h3 className="text-base font-semibold tracking-tight">Message Format</h3>

      <label className="flex items-center gap-3 cursor-pointer">
        <input
          type="checkbox"
          checked={markdown}
          onChange={(e) => {
            const md = e.target.checked;
            const updates: Record<string, unknown> = { ...config, markdown_format: md };
            const curDm = ((config.body_format_dm as string) || '').trim();
            const curChan = ((config.body_format_channel as string) || '').trim();
            if (md) {
              if (!curDm || curDm === APPRISE_DEFAULT_DM_PLAIN)
                updates.body_format_dm = APPRISE_DEFAULT_DM;
              if (!curChan || curChan === APPRISE_DEFAULT_CHANNEL_PLAIN)
                updates.body_format_channel = APPRISE_DEFAULT_CHANNEL;
            } else {
              if (!curDm || curDm === APPRISE_DEFAULT_DM)
                updates.body_format_dm = APPRISE_DEFAULT_DM_PLAIN;
              if (!curChan || curChan === APPRISE_DEFAULT_CHANNEL)
                updates.body_format_channel = APPRISE_DEFAULT_CHANNEL_PLAIN;
            }
            onChange(updates);
          }}
          className="h-4 w-4 rounded border-border"
        />
        <div>
          <span className="text-sm">Markdown formatting</span>
          <p className="text-[0.8125rem] text-muted-foreground">
            If notifications fail on services like Telegram due to special characters in sender
            names, disable this option.
          </p>
        </div>
      </label>

      <details className="group">
        <summary className="text-sm font-medium text-foreground cursor-pointer select-none flex items-center gap-1">
          <ChevronDown className="h-3 w-3 transition-transform group-open:rotate-0 -rotate-90" />
          Available variables
        </summary>
        <div className="mt-2 rounded-md border border-border bg-muted/30 p-2 text-xs space-y-0.5">
          <div className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-0.5">
            <code className="text-[0.6875rem] font-mono bg-muted px-1 rounded">{'{text}'}</code>
            <span className="text-muted-foreground">Message body</span>
            <code className="text-[0.6875rem] font-mono bg-muted px-1 rounded">
              {'{sender_name}'}
            </code>
            <span className="text-muted-foreground">Sender display name</span>
            <code className="text-[0.6875rem] font-mono bg-muted px-1 rounded">
              {'{sender_key}'}
            </code>
            <span className="text-muted-foreground">Sender public key (hex)</span>
            <code className="text-[0.6875rem] font-mono bg-muted px-1 rounded">
              {'{channel_name}'}
            </code>
            <span className="text-muted-foreground">Channel name (channel messages only)</span>
            <code className="text-[0.6875rem] font-mono bg-muted px-1 rounded">
              {'{conversation_key}'}
            </code>
            <span className="text-muted-foreground">
              Contact pubkey (DM) or channel key (channel)
            </span>
            <code className="text-[0.6875rem] font-mono bg-muted px-1 rounded">{'{type}'}</code>
            <span className="text-muted-foreground">PRIV or CHAN</span>
            <code className="text-[0.6875rem] font-mono bg-muted px-1 rounded">{'{hops}'}</code>
            <span className="text-muted-foreground">
              Comma-separated hop IDs, or &quot;direct&quot;
            </span>
            <code className="text-[0.6875rem] font-mono bg-muted px-1 rounded">
              {'{hops_backticked}'}
            </code>
            <span className="text-muted-foreground">Hops wrapped in backticks for markdown</span>
            <code className="text-[0.6875rem] font-mono bg-muted px-1 rounded">
              {'{hop_count}'}
            </code>
            <span className="text-muted-foreground">Number of hops (0 for direct)</span>
            <code className="text-[0.6875rem] font-mono bg-muted px-1 rounded">{'{rssi}'}</code>
            <span className="text-muted-foreground">Last-hop RSSI in dBm</span>
            <code className="text-[0.6875rem] font-mono bg-muted px-1 rounded">{'{snr}'}</code>
            <span className="text-muted-foreground">Last-hop SNR in dB</span>
          </div>
          <p className="text-xs text-muted-foreground mt-1.5">
            Empty textareas use the default format. RSSI/SNR may be empty if unavailable.
          </p>
        </div>
      </details>

      <div className="space-y-2">
        <div className="flex items-center justify-between">
          <Label htmlFor="fanout-apprise-fmt-dm">DM format</Label>
          {!appriseIsDefault(config.body_format_dm, defaultDm) && (
            <button
              type="button"
              aria-label="Reset DM format to default"
              className="text-xs text-muted-foreground hover:text-foreground transition-colors"
              onClick={() => onChange({ ...config, body_format_dm: defaultDm })}
            >
              Reset to default
            </button>
          )}
        </div>
        <textarea
          id="fanout-apprise-fmt-dm"
          className="w-full rounded-md border border-input bg-background px-3 py-2 text-sm font-mono min-h-[56px]"
          placeholder={defaultDm}
          value={(config.body_format_dm as string) ?? ''}
          onChange={(e) => onChange({ ...config, body_format_dm: e.target.value })}
          rows={2}
        />
        <AppriseFormatPreview format={dmFormat} vars={APPRISE_SAMPLE_VARS_DM} markdown={markdown} />
      </div>

      <div className="space-y-2">
        <div className="flex items-center justify-between">
          <Label htmlFor="fanout-apprise-fmt-chan">Channel format</Label>
          {!appriseIsDefault(config.body_format_channel, defaultChan) && (
            <button
              type="button"
              aria-label="Reset channel format to default"
              className="text-xs text-muted-foreground hover:text-foreground transition-colors"
              onClick={() => onChange({ ...config, body_format_channel: defaultChan })}
            >
              Reset to default
            </button>
          )}
        </div>
        <textarea
          id="fanout-apprise-fmt-chan"
          className="w-full rounded-md border border-input bg-background px-3 py-2 text-sm font-mono min-h-[56px]"
          placeholder={defaultChan}
          value={(config.body_format_channel as string) ?? ''}
          onChange={(e) => onChange({ ...config, body_format_channel: e.target.value })}
          rows={2}
        />
        <AppriseFormatPreview format={chanFormat} vars={APPRISE_SAMPLE_VARS} markdown={markdown} />
      </div>

      <Separator />

      <ScopeSelector scope={scope} onChange={onScopeChange} />
    </div>
  );
}

function WebhookConfigEditor({
  config,
  scope,
  onChange,
  onScopeChange,
}: {
  config: Record<string, unknown>;
  scope: Record<string, unknown>;
  onChange: (config: Record<string, unknown>) => void;
  onScopeChange: (scope: Record<string, unknown>) => void;
}) {
  const headersStr = JSON.stringify(config.headers ?? {}, null, 2);
  const [headersText, setHeadersText] = useState(headersStr);
  const [headersError, setHeadersError] = useState<string | null>(null);

  const handleHeadersChange = (text: string) => {
    setHeadersText(text);
    try {
      const parsed = JSON.parse(text);
      if (typeof parsed !== 'object' || Array.isArray(parsed)) {
        setHeadersError('Must be a JSON object');
        return;
      }
      setHeadersError(null);
      onChange({ ...config, headers: parsed });
    } catch {
      setHeadersError('Invalid JSON');
    }
  };

  return (
    <div className="space-y-3">
      <p className="text-[0.8125rem] text-muted-foreground">
        Send message data as JSON to an HTTP endpoint when messages are received.
      </p>

      <div className="space-y-2">
        <Label htmlFor="fanout-webhook-url">URL</Label>
        <Input
          id="fanout-webhook-url"
          type="url"
          placeholder="https://example.com/webhook"
          value={(config.url as string) || ''}
          onChange={(e) => onChange({ ...config, url: e.target.value })}
        />
      </div>

      <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
        <div className="space-y-2">
          <Label htmlFor="fanout-webhook-method">HTTP Method</Label>
          <select
            id="fanout-webhook-method"
            value={(config.method as string) || 'POST'}
            onChange={(e) => onChange({ ...config, method: e.target.value })}
            className="w-full rounded-md border border-input bg-background px-3 py-2 text-sm"
          >
            <option value="POST">POST</option>
            <option value="PUT">PUT</option>
            <option value="PATCH">PATCH</option>
          </select>
        </div>
      </div>

      <Separator />

      <div className="space-y-3">
        <h3 className="text-base font-semibold tracking-tight">HMAC Signing</h3>
        <p className="text-[0.8125rem] text-muted-foreground">
          When a secret is set, each request includes an HMAC-SHA256 signature of the JSON body in
          the specified header (e.g. <code className="bg-muted px-1 rounded">sha256=ab12cd...</code>
          ).
        </p>
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
          <div className="space-y-2">
            <Label htmlFor="fanout-webhook-hmac-secret">HMAC Secret</Label>
            <Input
              id="fanout-webhook-hmac-secret"
              type="password"
              placeholder="Leave empty to disable signing"
              value={(config.hmac_secret as string) || ''}
              onChange={(e) => onChange({ ...config, hmac_secret: e.target.value })}
            />
          </div>
          <div className="space-y-2">
            <Label htmlFor="fanout-webhook-hmac-header">Signature Header Name</Label>
            <Input
              id="fanout-webhook-hmac-header"
              type="text"
              placeholder="X-Webhook-Signature"
              value={(config.hmac_header as string) || ''}
              onChange={(e) => onChange({ ...config, hmac_header: e.target.value })}
            />
          </div>
        </div>
      </div>

      <Separator />

      <div className="space-y-2">
        <Label htmlFor="fanout-webhook-headers">Extra Headers (JSON)</Label>
        <textarea
          id="fanout-webhook-headers"
          className="w-full rounded-md border border-input bg-background px-3 py-2 text-sm font-mono min-h-[60px]"
          value={headersText}
          onChange={(e) => handleHeadersChange(e.target.value)}
          placeholder='{"Authorization": "Bearer ..."}'
        />
        {headersError && <p className="text-xs text-destructive">{headersError}</p>}
      </div>

      <Separator />

      <ScopeSelector scope={scope} onChange={onScopeChange} />
    </div>
  );
}

function SqsConfigEditor({
  config,
  scope,
  onChange,
  onScopeChange,
}: {
  config: Record<string, unknown>;
  scope: Record<string, unknown>;
  onChange: (config: Record<string, unknown>) => void;
  onScopeChange: (scope: Record<string, unknown>) => void;
}) {
  return (
    <div className="space-y-3">
      <p className="text-[0.8125rem] text-muted-foreground">
        Send matched mesh events to an Amazon SQS queue for durable processing by workers, Lambdas,
        or downstream automation.
      </p>

      <div className="rounded-md border border-warning/50 bg-warning/10 px-3 py-2 text-xs text-warning">
        Outgoing messages and any selected raw packets will be delivered exactly as forwarded by the
        fanout scope, including decrypted/plaintext message content.
      </div>

      <div className="space-y-2">
        <Label htmlFor="fanout-sqs-queue-url">Queue URL</Label>
        <Input
          id="fanout-sqs-queue-url"
          type="url"
          placeholder="https://sqs.us-east-1.amazonaws.com/123456789012/mesh-events"
          value={(config.queue_url as string) || ''}
          onChange={(e) => onChange({ ...config, queue_url: e.target.value })}
        />
      </div>

      <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
        <div className="space-y-2">
          <Label htmlFor="fanout-sqs-region">Region (optional)</Label>
          <Input
            id="fanout-sqs-region"
            type="text"
            placeholder="us-east-1"
            value={(config.region_name as string) || ''}
            onChange={(e) => onChange({ ...config, region_name: e.target.value })}
          />
        </div>
        <div className="space-y-2">
          <Label htmlFor="fanout-sqs-endpoint">Endpoint URL (optional)</Label>
          <Input
            id="fanout-sqs-endpoint"
            type="url"
            placeholder="http://localhost:4566"
            value={(config.endpoint_url as string) || ''}
            onChange={(e) => onChange({ ...config, endpoint_url: e.target.value })}
          />
          <p className="text-[0.8125rem] text-muted-foreground">
            Useful for LocalStack or custom endpoints
          </p>
        </div>
      </div>

      <Separator />

      <div className="space-y-2">
        <h3 className="text-base font-semibold tracking-tight">Static Credentials (optional)</h3>
        <p className="text-[0.8125rem] text-muted-foreground">
          Leave blank to use the server&apos;s normal AWS credential chain.
        </p>
      </div>

      <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
        <div className="space-y-2">
          <Label htmlFor="fanout-sqs-access-key">Access Key ID</Label>
          <Input
            id="fanout-sqs-access-key"
            type="text"
            value={(config.access_key_id as string) || ''}
            onChange={(e) => onChange({ ...config, access_key_id: e.target.value })}
          />
        </div>
        <div className="space-y-2">
          <Label htmlFor="fanout-sqs-secret-key">Secret Access Key</Label>
          <Input
            id="fanout-sqs-secret-key"
            type="password"
            value={(config.secret_access_key as string) || ''}
            onChange={(e) => onChange({ ...config, secret_access_key: e.target.value })}
          />
        </div>
      </div>

      <div className="space-y-2">
        <Label htmlFor="fanout-sqs-session-token">Session Token (optional)</Label>
        <Input
          id="fanout-sqs-session-token"
          type="password"
          value={(config.session_token as string) || ''}
          onChange={(e) => onChange({ ...config, session_token: e.target.value })}
        />
      </div>

      <Separator />

      <ScopeSelector scope={scope} onChange={onScopeChange} showRawPackets />
    </div>
  );
}

export function SettingsFanoutSection({
  health,
  onHealthRefresh,
  className,
}: {
  health: HealthStatus | null;
  onHealthRefresh?: () => Promise<void>;
  className?: string;
}) {
  const [configs, setConfigs] = useState<FanoutConfig[]>([]);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [draftType, setDraftType] = useState<DraftType | null>(null);
  const [editConfig, setEditConfig] = useState<Record<string, unknown>>({});
  const [editScope, setEditScope] = useState<Record<string, unknown>>({});
  const [editName, setEditName] = useState('');
  const [inlineEditingId, setInlineEditingId] = useState<string | null>(null);
  const [inlineEditName, setInlineEditName] = useState('');
  const [createDialogOpen, setCreateDialogOpen] = useState(false);
  const [selectedCreateType, setSelectedCreateType] = useState<DraftType | null>(null);
  const [errorDialogState, setErrorDialogState] = useState<{
    integrationName: string;
    error: string;
  } | null>(null);
  const [busy, setBusy] = useState(false);

  const loadConfigs = useCallback(async () => {
    try {
      const data = await api.getFanoutConfigs();
      setConfigs(data);
    } catch (err) {
      console.error('Failed to load fanout configs:', err);
    }
  }, []);

  useEffect(() => {
    loadConfigs();
  }, [loadConfigs]);

  const availableCreateOptions = CREATE_INTEGRATION_DEFINITIONS;

  useEffect(() => {
    if (!createDialogOpen) return;
    if (availableCreateOptions.length === 0) {
      setSelectedCreateType(null);
      return;
    }
    if (
      selectedCreateType &&
      availableCreateOptions.some((option) => option.value === selectedCreateType)
    ) {
      return;
    }
    setSelectedCreateType(availableCreateOptions[0].value);
  }, [createDialogOpen, availableCreateOptions, selectedCreateType]);

  const handleToggleEnabled = async (cfg: FanoutConfig) => {
    try {
      await api.updateFanoutConfig(cfg.id, { enabled: !cfg.enabled });
      await loadConfigs();
      if (onHealthRefresh) await onHealthRefresh();
      toast.success(cfg.enabled ? 'Integration disabled' : 'Integration enabled');
    } catch (err) {
      toast.error(err instanceof Error ? err.message : 'Failed to update');
    }
  };

  const handleEdit = (cfg: FanoutConfig) => {
    setCreateDialogOpen(false);
    setInlineEditingId(null);
    setInlineEditName('');
    setDraftType(null);
    setEditingId(cfg.id);
    setEditConfig(cfg.config);
    setEditScope(cfg.scope);
    setEditName(cfg.name);
  };

  const handleStartInlineEdit = (cfg: FanoutConfig) => {
    setCreateDialogOpen(false);
    setInlineEditingId(cfg.id);
    setInlineEditName(cfg.name);
  };

  const handleCancelInlineEdit = () => {
    setInlineEditingId(null);
    setInlineEditName('');
  };

  const handleBackToList = () => {
    const shouldConfirm =
      draftType !== null ||
      fanoutDraftHasUnsavedChanges(
        editingId ? (configs.find((c) => c.id === editingId) ?? null) : null,
        {
          name: editName,
          config: editConfig,
          scope: editScope,
        }
      );
    if (shouldConfirm && !confirm('Leave without saving?')) return;
    setEditingId(null);
    setDraftType(null);
  };

  const handleInlineNameSave = async (cfg: FanoutConfig) => {
    const nextName = inlineEditName.trim();
    if (inlineEditingId !== cfg.id) return;
    if (!nextName) {
      toast.error('Name cannot be empty');
      handleCancelInlineEdit();
      return;
    }
    if (nextName === cfg.name) {
      handleCancelInlineEdit();
      return;
    }
    try {
      await api.updateFanoutConfig(cfg.id, { name: nextName });
      if (editingId === cfg.id) {
        setEditName(nextName);
      }
      await loadConfigs();
      toast.success('Name updated');
    } catch (err) {
      toast.error(err instanceof Error ? err.message : 'Failed to update name');
    } finally {
      handleCancelInlineEdit();
    }
  };

  const handleSave = async (enabled?: boolean) => {
    const currentDraftType = draftType;
    const currentEditingId = editingId;
    if (!currentEditingId && !currentDraftType) return;
    setBusy(true);
    try {
      if (currentDraftType) {
        const recipe = getCreateIntegrationDefinition(currentDraftType);
        await api.createFanoutConfig({
          type: recipe.savedType,
          name: normalizeDraftName(currentDraftType, editName.trim(), configs),
          config: normalizeDraftConfig(currentDraftType, editConfig),
          scope: normalizeDraftScope(currentDraftType, editScope),
          enabled: enabled ?? true,
        });
      } else {
        if (!currentEditingId) {
          throw new Error('Missing fanout config id for update');
        }
        const editingType = configs.find((cfg) => cfg.id === currentEditingId)?.type ?? '';
        const update: Record<string, unknown> = {
          name: editName,
          config: normalizeIntegrationConfigForSave(editingType, editConfig),
          scope: editScope,
        };
        if (enabled !== undefined) update.enabled = enabled;
        await api.updateFanoutConfig(currentEditingId, update);
      }
      setDraftType(null);
      setEditingId(null);
      await loadConfigs();
      if (onHealthRefresh) {
        try {
          await onHealthRefresh();
        } catch (err) {
          console.error('Failed to refresh health after saving fanout config:', err);
        }
      }
      toast.success(enabled ? 'Integration saved and enabled' : 'Integration saved');
    } catch (err) {
      toast.error(err instanceof Error ? err.message : 'Failed to save');
    } finally {
      setBusy(false);
    }
  };

  const handleDelete = async (id: string) => {
    const cfg = configs.find((c) => c.id === id);
    if (!confirm(`Delete "${cfg?.name}"? This cannot be undone.`)) return;
    try {
      await api.deleteFanoutConfig(id);
      if (editingId === id) setEditingId(null);
      await loadConfigs();
      if (onHealthRefresh) await onHealthRefresh();
      toast.success('Integration deleted');
    } catch (err) {
      toast.error(err instanceof Error ? err.message : 'Failed to delete');
    }
  };

  const handleAddCreate = (type: DraftType) => {
    const definition = getCreateIntegrationDefinition(type);
    const defaults = cloneDraftDefaults(type);
    setCreateDialogOpen(false);
    setEditingId(null);
    setDraftType(type);
    setEditName(
      definition.nameMode === 'fixed'
        ? definition.defaultName
        : getDefaultIntegrationName(definition.savedType, configs)
    );
    setEditConfig(defaults.config);
    setEditScope(defaults.scope);
  };

  const editingConfig = editingId ? configs.find((c) => c.id === editingId) : null;
  const detailType = draftType ?? editingConfig?.type ?? null;
  const isDraft = draftType !== null;
  const configGroups = Object.entries(TYPE_LABELS)
    .map(([type, label]) => ({
      type,
      label,
      configs: configs
        .filter((cfg) => cfg.type === type)
        .sort((a, b) => a.name.localeCompare(b.name, undefined, { sensitivity: 'base' })),
    }))
    .filter((group) => group.configs.length > 0);

  // Detail view
  if (detailType) {
    return (
      <div className={cn('mx-auto w-full max-w-[800px] space-y-4', className)}>
        <button
          type="button"
          className="inline-flex items-center rounded-md border border-warning/50 bg-warning/10 px-3 py-2 text-sm text-warning transition-colors hover:bg-warning/20"
          onClick={handleBackToList}
        >
          &larr; Back to list
        </button>

        <div className="space-y-2">
          <Label htmlFor="fanout-edit-name">Name</Label>
          <Input
            id="fanout-edit-name"
            type="text"
            value={editName}
            onChange={(e) => setEditName(e.target.value)}
          />
        </div>

        <div className="text-xs text-muted-foreground">Type: {getDetailTypeLabel(detailType)}</div>

        <Separator />

        {detailType === 'mqtt_private' && (
          <MqttPrivateConfigEditor
            config={editConfig}
            scope={editScope}
            onChange={setEditConfig}
            onScopeChange={setEditScope}
          />
        )}

        {detailType === 'mqtt_ha' && (
          <MqttHaConfigEditor
            config={editConfig}
            scope={editScope}
            onChange={setEditConfig}
            onScopeChange={setEditScope}
          />
        )}

        {detailType === 'mqtt_community' && (
          <MqttCommunityConfigEditor config={editConfig} onChange={setEditConfig} />
        )}

        {detailType === 'mqtt_community_meshrank' && (
          <MeshRankConfigEditor config={editConfig} onChange={setEditConfig} />
        )}

        {detailType === 'mqtt_community_letsmesh_us' && (
          <LetsMeshConfigEditor
            config={editConfig}
            onChange={setEditConfig}
            brokerHost={DEFAULT_COMMUNITY_BROKER_HOST}
          />
        )}

        {detailType === 'mqtt_community_letsmesh_eu' && (
          <LetsMeshConfigEditor
            config={editConfig}
            onChange={setEditConfig}
            brokerHost={DEFAULT_COMMUNITY_BROKER_HOST_EU}
          />
        )}

        {detailType === 'discord_bridge' && (
          <DiscordBridgeConfigEditor config={editConfig} onChange={setEditConfig} />
        )}

        {detailType === 'telegram_bridge' && (
          <TelegramBridgeConfigEditor config={editConfig} onChange={setEditConfig} />
        )}

        {detailType === 'apprise' && (
          <AppriseConfigEditor
            config={editConfig}
            scope={editScope}
            onChange={setEditConfig}
            onScopeChange={setEditScope}
          />
        )}

        {detailType === 'webhook' && (
          <WebhookConfigEditor
            config={editConfig}
            scope={editScope}
            onChange={setEditConfig}
            onScopeChange={setEditScope}
          />
        )}

        {detailType === 'sqs' && (
          <SqsConfigEditor
            config={editConfig}
            scope={editScope}
            onChange={setEditConfig}
            onScopeChange={setEditScope}
          />
        )}

        {detailType === 'map_upload' && (
          <MapUploadConfigEditor config={editConfig} onChange={setEditConfig} />
        )}

        <Separator />

        <div className="flex gap-2">
          <Button
            onClick={() => handleSave(true)}
            disabled={busy}
            className="flex-1 bg-status-connected hover:bg-status-connected/90 text-primary-foreground"
          >
            {busy ? 'Saving...' : 'Save as Enabled'}
          </Button>
          <Button
            variant="secondary"
            onClick={() => handleSave(false)}
            disabled={busy}
            className="flex-1"
          >
            {busy ? 'Saving...' : 'Save as Disabled'}
          </Button>
          {!isDraft && editingConfig && (
            <Button variant="destructive" onClick={() => handleDelete(editingConfig.id)}>
              Delete
            </Button>
          )}
        </div>
      </div>
    );
  }

  // List view
  return (
    <div className={cn('mx-auto w-full max-w-[800px] space-y-4', className)}>
      <div className="rounded-lg border border-primary/20 bg-primary/5 p-4 text-[0.8125rem] leading-relaxed">
        <span className="font-semibold">Python bots have moved.</span>{' '}
        <span className="text-muted-foreground">
          Message bots, cron jobs, feeds and logs now live in the Bots workspace (sidebar › Tools ›
          Bots). Existing bots were migrated automatically. This page keeps pure forwarding
          integrations.
        </span>
      </div>

      <div className="rounded-md border border-warning/50 bg-warning/10 px-4 py-3 text-sm text-warning">
        Integrations are an experimental feature in open beta, and allow you to fanout raw and
        decrypted messages across multiple services for automation, analysis, or archiving.
      </div>

      <div className="flex flex-wrap gap-2">
        <Button type="button" size="sm" onClick={() => setCreateDialogOpen(true)}>
          Add Integration
        </Button>
      </div>

      <CreateIntegrationDialog
        open={createDialogOpen}
        options={availableCreateOptions}
        selectedType={selectedCreateType}
        onOpenChange={setCreateDialogOpen}
        onSelect={setSelectedCreateType}
        onCreate={() => {
          if (selectedCreateType) {
            handleAddCreate(selectedCreateType);
          }
        }}
      />

      <Dialog
        open={errorDialogState !== null}
        onOpenChange={(open) => {
          if (!open) {
            setErrorDialogState(null);
          }
        }}
      >
        <DialogContent className="sm:max-w-md">
          <DialogHeader className="border-b border-border px-5 py-4">
            <DialogTitle>
              {errorDialogState ? `${errorDialogState.integrationName} Error` : 'Integration Error'}
            </DialogTitle>
            <DialogDescription>
              Most recent backend error retained for this integration.
            </DialogDescription>
          </DialogHeader>
          <div className="px-5 py-4 text-sm text-muted-foreground">
            <p className="whitespace-pre-wrap break-words font-mono text-foreground">
              {errorDialogState?.error}
            </p>
          </div>
        </DialogContent>
      </Dialog>

      {configGroups.length > 0 && (
        <div className="columns-1 gap-4 md:columns-2">
          {configGroups.map((group) => (
            <section
              key={group.type}
              className="mb-4 inline-block w-full break-inside-avoid space-y-2"
              aria-label={`${group.label} integrations`}
            >
              <div className="px-1 text-sm font-medium text-muted-foreground">{group.label}</div>
              <div className="space-y-2">
                {group.configs.map((cfg) => {
                  const statusEntry = health?.fanout_statuses?.[cfg.id];
                  const status = cfg.enabled ? statusEntry?.status : undefined;
                  const lastError = cfg.enabled ? statusEntry?.last_error : null;
                  const communityConfig = cfg.config as Record<string, unknown>;
                  return (
                    <div
                      key={cfg.id}
                      role="group"
                      aria-label={`Integration ${cfg.name}`}
                      className="border border-input rounded-md overflow-hidden"
                    >
                      <div className="flex items-center gap-2 px-3 py-2 bg-muted/50">
                        <label
                          className="flex items-center cursor-pointer"
                          onClick={(e) => e.stopPropagation()}
                        >
                          <input
                            type="checkbox"
                            checked={cfg.enabled}
                            onChange={() => handleToggleEnabled(cfg)}
                            className="w-4 h-4 rounded border-input accent-primary"
                            aria-label={`Enable ${cfg.name}`}
                          />
                        </label>

                        <div className="flex-1 min-w-0">
                          {inlineEditingId === cfg.id ? (
                            <Input
                              value={inlineEditName}
                              autoFocus
                              onChange={(e) => setInlineEditName(e.target.value)}
                              onFocus={(e) => e.currentTarget.select()}
                              onBlur={() => void handleInlineNameSave(cfg)}
                              onKeyDown={(e) => {
                                if (e.key === 'Enter') {
                                  e.preventDefault();
                                  e.currentTarget.blur();
                                }
                                if (e.key === 'Escape') {
                                  e.preventDefault();
                                  handleCancelInlineEdit();
                                }
                              }}
                              aria-label={`Edit name for ${cfg.name}`}
                              className="h-8"
                            />
                          ) : (
                            <button
                              type="button"
                              className="block max-w-full cursor-text truncate text-left text-sm font-medium hover:text-foreground/80"
                              onClick={() => handleStartInlineEdit(cfg)}
                            >
                              {cfg.name}
                            </button>
                          )}
                        </div>

                        <div
                          className={cn(
                            'w-2 h-2 rounded-full transition-colors',
                            getStatusColor(status, cfg.enabled)
                          )}
                          title={cfg.enabled ? getStatusLabel(status, cfg.type) : 'Disabled'}
                          aria-hidden="true"
                        />
                        <span className="text-xs text-muted-foreground hidden sm:inline">
                          {cfg.enabled ? getStatusLabel(status, cfg.type) : 'Disabled'}
                        </span>

                        {lastError && (
                          <Button
                            type="button"
                            variant="ghost"
                            size="sm"
                            className="h-6 w-6 px-0"
                            onClick={() =>
                              setErrorDialogState({
                                integrationName: cfg.name,
                                error: lastError,
                              })
                            }
                            aria-label={`View error details for ${cfg.name}`}
                            title="View latest error"
                          >
                            <Info className="h-3.5 w-3.5" aria-hidden="true" />
                          </Button>
                        )}

                        <Button
                          type="button"
                          variant="ghost"
                          size="sm"
                          className="h-6 px-2 text-xs"
                          onClick={() => handleEdit(cfg)}
                        >
                          Edit
                        </Button>
                      </div>

                      {cfg.type === 'mqtt_community' && (
                        <div className="space-y-1 border-t border-input px-3 py-2 text-xs text-muted-foreground">
                          <div>
                            Broker:{' '}
                            {formatBrokerSummary(communityConfig, {
                              host: DEFAULT_COMMUNITY_BROKER_HOST,
                              port: DEFAULT_COMMUNITY_BROKER_PORT,
                            })}
                          </div>
                          <div className="break-all">
                            Topic:{' '}
                            <code>
                              {(communityConfig.topic_template as string) ||
                                DEFAULT_COMMUNITY_PACKET_TOPIC_TEMPLATE}
                            </code>
                          </div>
                        </div>
                      )}

                      {cfg.type === 'mqtt_private' && (
                        <div className="space-y-1 border-t border-input px-3 py-2 text-xs text-muted-foreground">
                          <div>
                            Broker:{' '}
                            {formatBrokerSummary(cfg.config as Record<string, unknown>, {
                              host: '',
                              port: 1883,
                            })}
                          </div>
                          <div className="break-all">
                            Topics:{' '}
                            <code>
                              {formatPrivateTopicSummary(cfg.config as Record<string, unknown>)}
                            </code>
                          </div>
                        </div>
                      )}

                      {cfg.type === 'webhook' && (
                        <div className="space-y-1 border-t border-input px-3 py-2 text-xs text-muted-foreground">
                          <div className="break-all">
                            URL:{' '}
                            <code>
                              {((cfg.config as Record<string, unknown>).url as string) || 'Not set'}
                            </code>
                          </div>
                        </div>
                      )}

                      {cfg.type === 'apprise' && (
                        <div className="space-y-1 border-t border-input px-3 py-2 text-xs text-muted-foreground">
                          <div className="break-all">
                            Targets:{' '}
                            <code>
                              {formatAppriseTargets(
                                (cfg.config as Record<string, unknown>).urls as string | undefined
                              )}
                            </code>
                          </div>
                        </div>
                      )}

                      {cfg.type === 'sqs' && (
                        <div className="space-y-1 border-t border-input px-3 py-2 text-xs text-muted-foreground">
                          <div className="break-all">
                            Queue:{' '}
                            <code>
                              {formatSqsQueueSummary(cfg.config as Record<string, unknown>)}
                            </code>
                          </div>
                        </div>
                      )}
                    </div>
                  );
                })}
              </div>
            </section>
          ))}
        </div>
      )}
    </div>
  );
}
