import {
  Database,
  Info,
  MonitorCog,
  RadioTower,
  Share2,
  SlidersHorizontal,
  type LucideIcon,
} from 'lucide-react';

export type SettingsSection = 'radio' | 'local' | 'radio-app' | 'database' | 'fanout' | 'about';

export const SETTINGS_SECTION_ORDER: SettingsSection[] = [
  'radio',
  'local',
  'fanout',
  'radio-app',
  'database',
  'about',
];

export const SETTINGS_SECTION_LABELS: Record<SettingsSection, string> = {
  radio: 'Radio',
  local: 'Local Configuration',
  'radio-app': 'Radio-App Management',
  database: 'Database',
  fanout: 'Integrations',
  about: 'About',
};

export const SETTINGS_SECTION_ICONS: Record<SettingsSection, LucideIcon> = {
  radio: RadioTower,
  local: MonitorCog,
  'radio-app': SlidersHorizontal,
  database: Database,
  fanout: Share2,
  about: Info,
};
