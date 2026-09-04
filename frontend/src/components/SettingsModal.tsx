import { useState, useEffect, type ReactNode } from 'react';
import type {
  AppSettings,
  AppSettingsUpdate,
  Channel,
  Contact,
  HealthStatus,
  RadioAdvertMode,
  RadioConfig,
  RadioConfigUpdate,
  RadioDiscoveryResponse,
  RadioDiscoveryTarget,
  RadioRegionDiscoveryResponse,
} from '../types';
import type { LocalLabel } from '../utils/localLabel';
import {
  SETTINGS_SECTION_ICONS,
  SETTINGS_SECTION_LABELS,
  type SettingsSection,
} from './settings/settingsConstants';

import { SettingsRadioSection } from './settings/SettingsRadioSection';
import { SettingsLocalSection } from './settings/SettingsLocalSection';
import { SettingsRadioAppSection } from './settings/SettingsRadioAppSection';
import { SettingsFanoutSection } from './settings/SettingsFanoutSection';
import { SettingsDatabaseSection } from './settings/SettingsDatabaseSection';
import { SettingsStatisticsSection } from './settings/SettingsStatisticsSection';
import { SettingsAboutSection } from './settings/SettingsAboutSection';

interface SettingsModalBaseProps {
  open: boolean;
  pageMode?: boolean;
  config: RadioConfig | null;
  health: HealthStatus | null;
  appSettings: AppSettings | null;
  onClose: () => void;
  onSave: (update: RadioConfigUpdate) => Promise<void>;
  onSaveAppSettings: (update: AppSettingsUpdate) => Promise<void>;
  onSetPrivateKey: (key: string) => Promise<void>;
  onReboot: () => Promise<void>;
  onDisconnect: () => Promise<void>;
  onReconnect: () => Promise<void>;
  onAdvertise: (mode: RadioAdvertMode) => Promise<void>;
  meshDiscovery: RadioDiscoveryResponse | null;
  meshDiscoveryLoadingTarget: RadioDiscoveryTarget | null;
  onDiscoverMesh: (target: RadioDiscoveryTarget) => Promise<void>;
  regionDiscovery: RadioRegionDiscoveryResponse | null;
  regionDiscoveryLoading: boolean;
  onDiscoverRegions: (publicKeys?: string[]) => Promise<void>;
  onHealthRefresh: () => Promise<void>;
  onRefreshAppSettings: () => Promise<void>;
  onLocalLabelChange?: (label: LocalLabel) => void;
  blockedKeys?: string[];
  blockedNames?: string[];
  onToggleBlockedKey?: (key: string) => void;
  onToggleBlockedName?: (name: string) => void;
  contacts?: Contact[];
  channels?: Channel[];
  onBulkDeleteContacts?: (deletedKeys: string[]) => void;
  trackedTelemetryRepeaters?: string[];
  onToggleTrackedTelemetry?: (publicKey: string) => Promise<void>;
  trackedTelemetryContacts?: string[];
  onToggleTrackedTelemetryContact?: (publicKey: string) => Promise<void>;
}

export type SettingsModalProps = SettingsModalBaseProps &
  (
    | { externalSidebarNav: true; desktopSection: SettingsSection }
    | { externalSidebarNav?: false; desktopSection?: never }
  );

