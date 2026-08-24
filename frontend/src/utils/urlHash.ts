import type { Channel, Contact, Conversation } from '../types';
import { findPublicChannel, PUBLIC_CHANNEL_NAME } from './publicChannel';
import { getContactDisplayName } from './pubkey';
import type { SettingsSection } from '../components/settings/settingsConstants';

interface ParsedHashConversation {
  type:
    | 'channel'
    | 'contact'
    | 'raw'
    | 'map'
    | 'visualizer'
    | 'search'
    | 'trace'
    | 'bots'
    | 'statistics';
  /** Conversation identity token (channel key or contact public key, or legacy name token) */
  name: string;
  /** Optional human-readable label segment (ignored for identity resolution) */
  label?: string;
  /** For map view: public key prefix to focus on */
  mapFocusKey?: string;
  /** For bots view: bot id to open in the editor */
  botId?: string;
}

const SETTINGS_SECTIONS: SettingsSection[] = [
  'radio',
  'local',
  'https',
  'radio-app',
  'fanout',
  'database',
  'about',
];

// Parse URL hash to get conversation
// (e.g., #channel/ABCDEF0123456789ABCDEF0123456789 or #contact/<64-char-pubkey>).
export function parseHashConversation(): ParsedHashConversation | null {
  const hash = window.location.hash.slice(1); // Remove leading #
  if (!hash) return null;

  if (hash === 'raw') {
    return { type: 'raw', name: 'raw' };
  }

  if (hash === 'map') {
    return { type: 'map', name: 'map' };
  }

  if (hash === 'visualizer') {
    return { type: 'visualizer', name: 'visualizer' };
  }

  if (hash === 'search') {
    return { type: 'search', name: 'search' };
  }

  if (hash === 'trace') {
    return { type: 'trace', name: 'trace' };
  }

  if (hash === 'bots') {
    return { type: 'bots', name: 'bots' };
  }

  // Statistics lived under Settings before becoming a sidebar tool; keep the
  // old settings hash working as a redirect.
  if (hash === 'statistics' || hash === 'settings/statistics') {
    return { type: 'statistics', name: 'statistics' };
  }

  // Bots editor deep link: #bots/{botId}
  if (hash.startsWith('bots/')) {
    const botId = decodeURIComponent(hash.slice('bots/'.length));
    return { type: 'bots', name: 'bots', ...(botId ? { botId } : {}) };
  }

  // Check for map with focus: #map/focus/{pubkey_prefix}
  if (hash.startsWith('map/focus/')) {
    const focusKey = hash.slice('map/focus/'.length);
    if (focusKey) {
      return { type: 'map', name: 'map', mapFocusKey: decodeURIComponent(focusKey) };
    }
    return { type: 'map', name: 'map' };
  }

  const slashIndex = hash.indexOf('/');
  if (slashIndex === -1) return null;

  const type = hash.slice(0, slashIndex);
  const value = hash.slice(slashIndex + 1);
  if (!(type === 'channel' || type === 'contact') || !value) {
    return null;
  }

  // Support both:
  // - Legacy: #channel/Public
  // - Stable: #channel/<id>
  // - Stable + readable: #channel/<id>/<display-name>
  const valueSlashIndex = value.indexOf('/');
  const tokenRaw = valueSlashIndex === -1 ? value : value.slice(0, valueSlashIndex);
  const labelRaw = valueSlashIndex === -1 ? '' : value.slice(valueSlashIndex + 1);

  const token = decodeURIComponent(tokenRaw);
  if (!token) return null;

  return {
    type,
    name: token,
    ...(labelRaw ? { label: decodeURIComponent(labelRaw) } : {}),
  };
}

export function parseHashSettingsSection(): SettingsSection | null {
  const hash = window.location.hash.slice(1);
  if (!hash.startsWith('settings/')) {
    return null;
  }

  const section = decodeURIComponent(hash.slice('settings/'.length)) as SettingsSection;
  return SETTINGS_SECTIONS.includes(section) ? section : null;
}

