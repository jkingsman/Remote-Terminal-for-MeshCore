import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { useState } from 'react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { MessageList } from '../components/MessageList';
import { api } from '../api';
import { PathHopWidthProvider } from '../contexts/PathHopWidthContext';
import { CONTACT_TYPE_ROOM, type Contact, type Message } from '../types';

const scrollIntoViewMock = vi.fn();
const originalGetBoundingClientRect = HTMLElement.prototype.getBoundingClientRect;

function createMessage(overrides: Partial<Message> = {}): Message {
  return {
    id: 1,
    type: 'CHAN',
    conversation_key: 'C3B889530D4F02DB5662EA13C417F530',
    text: 'Alice: hello world',
    sender_timestamp: 1700000000,
    received_at: 1700000001,
    paths: null,
    txt_type: 0,
    signature: null,
    sender_key: null,
    outgoing: false,
    acked: 0,
    sender_name: null,
    ...overrides,
  };
}

describe('MessageList channel sender rendering', () => {
  beforeEach(() => {
    scrollIntoViewMock.mockReset();
    Object.defineProperty(HTMLElement.prototype, 'scrollIntoView', {
      configurable: true,
      value: scrollIntoViewMock,
      writable: true,
    });
    Object.defineProperty(HTMLElement.prototype, 'getBoundingClientRect', {
      configurable: true,
      value: originalGetBoundingClientRect,
      writable: true,
    });
  });

  it('renders explicit corrupt placeholder and warning avatar for unnamed corrupt channel packets', () => {
    render(
      <MessageList
        messages={[
          createMessage({
            text: "Nv\x0ek\x16ɩ'\x7fg:",
            sender_name: null,
            sender_key: null,
          }),
        ]}
        contacts={[]}
        loading={false}
      />
    );

    expect(screen.getByText('<No name -- corrupt packet?>')).toBeInTheDocument();
    expect(screen.getByTestId('corrupt-avatar')).toBeInTheDocument();
  });

  it('renders a region badge for region-scoped channel messages', () => {
    render(
      <MessageList
        messages={[createMessage({ sender_name: 'Alice', region: 'nl-gr' })]}
        contacts={[]}
        loading={false}
      />
    );

    expect(screen.getByText('nl-gr')).toBeInTheDocument();
    expect(screen.getByTitle('Regional scope: nl-gr')).toBeInTheDocument();
  });

  it('does not render a region badge for unscoped messages', () => {
    render(
      <MessageList
        messages={[createMessage({ sender_name: 'Alice', region: null })]}
        contacts={[]}
        loading={false}
      />
    );

    expect(screen.queryByText('nl-gr')).not.toBeInTheDocument();
  });

  it('shows per-hop byte width in the path badge when the toggle is on', () => {
    render(
      <PathHopWidthProvider showPathHopWidth setShowPathHopWidth={() => {}}>
        <MessageList
          messages={[
            createMessage({
              sender_name: 'Alice',
              // 8 hex chars over 2 hops = 2 bytes/hop.
              paths: [{ path: 'AABBCCDD', path_len: 2, received_at: 1700000001 }],
            }),
          ]}
          contacts={[]}
          loading={false}
        />
      </PathHopWidthProvider>
    );

    expect(screen.getByText('(2 · 2B)')).toBeInTheDocument();
    expect(screen.getByTitle('View message path (2B per hop)')).toBeInTheDocument();
  });

  it('hides the width by default (toggle off) and shows only the hop count', () => {
    render(
      <MessageList
        messages={[
          createMessage({
            sender_name: 'Alice',
            paths: [{ path: 'AABBCCDD', path_len: 2, received_at: 1700000001 }],
          }),
        ]}
        contacts={[]}
        loading={false}
      />
    );

    expect(screen.getByText('(2)')).toBeInTheDocument();
    expect(screen.queryByText('(2 · 2B)')).not.toBeInTheDocument();
    expect(screen.getByTitle('View message path')).toBeInTheDocument();
  });

  it('omits the width for direct (0-hop) paths even when the toggle is on', () => {
    render(
      <PathHopWidthProvider showPathHopWidth setShowPathHopWidth={() => {}}>
        <MessageList
          messages={[
            createMessage({
              sender_name: 'Alice',
              paths: [{ path: '', path_len: 0, received_at: 1700000001 }],
            }),
          ]}
          contacts={[]}
          loading={false}
        />
      </PathHopWidthProvider>
    );

    expect(screen.getByText('(d)')).toBeInTheDocument();
    expect(screen.getByTitle('View message path')).toBeInTheDocument();
  });

  it('prefers stored sender_name for channel messages even when text is not sender-prefixed', () => {
    render(
      <MessageList
        messages={[
          createMessage({
            text: 'garbled payload with no sender prefix',
            sender_name: 'Alice',
            sender_key: 'ab'.repeat(32),
          }),
        ]}
        contacts={[]}
        loading={false}
      />
    );

    expect(screen.getByText('Alice')).toBeInTheDocument();
    expect(screen.getByText('A')).toBeInTheDocument();
  });

  it('renders room-server DM messages using stored sender attribution instead of the room contact', () => {
    const roomContact: Contact = {
      public_key: 'ab'.repeat(32),
      name: 'Ops Board',
      type: CONTACT_TYPE_ROOM,
      flags: 0,
      direct_path: null,
      direct_path_len: -1,
      direct_path_hash_mode: -1,
      last_advert: null,
      lat: null,
      lon: null,
      last_seen: null,
      on_radio: false,
      favorite: false,
      last_contacted: null,
      last_read_at: null,
      first_seen: null,
    };

    render(
      <MessageList
        messages={[
          createMessage({
            type: 'PRIV',
            conversation_key: roomContact.public_key,
            text: 'status update: ready',
            sender_name: 'Alice',
            sender_key: '12'.repeat(32),
          }),
        ]}
        contacts={[roomContact]}
        loading={false}
      />
    );

    expect(screen.getByText('Alice')).toBeInTheDocument();
    expect(screen.queryByText('Ops Board')).not.toBeInTheDocument();
    expect(screen.getByText('status update: ready')).toBeInTheDocument();
  });

  it('gives clickable sender avatars an accessible label', () => {
    render(
      <MessageList
        messages={[
          createMessage({
            text: 'garbled payload with no sender prefix',
            sender_name: 'Alice',
            sender_key: 'ab'.repeat(32),
          }),
        ]}
        contacts={[]}
        loading={false}
        onOpenContactInfo={() => {}}
      />
    );

    expect(screen.getByRole('button', { name: 'View info for Alice' })).toBeInTheDocument();
  });

  it('renders valid channel references as clickable links and ignores invalid ones', async () => {
    const user = userEvent.setup();
    const onChannelReferenceClick = vi.fn();

    render(
      <MessageList
        messages={[
          createMessage({
            text: 'Alice: Join #mesh-room now skip #bad--room and visit https://example.com/#also-skip',
          }),
        ]}
        contacts={[]}
        loading={false}
        onChannelReferenceClick={onChannelReferenceClick}
      />
    );

    const linkedChannel = screen.getByRole('button', { name: '#mesh-room' });
    expect(linkedChannel).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '#bad--room' })).not.toBeInTheDocument();
    expect(
      screen.getByRole('link', { name: 'https://example.com/#also-skip' })
    ).toBeInTheDocument();

    await user.click(linkedChannel);

    expect(onChannelReferenceClick).toHaveBeenCalledWith('#mesh-room');
  });

  it('links valid channel references when followed by clause punctuation', async () => {
    const user = userEvent.setup();
    const onChannelReferenceClick = vi.fn();

    render(
      <MessageList
        messages={[
          createMessage({
            text: 'Alice: Check #mesh-room, then #ops-room; then #alpha-room.',
          }),
        ]}
        contacts={[]}
        loading={false}
        onChannelReferenceClick={onChannelReferenceClick}
      />
    );

    await user.click(screen.getByRole('button', { name: '#mesh-room' }));
    await user.click(screen.getByRole('button', { name: '#ops-room' }));
    await user.click(screen.getByRole('button', { name: '#alpha-room' }));

    expect(onChannelReferenceClick).toHaveBeenNthCalledWith(1, '#mesh-room');
    expect(onChannelReferenceClick).toHaveBeenNthCalledWith(2, '#ops-room');
    expect(onChannelReferenceClick).toHaveBeenNthCalledWith(3, '#alpha-room');
  });

  it('links valid channel references in direct messages too', async () => {
    const user = userEvent.setup();
    const onChannelReferenceClick = vi.fn();

    render(
      <MessageList
        messages={[
          createMessage({
            type: 'PRIV',
            text: 'check #ops-room',
            conversation_key: 'ab'.repeat(32),
          }),
        ]}
        contacts={[]}
        loading={false}
        onChannelReferenceClick={onChannelReferenceClick}
      />
    );

    await user.click(screen.getByRole('button', { name: '#ops-room' }));

    expect(onChannelReferenceClick).toHaveBeenCalledWith('#ops-room');
  });

  it('does not strip colon-prefixed text in direct messages (issue #198)', () => {
    render(
      <MessageList
        messages={[
          createMessage({
            type: 'PRIV',
            conversation_key: 'ab'.repeat(32),
            text: 'TEST1: TEST2',
          }),
        ]}
        contacts={[]}
        loading={false}
      />
    );

    expect(screen.getByText('TEST1: TEST2')).toBeInTheDocument();
  });

  it('offers a jump instead of a divider when the unread boundary is not loaded', async () => {
    const user = userEvent.setup();
    const onNavigateToUnread = vi.fn();
    // Boundary id 999 is not among the loaded messages: the real first-unread is
    // further back than this window. The divider must not be invented at the top.
    render(
      <MessageList
        messages={[
          createMessage({ id: 1, received_at: 1700000001, text: 'Alice: older' }),
          createMessage({ id: 2, received_at: 1700000010, text: 'Alice: newer' }),
        ]}
        contacts={[]}
        loading={false}
        unreadMarkerMessageId={999}
        onNavigateToUnread={onNavigateToUnread}
      />
    );

    expect(screen.queryByText('Unread messages')).not.toBeInTheDocument();

    const jump = await screen.findByRole('button', { name: 'Jump to unread' });
    await user.click(jump);

    // Hands off to the jump-to-message path rather than scrolling to a wrong row.
    expect(onNavigateToUnread).toHaveBeenCalledWith(999);
  });

  it('shows no unread affordance at all when nothing is unread', () => {
    render(
      <MessageList
        messages={[createMessage({ id: 1, received_at: 1700000001, text: 'Alice: hi' })]}
        contacts={[]}
        loading={false}
        unreadMarkerMessageId={null}
      />
    );

    expect(screen.queryByText('Unread messages')).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Jump to unread' })).not.toBeInTheDocument();
  });

  it('renders and dismisses an unread marker at the first unread message boundary', async () => {
    const user = userEvent.setup();
    const messages = [
      createMessage({ id: 1, received_at: 1700000001, text: 'Alice: older' }),
      createMessage({ id: 2, received_at: 1700000010, text: 'Alice: newer' }),
    ];

    function DismissibleUnreadMarkerList() {
      const [unreadMarkerMessageId, setUnreadMarkerMessageId] = useState<number | undefined>(2);

      return (
        <MessageList
          messages={messages}
          contacts={[]}
          loading={false}
          unreadMarkerMessageId={unreadMarkerMessageId}
          onDismissUnreadMarker={() => setUnreadMarkerMessageId(undefined)}
        />
      );
    }

    render(<DismissibleUnreadMarkerList />);

    const marker = screen.getByRole('button', { name: /Unread messages/i });
    expect(marker).toBeInTheDocument();
    expect(screen.getByText('older')).toBeInTheDocument();
    expect(screen.getByText('newer')).toBeInTheDocument();

    await user.click(marker);

    expect(screen.queryByRole('button', { name: /Unread messages/i })).not.toBeInTheDocument();
  });

  it('shows a jump-to-unread button and dismisses it after use without hiding the marker', async () => {
    const user = userEvent.setup();
    const messages = [
      createMessage({ id: 1, received_at: 1700000001, text: 'Alice: older' }),
      createMessage({ id: 2, received_at: 1700000010, text: 'Alice: newer' }),
    ];

    render(
      <MessageList messages={messages} contacts={[]} loading={false} unreadMarkerMessageId={2} />
    );

    const jumpButton = screen.getByRole('button', { name: 'Jump to unread' });
    expect(jumpButton).toBeInTheDocument();
    expect(screen.getByText('Unread messages')).toBeInTheDocument();

    await user.click(jumpButton);

    expect(screen.queryByRole('button', { name: 'Jump to unread' })).not.toBeInTheDocument();
    expect(screen.getByText('Unread messages')).toBeInTheDocument();
    expect(scrollIntoViewMock).toHaveBeenCalled();
  });

  it('lets the user dismiss the jump-to-unread button without scrolling or hiding the marker', async () => {
    const user = userEvent.setup();
    const messages = [
      createMessage({ id: 1, received_at: 1700000001, text: 'Alice: older' }),
      createMessage({ id: 2, received_at: 1700000010, text: 'Alice: newer' }),
    ];

    render(
      <MessageList messages={messages} contacts={[]} loading={false} unreadMarkerMessageId={2} />
    );

    await user.click(screen.getByRole('button', { name: 'Dismiss jump to unread' }));

    expect(screen.queryByRole('button', { name: 'Jump to unread' })).not.toBeInTheDocument();
    expect(screen.getByText('Unread messages')).toBeInTheDocument();
    expect(scrollIntoViewMock).not.toHaveBeenCalled();
  });

  it('hides the jump-to-unread button when the unread marker is already visible', () => {
    Object.defineProperty(HTMLElement.prototype, 'getBoundingClientRect', {
      configurable: true,
      writable: true,
      value: function () {
        const element = this as HTMLElement;
        if (element.textContent?.includes('Unread messages')) {
          return {
            top: 200,
            bottom: 240,
            left: 0,
            right: 300,
            width: 300,
            height: 40,
            x: 0,
            y: 200,
            toJSON: () => '',
          };
        }
        if (element.className.includes('overflow-y-auto')) {
          return {
            top: 100,
            bottom: 500,
            left: 0,
            right: 400,
            width: 400,
            height: 400,
            x: 0,
            y: 100,
            toJSON: () => '',
          };
        }
        return {
          top: 0,
          bottom: 0,
          left: 0,
          right: 0,
          width: 0,
          height: 0,
          x: 0,
          y: 0,
          toJSON: () => '',
        };
      },
    });

    const messages = [
      createMessage({ id: 1, received_at: 1700000001, text: 'Alice: older' }),
      createMessage({ id: 2, received_at: 1700000010, text: 'Alice: newer' }),
    ];

    render(
      <MessageList messages={messages} contacts={[]} loading={false} unreadMarkerMessageId={2} />
    );

    expect(screen.getByText('Unread messages')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Jump to unread' })).not.toBeInTheDocument();
  });
  it('mounts only a window of rows for a long history', () => {
    const messages = Array.from({ length: 500 }, (_, i) =>
      createMessage({
        id: i + 1,
        text: `Alice: message ${i}`,
        sender_timestamp: 1700000000 + i,
        received_at: 1700000001 + i,
      })
    );

    const { container } = render(<MessageList messages={messages} contacts={[]} loading={false} />);

    // jsdom reports no layout, so the list falls back to a nominal viewport. The point
    // is that the window is bounded: a 500-message history must not mount 500 rows.
    const mounted = container.querySelectorAll('[data-message-id]').length;
    expect(mounted).toBeGreaterThan(0);
    expect(mounted).toBeLessThan(100);
  });
});