export function SettingsModal(props: SettingsModalProps) {
  const {
    open,
    pageMode = false,
    config,
    health,
    appSettings,
    onClose,
    onSave,
    onSaveAppSettings,
    onSetPrivateKey,
    onReboot,
    onDisconnect,
    onReconnect,
    onAdvertise,
    meshDiscovery,
    meshDiscoveryLoadingTarget,
    onDiscoverMesh,
    regionDiscovery,
    regionDiscoveryLoading,
    onDiscoverRegions,
    onHealthRefresh,
    onRefreshAppSettings,
    onLocalLabelChange,
    blockedKeys,
    blockedNames,
    onToggleBlockedKey,
    onToggleBlockedName,
    contacts,
    channels,
    onBulkDeleteContacts,
    trackedTelemetryRepeaters,
    onToggleTrackedTelemetry,
    trackedTelemetryContacts,
    onToggleTrackedTelemetryContact,
  } = props;
  const externalSidebarNav = props.externalSidebarNav === true;
  const desktopSection = props.externalSidebarNav ? props.desktopSection : undefined;

  const getIsMobileLayout = () => {
    if (typeof window === 'undefined' || typeof window.matchMedia !== 'function') return false;
    return window.matchMedia('(max-width: 767px)').matches;
  };

  const [isMobileLayout, setIsMobileLayout] = useState(getIsMobileLayout);
  const externalDesktopSidebarMode = externalSidebarNav && !isMobileLayout;
  const [expandedSections, setExpandedSections] = useState<Record<SettingsSection, boolean>>({
    radio: false,
    local: false,
    'radio-app': false,
    fanout: false,
    database: false,
    statistics: false,
    about: false,
  });

  // Refresh settings from server when modal opens
  useEffect(() => {
    if (open || pageMode) {
      onRefreshAppSettings();
    }
  }, [open, pageMode, onRefreshAppSettings]);

  useEffect(() => {
    if (typeof window === 'undefined' || typeof window.matchMedia !== 'function') return;

    const query = window.matchMedia('(max-width: 767px)');
    const onChange = (event: MediaQueryListEvent) => {
      setIsMobileLayout(event.matches);
    };

    setIsMobileLayout(query.matches);

    if (typeof query.addEventListener === 'function') {
      query.addEventListener('change', onChange);
      return () => query.removeEventListener('change', onChange);
    }

    query.addListener(onChange);
    return () => query.removeListener(onChange);
  }, []);

  const toggleSection = (section: SettingsSection) => {
    setExpandedSections((prev) => ({
      ...prev,
      [section]: !prev[section],
    }));
  };

  const isSectionVisible = (section: SettingsSection) =>
    externalDesktopSidebarMode ? desktopSection === section : expandedSections[section];

  const showSectionButton = !externalDesktopSidebarMode;
  const shouldRenderSection = (section: SettingsSection) =>
    !externalDesktopSidebarMode || desktopSection === section;

  const sectionWrapperClass = '';

  const sectionContentClass = externalDesktopSidebarMode
    ? 'mx-auto w-full max-w-[800px] space-y-4 p-4'
    : 'mx-auto w-full max-w-[800px] space-y-4 border-t border-input p-4';

  const settingsContainerClass = externalDesktopSidebarMode
    ? 'w-full h-full min-w-0 overflow-x-hidden overflow-y-auto [contain:layout_paint]'
    : 'w-full h-full min-w-0 overflow-x-hidden overflow-y-auto space-y-3 [contain:layout_paint]';

  const sectionButtonClasses =
    'w-full flex items-center justify-between px-4 py-3 text-left hover:bg-muted/40 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-inset';

  const renderSectionHeader = (section: SettingsSection): ReactNode => {
    if (!showSectionButton) return null;
    const Icon = SETTINGS_SECTION_ICONS[section];
    return (
      <button
        type="button"
        className={sectionButtonClasses}
        aria-expanded={expandedSections[section]}
        onClick={() => toggleSection(section)}
      >
        <span className="inline-flex items-center gap-2 font-medium" role="heading" aria-level={3}>
          <Icon className="h-4 w-4 text-muted-foreground" aria-hidden="true" />
          <span>{SETTINGS_SECTION_LABELS[section]}</span>
        </span>
        <span className="text-muted-foreground md:hidden" aria-hidden="true">
          {expandedSections[section] ? '−' : '+'}
        </span>
      </button>
    );
  };

  if (!pageMode && !open) {
    return null;
  }

  return (
    <div className={settingsContainerClass}>
      {shouldRenderSection('radio') && (
        <section className={sectionWrapperClass}>
          {renderSectionHeader('radio')}
          {isSectionVisible('radio') &&
            (config && appSettings ? (
              <SettingsRadioSection
                config={config}
                health={health}
                appSettings={appSettings}
                pageMode={pageMode}
                onSave={onSave}
                onSaveAppSettings={onSaveAppSettings}
                onSetPrivateKey={onSetPrivateKey}
                onReboot={onReboot}
                onDisconnect={onDisconnect}
                onReconnect={onReconnect}
                onAdvertise={onAdvertise}
                meshDiscovery={meshDiscovery}
                meshDiscoveryLoadingTarget={meshDiscoveryLoadingTarget}
                onDiscoverMesh={onDiscoverMesh}
                regionDiscovery={regionDiscovery}
                regionDiscoveryLoading={regionDiscoveryLoading}
                onDiscoverRegions={onDiscoverRegions}
                onClose={onClose}
                className={sectionContentClass}
              />
            ) : (
              <div className={sectionContentClass}>
                <div className="flex items-center justify-center py-12 text-sm text-muted-foreground">
                  Radio is not available.
                </div>
              </div>
            ))}
        </section>
      )}

      {shouldRenderSection('local') && (
        <section className={sectionWrapperClass}>
          {renderSectionHeader('local')}
          {isSectionVisible('local') && (
            <SettingsLocalSection
              onLocalLabelChange={onLocalLabelChange}
              contacts={contacts}
              channels={channels}
              appSettings={appSettings}
              onSaveAppSettings={onSaveAppSettings}
              className={sectionContentClass}
            />
          )}
        </section>
      )}

      {shouldRenderSection('radio-app') && (
        <section className={sectionWrapperClass}>
          {renderSectionHeader('radio-app')}
          {isSectionVisible('radio-app') &&
            (appSettings ? (
              <SettingsRadioAppSection
                appSettings={appSettings}
                onSaveAppSettings={onSaveAppSettings}
                blockedKeys={blockedKeys}
                blockedNames={blockedNames}
                onToggleBlockedKey={onToggleBlockedKey}
                onToggleBlockedName={onToggleBlockedName}
                contacts={contacts}
                onBulkDeleteContacts={onBulkDeleteContacts}
                trackedTelemetryRepeaters={trackedTelemetryRepeaters}
                onToggleTrackedTelemetry={onToggleTrackedTelemetry}
                trackedTelemetryContacts={trackedTelemetryContacts}
                onToggleTrackedTelemetryContact={onToggleTrackedTelemetryContact}
                className={sectionContentClass}
              />
            ) : (
              <div className={sectionContentClass}>
                <div className="rounded-md border border-input bg-muted/20 px-4 py-3 text-sm text-muted-foreground">
                  Loading app settings...
                </div>
              </div>
            ))}
        </section>
      )}

      {shouldRenderSection('database') && (
        <section className={sectionWrapperClass}>
          {renderSectionHeader('database')}
          {isSectionVisible('database') &&
            (appSettings ? (
              <SettingsDatabaseSection
                appSettings={appSettings}
                health={health}
                onSaveAppSettings={onSaveAppSettings}
                onHealthRefresh={onHealthRefresh}
                className={sectionContentClass}
              />
            ) : (
              <div className={sectionContentClass}>
                <div className="rounded-md border border-input bg-muted/20 px-4 py-3 text-sm text-muted-foreground">
                  Loading app settings...
                </div>
              </div>
            ))}
        </section>
      )}

      {shouldRenderSection('fanout') && (
        <section className={sectionWrapperClass}>
          {renderSectionHeader('fanout')}
          {isSectionVisible('fanout') && (
            <SettingsFanoutSection
              health={health}
              onHealthRefresh={onHealthRefresh}
              className={sectionContentClass}
            />
          )}
        </section>
      )}

      {shouldRenderSection('statistics') && (
        <section className={sectionWrapperClass}>
          {renderSectionHeader('statistics')}
          {isSectionVisible('statistics') && (
            <SettingsStatisticsSection className={sectionContentClass} />
          )}
        </section>
      )}

      {shouldRenderSection('about') && (
        <section className={sectionWrapperClass}>
          {renderSectionHeader('about')}
          {isSectionVisible('about') && (
            <SettingsAboutSection health={health} className={sectionContentClass} />
          )}
        </section>
      )}
    </div>
  );
}
