import { useCallback, type MutableRefObject, type RefObject } from 'react';
import { api } from '../api';
import { toast } from '../components/ui/sonner';
import type { MessageInputHandle } from '../components/MessageInput';
import type { Channel, Contact, Conversation, Message, PathDiscoveryResponse } from '../types';
import { mergeContactIntoList } from '../utils/contactMerge';

interface UseConversationActionsArgs {
  activeConversation: Conversation | null;
  activeConversationRef: MutableRefObject<Conversation | null>;
  setContacts: React.Dispatch<React.SetStateAction<Contact[]>>;
  setChannels: React.Dispatch<React.SetStateAction<Channel[]>>;
  observeMessage: (msg: Message) => { added: boolean; activeConversation: boolean };
  messageInputRef: RefObject<MessageInputHandle | null>;
}

interface UseConversationActionsResult {
  handleSendMessage: (text: string) => Promise<void>;
  handleResendChannelMessage: (messageId: number, newTimestamp?: boolean) => Promise<void>;
  handleSetChannelFloodScopeOverride: (
    channelKey: string,
    floodScopeOverride: string
  ) => Promise<void>;
  handleSetChannelPathHashModeOverride: (
    channelKey: string,
    pathHashModeOverride: number | null
  ) => Promise<void>;
  handleSetChannelMcmp: (
    channelKey: string,
    mcmpEnabled: boolean,
    mcmpSignEnabled: boolean
  ) => Promise<void>;
  handleSetContactMcmp: (publicKey: string, mcmpEnabled: boolean) => Promise<void>;
  handleSenderClick: (sender: string) => void;
  handleTrace: () => Promise<void>;
  handlePathDiscovery: (publicKey: string) => Promise<PathDiscoveryResponse>;
}

export function useConversationActions({
  activeConversation,
  activeConversationRef,
  setContacts,
  setChannels,
  observeMessage,
  messageInputRef,
}: UseConversationActionsArgs): UseConversationActionsResult {
  const mergeChannelIntoList = useCallback(
    (updated: Channel) => {
      setChannels((prev) => {
        const existingIndex = prev.findIndex((channel) => channel.key === updated.key);
        if (existingIndex === -1) {
          return [...prev, updated].sort((a, b) => a.name.localeCompare(b.name));
        }
        const next = [...prev];
        next[existingIndex] = updated;
        return next;
      });
    },
    [setChannels]
  );

  const handleSendMessage = useCallback(
    async (text: string) => {
      if (!activeConversation) return;

      const conversationId = activeConversation.id;
      const sent =
        activeConversation.type === 'channel'
          ? await api.sendChannelMessage(activeConversation.id, text)
          : await api.sendDirectMessage(activeConversation.id, text);

      if (activeConversationRef.current?.id === conversationId) {
        observeMessage(sent);
      }
    },
    [activeConversation, activeConversationRef, observeMessage]
  );

  const handleResendChannelMessage = useCallback(
    async (messageId: number, newTimestamp?: boolean) => {
      try {
        const resent = await api.resendChannelMessage(messageId, newTimestamp);
        const resentMessage = resent.message;
        if (
          newTimestamp &&
          resentMessage &&
          activeConversationRef.current?.type === 'channel' &&
          activeConversationRef.current.id === resentMessage.conversation_key
        ) {
          observeMessage(resentMessage);
        }
        toast.success(newTimestamp ? 'Message resent with new timestamp' : 'Message resent');
      } catch (err) {
        toast.error('Failed to resend', {
          description: err instanceof Error ? err.message : 'Unknown error',
        });
      }
    },
    [activeConversationRef, observeMessage]
  );

  const handleSetChannelFloodScopeOverride = useCallback(
    async (channelKey: string, floodScopeOverride: string) => {
      try {
        const updated = await api.setChannelFloodScopeOverride(channelKey, floodScopeOverride);
        mergeChannelIntoList(updated);
        toast.success(
          updated.flood_scope_override ? 'Regional override saved' : 'Regional override cleared'
        );
      } catch (err) {
        toast.error('Failed to update regional override', {
          description: err instanceof Error ? err.message : 'Unknown error',
        });
      }
    },
    [mergeChannelIntoList]
  );

  const handleSetChannelPathHashModeOverride = useCallback(
    async (channelKey: string, pathHashModeOverride: number | null) => {
      try {
        const updated = await api.setChannelPathHashModeOverride(channelKey, pathHashModeOverride);
        mergeChannelIntoList(updated);
        toast.success(
          updated.path_hash_mode_override != null
            ? 'Path hop width override saved'
            : 'Path hop width override cleared'
        );
      } catch (err) {
        toast.error('Failed to update path hop width override', {
          description: err instanceof Error ? err.message : 'Unknown error',
        });
      }
    },
    [mergeChannelIntoList]
  );

  const handleSetChannelMcmp = useCallback(
    async (channelKey: string, mcmpEnabled: boolean, mcmpSignEnabled: boolean) => {
      try {
        const updated = await api.setChannelMcmpSettings(channelKey, mcmpEnabled, mcmpSignEnabled);
        mergeChannelIntoList(updated);
        toast.success('MCMP settings saved');
      } catch (err) {
        toast.error('Failed to update MCMP settings', {
          description: err instanceof Error ? err.message : 'Unknown error',
        });
      }
    },
    [mergeChannelIntoList]
  );

  const handleSetContactMcmp = useCallback(
    async (publicKey: string, mcmpEnabled: boolean) => {
      try {
        const updated = await api.setContactMcmpSettings(publicKey, mcmpEnabled);
        setContacts((prev) => mergeContactIntoList(prev, updated));
        toast.success('MCMP setting saved');
      } catch (err) {
        toast.error('Failed to update MCMP setting', {
          description: err instanceof Error ? err.message : 'Unknown error',
        });
      }
    },
    [setContacts]
  );

  const handleSenderClick = useCallback(
    (sender: string) => {
      messageInputRef.current?.appendText(`@[${sender}] `);
    },
    [messageInputRef]
  );

  const handleTrace = useCallback(async () => {
    if (!activeConversation || activeConversation.type !== 'contact') return;
    toast('Trace started...');
    try {
      const result = await api.requestTrace(activeConversation.id);
      const parts: string[] = [];
      if (result.remote_snr !== null) parts.push(`Remote SNR: ${result.remote_snr.toFixed(1)} dB`);
      if (result.local_snr !== null) parts.push(`Local SNR: ${result.local_snr.toFixed(1)} dB`);
      const detail = parts.join(', ');
      toast.success(detail ? `Trace complete! ${detail}` : 'Trace complete!');
    } catch (err) {
      toast.error('Trace failed', {
        description: err instanceof Error ? err.message : 'Unknown error',
      });
    }
  }, [activeConversation]);

  const handlePathDiscovery = useCallback(
    async (publicKey: string) => {
      const result = await api.requestPathDiscovery(publicKey);
      setContacts((prev) => mergeContactIntoList(prev, result.contact));
      return result;
    },
    [setContacts]
  );

  return {
    handleSendMessage,
    handleResendChannelMessage,
    handleSetChannelFloodScopeOverride,
    handleSetChannelPathHashModeOverride,
    handleSetChannelMcmp,
    handleSetContactMcmp,
    handleSenderClick,
    handleTrace,
    handlePathDiscovery,
  };
}