import { lazy, Suspense, useMemo, useRef, useState, type Ref } from 'react';

import { ChatHeader } from './ChatHeader';
import { MessageInput, type MessageInputHandle } from './MessageInput';
import { MessageList } from './MessageList';
import { RawPacketFeedView } from './RawPacketFeedView';
import { RoomServerPanel } from './RoomServerPanel';
import { TracePane } from './TracePane';
import type {
  Channel,
  Contact,
  Conversation,
  HealthStatus,
  Message,
  PathDiscoveryResponse,
  RadioConfig,
  RadioTraceHopRequest,
  RadioTraceResponse,
} from '../types';
import { CONTACT_TYPE_REPEATER, CONTACT_TYPE_ROOM } from '../types';
import {
  getContactDisplayName,
  isPrefixOnlyContact,
  isUnknownFullKeyContact,
} from '../utils/pubkey';

const RepeaterDashboard = lazy(() =>
  import('./RepeaterDashboard').then((m) => ({ default: m.RepeaterDashboard }))
);
const MapView = lazy(() => import('./MapView').then((m) => ({ default: m.MapView })));
const BotsView = lazy(() => import('./bots/BotsView').then((m) => ({ default: m.BotsView })));
const VisualizerView = lazy(() =>
  import('./VisualizerView').then((m) => ({ default: m.VisualizerView }))
);
const StatisticsView = lazy(() =>
  import('./StatisticsView').then((m) => ({ default: m.StatisticsView }))
);

interface ConversationPaneProps {
  activeConversation: Conversation | null;
  contacts: Contact[];
  channels: Channel[];
  config: RadioConfig | null;
  health: HealthStatus | null;
  notificationsSupported: boolean;
  notificationsEnabled: boolean;
  notificationsPermission: NotificationPermission | 'unsupported';
  messages: Message[];
  preSorted?: boolean;
  messagesLoading: boolean;
  loadingOlder: boolean;
  hasOlderMessages: boolean;
  unreadMarkerMessageId?: number | null;
  onNavigateToUnread?: (messageId: number) => void;
  targetMessageId: number | null;
  hasNewerMessages: boolean;
  loadingNewer: boolean;
  messageInputRef: Ref<MessageInputHandle>;
  onTrace: () => Promise<void>;
  onRunTracePath: (
    hopHashBytes: 1 | 2 | 4,
    hops: RadioTraceHopRequest[]
  ) => Promise<RadioTraceResponse>;
  onPathDiscovery: (publicKey: string) => Promise<PathDiscoveryResponse>;
  onToggleFavorite: (type: 'channel' | 'contact', id: string) => Promise<void>;
  onToggleMute: (key: string) => Promise<void>;
  onDeleteContact: (publicKey: string) => Promise<void>;
  onDeleteChannel: (key: string) => Promise<void>;
  onSetChannelFloodScopeOverride: (channelKey: string, floodScopeOverride: string) => Promise<void>;
  onSetChannelPathHashModeOverride?: (
    channelKey: string,
    pathHashModeOverride: number | null
  ) => Promise<void>;
  onSelectConversation: (conversation: Conversation) => void;
  onOpenContactInfo: (publicKey: string, fromChannel?: boolean) => void;
  onOpenChannelInfo: (channelKey: string) => void;
  onSenderClick: (sender: string) => void;
  onChannelReferenceClick?: (channelName: string) => void;
  onLoadOlder: () => Promise<void>;
  onResendChannelMessage: (messageId: number, newTimestamp?: boolean) => Promise<void>;
  onTargetReached: () => void;
  onLoadNewer: () => Promise<void>;
  onJumpToBottom: () => void;
  onDismissUnreadMarker: () => void;
  onSendMessage: (text: string) => Promise<void>;
  onToggleNotifications: () => void;
  pushSupported?: boolean;
  pushSubscribed?: boolean;
  pushEnabledForConversation?: boolean;
  onTogglePush?: () => void;
  onOpenPushSettings?: () => void;
  trackedTelemetryRepeaters: string[];
  onToggleTrackedTelemetry: (publicKey: string) => Promise<void>;
  repeaterAutoLoginKey: string | null;
  onClearRepeaterAutoLogin: () => void;
  blockedKeys?: string[];
  blockedNames?: string[];
}