export function getSettingsHash(section: SettingsSection): string {
  return `#settings/${encodeURIComponent(section)}`;
}

export function resolveChannelFromHashToken(token: string, channels: Channel[]): Channel | null {
  const normalizedToken = token.trim();
  if (!normalizedToken) return null;

  // Preferred path: stable identity by channel key.
  const byKey = channels.find((c) => c.key.toLowerCase() === normalizedToken.toLowerCase());
  if (byKey) return byKey;

  // Legacy Public hashes should resolve to the canonical Public key, not any
  // arbitrary row that happens to share the display name.
  if (normalizedToken.toLowerCase() === PUBLIC_CHANNEL_NAME.toLowerCase()) {
    const publicChannel = findPublicChannel(channels);
    if (publicChannel) return publicChannel;
  }

  // Backward compatibility for legacy name-based hashes.
  return (
    channels.find((c) => c.name === normalizedToken || c.name === `#${normalizedToken}`) || null
  );
}

export function resolveContactFromHashToken(token: string, contacts: Contact[]): Contact | null {
  const normalizedToken = token.trim();
  if (!normalizedToken) return null;
  const lowerToken = normalizedToken.toLowerCase();

  // Preferred path: stable identity by full public key.
  const byKey = contacts.find((c) => c.public_key.toLowerCase() === lowerToken);
  if (byKey) return byKey;

  // Backward compatibility for legacy name/prefix-based hashes.
  return (
    contacts.find(
      (c) =>
        getContactDisplayName(c.name, c.public_key, c.last_advert) === normalizedToken ||
        c.public_key.toLowerCase().startsWith(lowerToken)
    ) || null
  );
}

/**
 * Generate a URL hash for focusing on a contact in the map view
 * @param publicKeyPrefix - The public key or prefix to focus on
 */
export function getMapFocusHash(publicKeyPrefix: string): string {
  return `#map/focus/${encodeURIComponent(publicKeyPrefix)}`;
}

// Generate URL hash from conversation
export function getConversationHash(conv: Conversation | null): string {
  if (!conv) return '';
  if (conv.type === 'raw') return '#raw';
  if (conv.type === 'map') return '#map';
  if (conv.type === 'visualizer') return '#visualizer';
  if (conv.type === 'search') return '#search';
  if (conv.type === 'trace') return '#trace';
  if (conv.type === 'statistics') return '#statistics';
  if (conv.type === 'bots') {
    return conv.botId ? `#bots/${encodeURIComponent(conv.botId)}` : '#bots';
  }

  // Use immutable IDs for identity, append readable label for UX.
  if (conv.type === 'channel') {
    const label = conv.name.startsWith('#') ? conv.name.slice(1) : conv.name;
    return `#channel/${encodeURIComponent(conv.id)}/${encodeURIComponent(label)}`;
  }
  return `#contact/${encodeURIComponent(conv.id)}/${encodeURIComponent(conv.name)}`;
}

// Update URL hash without adding to history
export function updateUrlHash(conv: Conversation | null): void {
  const newHash = getConversationHash(conv);
  if (newHash !== window.location.hash) {
    window.history.replaceState(null, '', newHash || window.location.pathname);
  }
}

// Update URL hash and add a new browser history entry
export function pushUrlHash(conv: Conversation | null): void {
  const newHash = getConversationHash(conv);
  if (newHash !== window.location.hash) {
    window.history.pushState(null, '', newHash || window.location.pathname);
  }
}

export function updateSettingsHash(section: SettingsSection): void {
  const newHash = getSettingsHash(section);
  if (newHash !== window.location.hash) {
    window.history.replaceState(null, '', newHash);
  }
}

// Push a settings hash as a new browser history entry
export function pushSettingsHash(section: SettingsSection): void {
  const newHash = getSettingsHash(section);
  if (newHash !== window.location.hash) {
    window.history.pushState(null, '', newHash);
  }
}