describe('MessageList image messages', () => {
  it('renders a completed outgoing IE4 image immediately', () => {
    render(
      <MessageList
        messages={[createMessage({ text: 'IE4:a:0:e:74:4r:1mc', outgoing: true })]}
        contacts={[]}
        loading={false}
      />
    );

    expect(screen.getByAltText('Image message')).toHaveAttribute(
      'src',
      './api/images/sessions/0000000a/content'
    );
  });

  it('shows tap-to-load progress and renders the completed image', async () => {
    const fetchSpy = vi.spyOn(api, 'fetchImage').mockResolvedValue({
      session_id: '0000000a',
      state: 'receiving',
      format: 0,
      width: 256,
      height: 171,
      size_bytes: 2100,
      fragment_count: 14,
      received_count: 4,
      missing_indices: [],
    });
    vi.spyOn(api, 'getImageSession').mockResolvedValue({
      session_id: '0000000a',
      state: 'complete',
      format: 0,
      width: 256,
      height: 171,
      size_bytes: 2100,
      fragment_count: 14,
      received_count: 14,
      missing_indices: [],
    });
    render(
      <MessageList
        messages={[createMessage({ text: 'IE4:a:0:e:74:4r:1mc', sender_name: 'Alice' })]}
        contacts={[]}
        loading={false}
      />
    );
    expect(screen.getByText('Tap to load')).toBeVisible();
    fireEvent.click(screen.getByRole('button', { name: 'Load image' }));
    expect(await screen.findByText('4/14')).toBeVisible();
    await waitFor(() => expect(screen.getByAltText('Image message')).toBeVisible(), {
      timeout: 2000,
    });
    expect(fetchSpy).toHaveBeenCalledWith(1);
  });
});
