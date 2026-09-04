import { lazy, Suspense, useEffect, useMemo, useState, type Ref } from 'react';

import { ChatHeader } from './ChatHeader';
import {
  ChannelUnreadSummaryBanner,
  type ChannelUnreadSummaryRequest,
} from './ChannelUnreadSummaryBanner';
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
const VisualizerView = lazy(() =>
  import('./VisualizerView').then((m) => ({ default: m.VisualizerView }))
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
  unreadSummaryRequest?: ChannelUnreadSummaryRequest | null;
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
  unreadSummaryRequest = null,
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
  useEffect(() => {
    setRoomAuthenticated(false);
  }, [activeConversation?.id]);
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
      {activeConversation.type === 'channel' && (
        <ChannelUnreadSummaryBanner request={unreadSummaryRequest} />
      )}
      {activeContactIsRoom && activeContact && (
        <RoomServerPanel contact={activeContact} onAuthenticatedChange={setRoomAuthenticated} />
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