function LoadingPane({ label }: { label: string }) {
  return (
    <div className="flex-1 flex items-center justify-center text-muted-foreground">{label}</div>
  );
}

function ContactResolutionBanner({ variant }: { variant: 'unknown-full-key' | 'prefix-only' }) {
  if (variant === 'prefix-only') {
    return (
      <div className="mx-4 mt-3 rounded-md border border-destructive/30 bg-destructive/10 px-3 py-2 text-sm text-destructive">
        We&apos;ve received a message from this sender but don&apos;t have their full identity yet.
        Sending is disabled until their identity is confirmed &mdash; this usually happens
        automatically when they next advertise.
      </div>
    );
  }

  return (
    <div className="mx-4 mt-3 rounded-md border border-warning/30 bg-warning/10 px-3 py-2 text-sm text-warning">
      This sender&apos;s profile details (name, location) haven&apos;t arrived yet. They will fill
      in automatically when the sender&apos;s next advert is heard.
    </div>
  );
}

export function ConversationPane({
  activeConversation,
  contacts,
  channels,
  config,
  health,
  notificationsSupported,
  notificationsEnabled,
  notificationsPermission,
  messages,
  preSorted,
  messagesLoading,
  loadingOlder,
  hasOlderMessages,
  unreadMarkerMessageId,
  onNavigateToUnread,
  targetMessageId,
  hasNewerMessages,
  loadingNewer,
  messageInputRef,
  onTrace,
  onRunTracePath,
  onPathDiscovery,
  onToggleFavorite,
  onToggleMute,
  onDeleteContact,
  onDeleteChannel,
  onSetChannelFloodScopeOverride,
  onSetChannelPathHashModeOverride,
  onSelectConversation,
  onOpenContactInfo,
  onOpenChannelInfo,
  onSenderClick,
  onChannelReferenceClick,
  onLoadOlder,
  onResendChannelMessage,
  onTargetReached,
  onLoadNewer,
  onJumpToBottom,
  onDismissUnreadMarker,
  onSendMessage,
  onToggleNotifications,
  pushSupported,
  pushSubscribed,
  pushEnabledForConversation,
  onTogglePush,
  onOpenPushSettings,
  trackedTelemetryRepeaters,
  onToggleTrackedTelemetry,
  repeaterAutoLoginKey,
  onClearRepeaterAutoLogin,
  blockedKeys,
  blockedNames,
}: ConversationPaneProps) {
  const [roomAuthenticated, setRoomAuthenticated] = useState(false);
  const activeContactIsRepeater = useMemo(() => {
    if (!activeConversation || activeConversation.type !== 'contact') return false;
    const contact = contacts.find((candidate) => candidate.public_key === activeConversation.id);
    return contact?.type === CONTACT_TYPE_REPEATER;
  }, [activeConversation, contacts]);
  const activeContact = useMemo(() => {
    if (!activeConversation || activeConversation.type !== 'contact') return null;
    return contacts.find((candidate) => candidate.public_key === activeConversation.id) ?? null;
  }, [activeConversation, contacts]);
  const activeContactIsRoom = activeContact?.type === CONTACT_TYPE_ROOM;
  // Reset the room-auth gate when the conversation changes, but do it during
  // render (guarded by the previous id) rather than in an effect. An effect here
  // races the keyed RoomServerPanel's own onAuthenticatedChange mount report:
  // React runs the child's effect before the parent's, so an effect reset would
  // clobber the child's "authenticated" with false and hide the chat until a
  // full reload. Resetting during render lets the child's post-commit report win.
  const prevConversationIdRef = useRef(activeConversation?.id);
  if (prevConversationIdRef.current !== activeConversation?.id) {
    prevConversationIdRef.current = activeConversation?.id;
    if (roomAuthenticated) setRoomAuthenticated(false);
  }
  const isPrefixOnlyActiveContact = activeContact
    ? isPrefixOnlyContact(activeContact.public_key)
    : false;
  const isUnknownFullKeyActiveContact =
    activeContact !== null &&
    !isPrefixOnlyActiveContact &&
    isUnknownFullKeyContact(activeContact.public_key, activeContact.last_advert);

  if (!activeConversation) {
    return (
      <div className="flex-1 flex items-center justify-center text-muted-foreground">
        Select a conversation or start a new one
      </div>
    );
  }

  if (activeConversation.type === 'map') {
    return (
      <>
        <h2 className="flex justify-between items-center px-4 py-2.5 border-b border-border font-semibold text-base">
          Node Map
        </h2>
        <div className="flex-1 overflow-hidden">
          <Suspense fallback={<LoadingPane label="Loading map..." />}>
            <MapView
              contacts={contacts}
              focusedKey={activeConversation.mapFocusKey}
              config={config}
              blockedKeys={blockedKeys}
              blockedNames={blockedNames}
              onSelectContact={(contact) =>
                onSelectConversation({
                  type: 'contact',
                  id: contact.public_key,
                  name: getContactDisplayName(
                    contact.name,
                    contact.public_key,
                    contact.last_advert
                  ),
                })
              }
            />
          </Suspense>
        </div>
      </>
    );
  }

  if (activeConversation.type === 'visualizer') {
    return (
      <Suspense fallback={<LoadingPane label="Loading visualizer..." />}>
        <VisualizerView contacts={contacts} channels={channels} config={config} />
      </Suspense>
    );
  }

  if (activeConversation.type === 'raw') {
    return <RawPacketFeedView contacts={contacts} channels={channels} />;
  }

  if (activeConversation.type === 'bots') {
    return (
      <Suspense fallback={<LoadingPane label="Loading bots..." />}>
        <BotsView
          botId={activeConversation.botId ?? null}
          channels={channels}
          contacts={contacts}
          onOpenBot={(botId) =>
            onSelectConversation({ type: 'bots', id: 'bots', name: 'Bots', botId })
          }
          onCloseBot={() => onSelectConversation({ type: 'bots', id: 'bots', name: 'Bots' })}
        />
      </Suspense>
    );
  }

  if (activeConversation.type === 'statistics') {
    return (
      <Suspense fallback={<LoadingPane label="Loading statistics..." />}>
        <StatisticsView />
      </Suspense>
    );
  }

  if (activeConversation.type === 'search') {
    return null;
  }

  if (activeConversation.type === 'trace') {
    return <TracePane contacts={contacts} config={config} onRunTracePath={onRunTracePath} />;
  }

  if (activeContactIsRepeater) {
    return (
      <Suspense fallback={<LoadingPane label="Loading dashboard..." />}>
        <RepeaterDashboard
          key={activeConversation.id}
          conversation={activeConversation}
          contacts={contacts}
          notificationsSupported={notificationsSupported}
          notificationsEnabled={notificationsEnabled}
          notificationsPermission={notificationsPermission}
          radioLat={config?.lat ?? null}
          radioLon={config?.lon ?? null}
          radioName={config?.name ?? null}
          onTrace={onTrace}
          onPathDiscovery={onPathDiscovery}
          onToggleNotifications={onToggleNotifications}
          onToggleFavorite={onToggleFavorite}
          onDeleteContact={onDeleteContact}
          onOpenContactInfo={onOpenContactInfo}
          trackedTelemetryRepeaters={trackedTelemetryRepeaters}
          onToggleTrackedTelemetry={onToggleTrackedTelemetry}
          autoLoginAndLoadAll={repeaterAutoLoginKey === activeConversation.id}
          onAutoLoginConsumed={onClearRepeaterAutoLogin}
        />
      </Suspense>
    );
  }

  const showRoomChat = !activeContactIsRoom || roomAuthenticated;

  return (
    <>
      <ChatHeader
        conversation={activeConversation}
        contacts={contacts}
        channels={channels}
        config={config}
        notificationsSupported={notificationsSupported}
        notificationsEnabled={notificationsEnabled}
        notificationsPermission={notificationsPermission}
        pushSupported={pushSupported}
        pushSubscribed={pushSubscribed}
        pushEnabledForConversation={pushEnabledForConversation}
        onTogglePush={onTogglePush}
        onOpenPushSettings={onOpenPushSettings}
        onTrace={onTrace}
        onPathDiscovery={onPathDiscovery}
        onToggleNotifications={onToggleNotifications}
        onToggleFavorite={onToggleFavorite}
        onToggleMute={onToggleMute}
        onSetChannelFloodScopeOverride={onSetChannelFloodScopeOverride}
        onSetChannelPathHashModeOverride={onSetChannelPathHashModeOverride}
        onDeleteChannel={onDeleteChannel}
        onDeleteContact={onDeleteContact}
        onOpenContactInfo={onOpenContactInfo}
        onOpenChannelInfo={onOpenChannelInfo}
      />
      {activeConversation.type === 'contact' && isPrefixOnlyActiveContact && (
        <ContactResolutionBanner variant="prefix-only" />
      )}
      {activeConversation.type === 'contact' && isUnknownFullKeyActiveContact && (
        <ContactResolutionBanner variant="unknown-full-key" />
      )}
      {activeContactIsRoom && activeContact && (
        // Key by conversation so switching rooms remounts the panel and restores
        // that room's own state (via its cache) instead of leaking the previous
        // room's login/failed-login view. The key is namespaced (`room-`) so it
        // does NOT collide with the sibling MessageList's key={activeConversation.id}
        // — an authenticated room renders both, and duplicate sibling keys break
        // React reconciliation.
        <RoomServerPanel
          key={`room-${activeConversation.id}`}
          contact={activeContact}
          onAuthenticatedChange={setRoomAuthenticated}
        />
      )}
      {showRoomChat && <div data-toast-anchor="conversation" aria-hidden="true" />}
      {showRoomChat && (
        <MessageList
          key={activeConversation.id}
          messages={messages}
          preSorted={preSorted}
          contacts={contacts}
          channels={channels}
          loading={messagesLoading}
          loadingOlder={loadingOlder}
          hasOlderMessages={hasOlderMessages}
          unreadMarkerMessageId={
            activeConversation.type === 'channel' ? unreadMarkerMessageId : undefined
          }
          onNavigateToUnread={
            activeConversation.type === 'channel' ? onNavigateToUnread : undefined
          }
          onDismissUnreadMarker={
            activeConversation.type === 'channel' ? onDismissUnreadMarker : undefined
          }
          onSenderClick={activeConversation.type === 'channel' ? onSenderClick : undefined}
          onChannelReferenceClick={onChannelReferenceClick}
          onLoadOlder={onLoadOlder}
          onResendChannelMessage={
            activeConversation.type === 'channel' ? onResendChannelMessage : undefined
          }
          radioName={config?.name}
          config={config}
          onOpenContactInfo={onOpenContactInfo}
          targetMessageId={targetMessageId}
          onTargetReached={onTargetReached}
          hasNewerMessages={hasNewerMessages}
          loadingNewer={loadingNewer}
          onLoadNewer={onLoadNewer}
          onJumpToBottom={onJumpToBottom}
        />
      )}
      {showRoomChat && !(activeConversation.type === 'contact' && isPrefixOnlyActiveContact) ? (
        <MessageInput
          ref={messageInputRef}
          onSend={onSendMessage}
          disabled={!health?.radio_connected}
          conversationType={activeConversation.type}
          senderName={config?.name}
          voiceConversation={
            activeConversation.id
              ? {
                  type: activeConversation.type === 'contact' ? 'PRIV' : 'CHAN',
                  key: activeConversation.id,
                }
              : undefined
          }
          placeholder={
            !health?.radio_connected
              ? 'Radio not connected'
              : `Message ${activeConversation.name}...`
          }
        />
      ) : null}
    </>
  );
}
