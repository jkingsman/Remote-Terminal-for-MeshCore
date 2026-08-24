/**
 * Tests for MessageInput component.
 *
 * Verifies character/byte limit calculation, warning states, and send button
 * behavior for both DM and channel conversations.
 */

import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach } from 'vitest';

import { MessageInput } from '../components/MessageInput';
import { toast } from '../components/ui/sonner';
import { api } from '../api';
import { encodeMeshImage } from '../services/imageCodec';

const voiceCapture = vi.hoisted(() => ({
  start: vi.fn().mockResolvedValue(undefined),
  stop: vi.fn().mockResolvedValue({
    pcm: new Blob(['voice']),
    durationMs: 500,
  }),
  cancel: vi.fn().mockResolvedValue(undefined),
}));
const encodedImage = vi.hoisted(() => ({
  blob: new Blob(['encoded-image'], { type: 'image/jpeg' }),
  format: 1 as const,
  width: 128,
  height: 96,
}));

vi.mock('../services/voiceCapture', () => ({
  VoiceCapture: vi.fn(function VoiceCapture() {
    return voiceCapture;
  }),
}));

vi.mock('../services/imageCodec', () => ({
  encodeMeshImage: vi.fn().mockResolvedValue(encodedImage),
}));

vi.mock('../api', async (importOriginal) => {
  const original = await importOriginal<typeof import('../api')>();
  return {
    ...original,
    api: {
      ...original.api,
      sendVoice: vi.fn().mockResolvedValue(undefined),
      sendImage: vi.fn().mockResolvedValue(undefined),
    },
  };
});

// Mock sonner (toast)
vi.mock('../components/ui/sonner', () => ({
  toast: { success: vi.fn(), error: vi.fn() },
}));

const mockToast = toast as unknown as {
  success: ReturnType<typeof vi.fn>;
  error: ReturnType<typeof vi.fn>;
};

const textEncoder = new TextEncoder();

function byteLen(s: string): number {
  return textEncoder.encode(s).length;
}

