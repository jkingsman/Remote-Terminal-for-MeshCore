import {
  Database,
  Info,
  MonitorCog,
  RadioTower,
  Share2,
  ShieldCheck,
  SlidersHorizontal,
  type LucideIcon,
} from 'lucide-react';

export type SettingsSection =
  | 'radio'
  | 'local'
  | 'https'
  | 'radio-app'
  | 'database'
  | 'fanout'
  | 'about';

export const SETTINGS_SECTION_ORDER: SettingsSection[] = [
  'radio',
  'local',
  'https',
  'fanout',
  'radio-app',
  'database',
  'about',
];

export const SETTINGS_SECTION_LABELS: Record<SettingsSection, string> = {
  radio: 'Radio',
  local: 'Local Configuration',
  https: 'HTTPS / TLS',
  'radio-app': 'Radio-App Management',
  database: 'Database',
  fanout: 'Integrations',
  about: 'About',
};

export const SETTINGS_SECTION_ICONS: Record<SettingsSection, LucideIcon> = {
  radio: RadioTower,
  local: MonitorCog,
  https: ShieldCheck,
  'radio-app': SlidersHorizontal,
  database: Database,
  fanout: Share2,
  about: Info,
};
