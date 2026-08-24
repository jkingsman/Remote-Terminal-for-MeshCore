/**
 * Tests for MessageInput component.
 *
 * Verifies character/byte limit calculation, warning states, and send button
 * behavior for both DM and channel conversations.
 */

import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach } from 'vitest';

import { MessageInput } from '../components/MessageInput';
import { api } from '../api';
import { toast } from '../components/ui/sonner';

// Mock sonner (toast)
vi.mock('../components/ui/sonner', () => ({
  toast: { success: vi.fn(), error: vi.fn() },
}));

// Mock the API — MessageInput calls api.estimateMcmp for the compressed counter.
vi.mock('../api', () => ({
  api: { estimateMcmp: vi.fn() },
}));

const mockApi = api as unknown as { estimateMcmp: ReturnType<typeof vi.fn> };

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
  });

  function renderInput(props: {
    conversationType?: 'contact' | 'channel' | 'raw';
    senderName?: string;
    disabled?: boolean;
    mcmpEnabled?: boolean;
  }) {
    return render(
      <MessageInput
        onSend={onSend}
        disabled={props.disabled ?? false}
        conversationType={props.conversationType}
        senderName={props.senderName}
        mcmpEnabled={props.mcmpEnabled}
        placeholder="Type a message..."
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

  describe('MCMP compressed counter', () => {
    it('shows the compressed wire size against the budget when enabled', async () => {
      // 200 raw chars would be over the 156 budget, but they compress to 40
      // bytes — the counter must reflect the compressed size so more fits.
      mockApi.estimateMcmp.mockResolvedValue({ wire_bytes: 40, compressed: true });
      renderInput({ conversationType: 'contact', mcmpEnabled: true });

      const text = 'x'.repeat(200);
      fireEvent.change(getInput(), { target: { value: text } });

      // Rendered in both desktop and mobile counter variants.
      await waitFor(() => expect(screen.getAllByText(/40\/156/).length).toBeGreaterThan(0));
      expect(mockApi.estimateMcmp).toHaveBeenCalledWith(text, 2);
      // The raw over-budget count is not shown.
      expect(screen.queryAllByText(/200\/156/).length).toBe(0);
    });

    it('does not query the backend when compression is off', () => {
      renderInput({ conversationType: 'contact', mcmpEnabled: false });
      fireEvent.change(getInput(), { target: { value: 'Hello there' } });
      // Raw byte count is shown; no estimate call.
      expect(screen.getByText(/11\/156/)).toBeTruthy();
      expect(mockApi.estimateMcmp).not.toHaveBeenCalled();
    });

    it('does not flash "too long" while the compressed estimate is pending', () => {
      // Estimate never resolves within the test: while pending, a long-but-
      // compressible draft must NOT show the raw over-budget error.
      mockApi.estimateMcmp.mockReturnValue(new Promise(() => {}));
      renderInput({ conversationType: 'contact', mcmpEnabled: true });

      fireEvent.change(getInput(), { target: { value: 'x'.repeat(200) } });

      // Without MCMP this 200-byte draft would be "likely truncated by radio";
      // with MCMP on and the estimate pending, we stay neutral.
      expect(screen.queryAllByText(/likely truncated by radio/).length).toBe(0);
      expect(screen.queryAllByText(/may impact multi-repeater/).length).toBe(0);
    });

    it('does show "too long" once the compressed size itself exceeds the budget', async () => {
      // A genuinely over-budget compressed size must still warn.
      mockApi.estimateMcmp.mockResolvedValue({ wire_bytes: 200, compressed: true });
      renderInput({ conversationType: 'contact', mcmpEnabled: true });

      fireEvent.change(getInput(), { target: { value: 'x'.repeat(400) } });
      await waitFor(() =>
        expect(screen.getAllByText(/likely truncated by radio/).length).toBeGreaterThan(0)
      );
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
});