describe('MessageInput', () => {
  const onSend = vi.fn().mockResolvedValue(undefined);

  beforeEach(() => {
    vi.clearAllMocks();
    Object.defineProperty(window, 'PointerEvent', { configurable: true, value: MouseEvent });
    voiceCapture.start.mockResolvedValue(undefined);
    voiceCapture.stop.mockResolvedValue({ pcm: new Blob(['voice']), durationMs: 500 });
    voiceCapture.cancel.mockResolvedValue(undefined);
  });

  function renderInput(props: {
    conversationType?: 'contact' | 'channel' | 'raw';
    senderName?: string;
    disabled?: boolean;
    voice?: boolean;
  }) {
    return render(
      <MessageInput
        onSend={onSend}
        disabled={props.disabled ?? false}
        conversationType={props.conversationType}
        senderName={props.senderName}
        placeholder="Type a message..."
        voiceConversation={props.voice ? { type: 'PRIV', key: 'aa'.repeat(32) } : undefined}
      />
    );
  }

  function getInput() {
    return screen.getByPlaceholderText('Type a message...') as HTMLTextAreaElement;
  }

  function getSendButton() {
    return screen.getByRole('button', { name: /send/i }) as HTMLButtonElement;
  }

  describe('send button state', () => {
    it('is disabled when text is empty', () => {
      renderInput({ conversationType: 'contact' });
      expect(getSendButton()).toBeDisabled();
    });

    it('is enabled when text is entered', () => {
      renderInput({ conversationType: 'contact' });
      fireEvent.change(getInput(), { target: { value: 'Hello' } });
      expect(getSendButton()).toBeEnabled();
    });

    it('is disabled when whitespace-only', () => {
      renderInput({ conversationType: 'contact' });
      fireEvent.change(getInput(), { target: { value: '   ' } });
      expect(getSendButton()).toBeDisabled();
    });

    it('is disabled when disabled prop is true', () => {
      renderInput({ conversationType: 'contact', disabled: true });
      fireEvent.change(getInput(), { target: { value: 'Hello' } });
      expect(getSendButton()).toBeDisabled();
    });
  });

  describe('byte counter display', () => {
    it('shows byte counter for DM conversations', () => {
      renderInput({ conversationType: 'contact' });
      fireEvent.change(getInput(), { target: { value: 'Hello' } });

      // Should show "5/156" somewhere (DM hard limit = 156)
      expect(screen.getByText(/5\/156/)).toBeTruthy();
    });

    it('shows byte counter for channel conversations', () => {
      renderInput({ conversationType: 'channel', senderName: 'MyNode' });
      fireEvent.change(getInput(), { target: { value: 'Hello' } });

      // Channel hard limit = 156 - byteLen("MyNode") - 2 = 156 - 6 - 2 = 148
      expect(screen.getByText(/5\/148/)).toBeTruthy();
    });

    it('does not show byte counter for raw conversations', () => {
      renderInput({ conversationType: 'raw' });
      fireEvent.change(getInput(), { target: { value: 'Hello' } });

      // No counter should be visible
      expect(screen.queryByText(/\/\d+/)).toBeNull();
    });

    it('accounts for multi-byte characters in byte count', () => {
      renderInput({ conversationType: 'contact' });
      // Emoji: "🥝" is 4 bytes in UTF-8
      fireEvent.change(getInput(), { target: { value: '🥝' } });
      const bytes = byteLen('🥝'); // Should be 4
      expect(bytes).toBe(4);
      expect(screen.getByText(new RegExp(`${bytes}/156`))).toBeTruthy();
    });
  });

  describe('channel limit adjusts for sender name', () => {
    it('reduces limit based on sender name byte length', () => {
      // Sender name "LongNodeName" = 12 bytes + 2 for ": " = 14 overhead
      // Hard limit = 156 - 14 = 142
      renderInput({ conversationType: 'channel', senderName: 'LongNodeName' });
      fireEvent.change(getInput(), { target: { value: 'x' } });
      expect(screen.getByText(/1\/142/)).toBeTruthy();
    });

    it('uses default 10-byte name when sender name is absent', () => {
      // Default: 10 bytes + 2 = 12 overhead. Hard limit = 156 - 12 = 144
      renderInput({ conversationType: 'channel' });
      fireEvent.change(getInput(), { target: { value: 'x' } });
      expect(screen.getByText(/1\/144/)).toBeTruthy();
    });

    it('handles multi-byte sender names correctly', () => {
      // "🥝Node" = 4 + 4 = 8 bytes name + 2 separator = 10 overhead
      // Hard limit = 156 - 10 = 146
      const senderName = '🥝Node';
      const nameBytes = byteLen(senderName);
      const expectedLimit = 156 - nameBytes - 2;
      renderInput({ conversationType: 'channel', senderName });
      fireEvent.change(getInput(), { target: { value: 'x' } });
      expect(screen.getByText(new RegExp(`1/${expectedLimit}`))).toBeTruthy();
    });
  });

  describe('warning states', () => {
    it('shows warning text when exceeding DM warning threshold', () => {
      renderInput({ conversationType: 'contact' });
      // DM warning threshold = 140 bytes
      const text = 'x'.repeat(141);
      fireEvent.change(getInput(), { target: { value: text } });
      // Rendered in both desktop and mobile variants
      expect(screen.getAllByText(/may impact multi-repeater hop delivery/).length).toBeGreaterThan(
        0
      );
    });

    it('shows truncation warning when exceeding DM hard limit', () => {
      renderInput({ conversationType: 'contact' });
      // DM hard limit = 156 bytes
      const text = 'x'.repeat(157);
      fireEvent.change(getInput(), { target: { value: text } });
      // Rendered in both desktop and mobile variants
      expect(screen.getAllByText(/likely truncated by radio/).length).toBeGreaterThan(0);
    });

    it('shows no warning for short messages', () => {
      renderInput({ conversationType: 'contact' });
      fireEvent.change(getInput(), { target: { value: 'Hello' } });
      expect(screen.queryByText(/truncated/)).toBeNull();
      expect(screen.queryByText(/may impact/)).toBeNull();
    });
  });

  describe('send button remains enabled past hard limit (current behavior)', () => {
    it('does not disable send button when over hard limit', () => {
      // NOTE: This documents the current behavior where canSubmit only checks
      // text.trim().length > 0, NOT the limit state. This is related to
      // hitlist item 1.1 — the send button stays enabled even over the limit.
      renderInput({ conversationType: 'contact' });
      const text = 'x'.repeat(200); // Well over 156 byte limit
      fireEvent.change(getInput(), { target: { value: text } });

      // Button is still enabled — canSubmit only checks non-empty text
      expect(getSendButton()).toBeEnabled();
    });
  });

  describe('send failure toasts', () => {
    it('shows the radio no-response toast when the send outcome is unknown', async () => {
      onSend.mockRejectedValueOnce(
        new Error(
          'Send command was issued to the radio, but no response was heard back. The message may or may not have sent successfully.'
        )
      );
      renderInput({ conversationType: 'contact' });

      fireEvent.change(getInput(), { target: { value: 'Hello' } });
      fireEvent.click(getSendButton());

      expect(await screen.findByDisplayValue('Hello')).toBeTruthy();
      expect(mockToast.error).toHaveBeenCalledWith('Radio did not confirm send', {
        description:
          'Send command was issued to the radio, but no response was heard back. The message may or may not have sent successfully.',
      });
    });
  });

  describe('voice recording', () => {
    it('places media controls left of the text field and always keeps send visible', () => {
      renderInput({ conversationType: 'contact', voice: true });

      const image = screen.getByRole('button', { name: /attach image/i });
      const microphone = screen.getByRole('button', { name: /hold to record voice/i });
      const input = getInput();
      expect(
        image.compareDocumentPosition(microphone) & Node.DOCUMENT_POSITION_FOLLOWING
      ).toBeTruthy();
      expect(
        microphone.compareDocumentPosition(input) & Node.DOCUMENT_POSITION_FOLLOWING
      ).toBeTruthy();
      expect(screen.getByRole('button', { name: /^send$/i })).toBeVisible();
      expect(screen.getByRole('button', { name: /^send$/i })).toBeDisabled();
    });

    it('preserves text send and keeps image attachment available when text is entered', () => {
      renderInput({ conversationType: 'contact', voice: true });
      fireEvent.change(getInput(), { target: { value: 'Hello' } });

      expect(screen.getByRole('button', { name: /^send$/i })).toBeVisible();
      expect(screen.getByRole('button', { name: /attach image/i })).toBeVisible();
      expect(screen.getByRole('button', { name: /hold to record voice/i })).toBeVisible();
    });

    it('shows recording state on pointer down and sends on pointer up', async () => {
      Object.defineProperty(window, 'isSecureContext', { configurable: true, value: true });
      renderInput({ conversationType: 'contact', voice: true });
      const microphone = screen.getByRole('button', { name: /hold to record voice/i });

      fireEvent.pointerDown(microphone, { pointerId: 1 });
      expect(await screen.findByText('Release to send')).toBeVisible();
      fireEvent.pointerUp(screen.getByRole('button', { name: /release to send voice/i }), {
        pointerId: 1,
      });

      await waitFor(() => expect(api.sendVoice).toHaveBeenCalledTimes(1));
    });

    it('does not send after the slide-up cancel gesture', async () => {
      Object.defineProperty(window, 'isSecureContext', { configurable: true, value: true });
      renderInput({ conversationType: 'contact', voice: true });
      const microphone = screen.getByRole('button', { name: /hold to record voice/i });

      fireEvent.pointerDown(microphone, { pointerId: 1 });
      await screen.findByText('Release to send');
      const recordingMicrophone = screen.getByRole('button', { name: /release to send voice/i });
      fireEvent.pointerMove(recordingMicrophone, { pointerId: 1, clientY: -100 });
      expect(screen.getByText('Release to cancel')).toBeVisible();
      fireEvent.pointerUp(recordingMicrophone, { pointerId: 1 });

      await waitFor(() => expect(voiceCapture.cancel).toHaveBeenCalledTimes(1));
      expect(api.sendVoice).not.toHaveBeenCalled();
    });

    it('explains the HTTPS requirement before requesting a microphone', () => {
      Object.defineProperty(window, 'isSecureContext', { configurable: true, value: false });
      renderInput({ conversationType: 'contact', voice: true });
      fireEvent.pointerDown(screen.getByRole('button', { name: /hold to record voice/i }));
      expect(mockToast.error).toHaveBeenCalledWith(
        'Voice recording requires HTTPS to access your microphone.',
        expect.objectContaining({ action: expect.objectContaining({ label: 'Configure HTTPS' }) })
      );
    });
  });

  describe('image attachment', () => {
    it('opens the picker, previews a selected image, and cancels', async () => {
      renderInput({ conversationType: 'contact', voice: true });
      const picker = screen.getByLabelText('Choose image') as HTMLInputElement;
      const click = vi.spyOn(picker, 'click');
      fireEvent.click(screen.getByRole('button', { name: /attach image/i }));
      expect(click).toHaveBeenCalledOnce();

      const file = new File(['source'], 'photo.png', { type: 'image/png' });
      fireEvent.change(picker, { target: { files: [file] } });
      expect(await screen.findByAltText('Image attachment preview')).toBeVisible();
      expect(screen.getByText(/128×96/)).toBeVisible();
      expect(screen.getByText(/1 fragments/)).toBeVisible();
      fireEvent.click(screen.getByRole('button', { name: 'Cancel' }));
      expect(screen.queryByAltText('Image attachment preview')).not.toBeInTheDocument();
    });

    it('sends only after preview confirmation', async () => {
      renderInput({ conversationType: 'contact', voice: true });
      const file = new File(['source'], 'photo.jpg', { type: 'image/jpeg' });
      fireEvent.change(screen.getByLabelText('Choose image'), { target: { files: [file] } });
      expect(await screen.findByAltText('Image attachment preview')).toBeVisible();
      expect(api.sendImage).not.toHaveBeenCalled();
      fireEvent.click(screen.getByRole('button', { name: 'Send image' }));
      await waitFor(() =>
        expect(api.sendImage).toHaveBeenCalledWith('PRIV', 'aa'.repeat(32), encodedImage)
      );
    });

    it('rejects an invalid image cleanly', async () => {
      vi.mocked(encodeMeshImage).mockRejectedValueOnce(new Error('Invalid image data'));
      renderInput({ conversationType: 'contact', voice: true });
      const file = new File(['bad'], 'broken.png', { type: 'image/png' });
      fireEvent.change(screen.getByLabelText('Choose image'), { target: { files: [file] } });
      await waitFor(() =>
        expect(mockToast.error).toHaveBeenCalledWith('Image unavailable', {
          description: 'Invalid image data',
        })
      );
      expect(screen.queryByRole('button', { name: 'Send image' })).not.toBeInTheDocument();
    });
  });

  describe('emoji picker', () => {
    it('opens the picker and inserts an emoji into the message', () => {
      renderInput({ conversationType: 'contact', voice: true });

      fireEvent.click(screen.getByRole('button', { name: 'Add emoji' }));
      expect(screen.getByRole('dialog', { name: 'Emoji picker' })).toBeVisible();
      fireEvent.click(screen.getByRole('button', { name: 'Insert 😀' }));

      expect(getInput()).toHaveValue('😀');
      expect(screen.queryByRole('dialog', { name: 'Emoji picker' })).not.toBeInTheDocument();
      expect(getSendButton()).toBeEnabled();
    });
  });
});
