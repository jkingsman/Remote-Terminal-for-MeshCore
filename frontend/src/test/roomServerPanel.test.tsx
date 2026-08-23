import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi, type Mock } from 'vitest';

import { RoomServerPanel, resetRoomCacheForTests } from '../components/RoomServerPanel';
import type { Contact } from '../types';

vi.mock('../api', () => ({
  api: {
    roomLogin: vi.fn(),
    roomStatus: vi.fn(),
    roomAcl: vi.fn(),
    roomLppTelemetry: vi.fn(),
    sendRepeaterCommand: vi.fn(),
    getRoomPoll: vi.fn(),
    setRoomPoll: vi.fn(),
    deleteRoomPoll: vi.fn(),
  },
}));

const NO_STORED_CREDENTIAL = {
  room_key: 'aa'.repeat(32),
  has_stored_credential: false,
  is_guest_credential: false,
  poll_enabled: false,
  interval_seconds: 1200,
  last_poll_at: null,
  last_result: null,
  last_error: null,
  consecutive_errors: 0,
};

vi.mock('../components/ui/sonner', () => ({
  toast: Object.assign(vi.fn(), {
    success: vi.fn(),
    error: vi.fn(),
    warning: vi.fn(),
  }),
}));

const { api: _rawApi } = await import('../api');
const mockApi = _rawApi as unknown as Record<string, Mock>;
const { toast } = await import('../components/ui/sonner');
const mockToast = toast as unknown as Record<string, Mock>;

const roomContact: Contact = {
  public_key: 'aa'.repeat(32),
  name: 'Ops Board',
  type: 3,
  flags: 0,
  direct_path: null,
  direct_path_len: -1,
  direct_path_hash_mode: 0,
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

describe('RoomServerPanel', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    localStorage.clear();
    resetRoomCacheForTests();
    // Default: no server-side credential, so the panel shows the login form
    // rather than auto-opening.
    mockApi.getRoomPoll.mockResolvedValue({ ...NO_STORED_CREDENTIAL });
  });

  it('keeps room controls available when login is not confirmed', async () => {
    mockApi.roomLogin.mockResolvedValueOnce({
      status: 'timeout',
      authenticated: false,
      message:
        "No login confirmation was heard from the room server. You're free to try sending messages; try logging in again if authenticated actions fail.",
    });
    const onAuthenticatedChange = vi.fn();

    render(<RoomServerPanel contact={roomContact} onAuthenticatedChange={onAuthenticatedChange} />);

    fireEvent.click(screen.getByText('Login with Existing Access / Guest'));

    await waitFor(() => {
      expect(screen.getByText('Show Tools')).toBeInTheDocument();
    });
    expect(screen.getByText('Show Tools')).toBeInTheDocument();
    expect(screen.getByText('Retry Existing-Access Login')).toBeInTheDocument();
    expect(mockToast.warning).toHaveBeenCalledWith("Couldn't confirm room login", {
      description:
        "No login confirmation was heard from the room server. You're free to try sending messages; try logging in again if authenticated actions fail.",
    });
    expect(onAuthenticatedChange).toHaveBeenLastCalledWith(true);
  });

  it('retains the last password for one-click retry after unlocking the panel', async () => {
    mockApi.roomLogin
      .mockResolvedValueOnce({
        status: 'timeout',
        authenticated: false,
        message: 'No reply heard',
      })
      .mockResolvedValueOnce({
        status: 'ok',
        authenticated: true,
        message: null,
      });

    render(<RoomServerPanel contact={roomContact} />);

    fireEvent.change(screen.getByLabelText('Repeater password'), {
      target: { value: 'secret-room-password' },
    });
    fireEvent.click(screen.getByText('Login with Password'));

    await waitFor(() => {
      expect(screen.getByText('Retry Password Login')).toBeInTheDocument();
    });

    fireEvent.click(screen.getByText('Retry Password Login'));

    await waitFor(() => {
      expect(mockApi.roomLogin).toHaveBeenNthCalledWith(1, roomContact.public_key, {
        password: 'secret-room-password',
      });
      expect(mockApi.roomLogin).toHaveBeenNthCalledWith(2, roomContact.public_key, {
        password: 'secret-room-password',
      });
    });
  });

  it('shows only a success toast after a confirmed login', async () => {
    mockApi.roomLogin.mockResolvedValueOnce({
      status: 'ok',
      authenticated: true,
      message: null,
    });

    render(<RoomServerPanel contact={roomContact} />);

    fireEvent.click(screen.getByText('Login with Existing Access / Guest'));

    await waitFor(() => {
      expect(screen.getByText('Show Tools')).toBeInTheDocument();
    });

    expect(screen.queryByText('Login confirmed by the room server.')).not.toBeInTheDocument();
    expect(screen.queryByText('Retry Password Login')).not.toBeInTheDocument();
    expect(screen.queryByText('Retry Existing-Access Login')).not.toBeInTheDocument();
    expect(mockToast.success).toHaveBeenCalledWith('Login confirmed by the room server.');
  });

  it('auto-opens with the stored credential instead of showing the login form', async () => {
    mockApi.getRoomPoll.mockResolvedValue({
      ...NO_STORED_CREDENTIAL,
      has_stored_credential: true,
      poll_enabled: true,
    });
    mockApi.roomLogin.mockResolvedValueOnce({ status: 'ok', authenticated: true, message: null });

    render(<RoomServerPanel contact={roomContact} />);

    await waitFor(() => {
      expect(screen.getByText('Show Tools')).toBeInTheDocument();
    });
    // Auto-login used the stored credential; the password form never rendered.
    expect(mockApi.roomLogin).toHaveBeenCalledWith(roomContact.public_key, {
      useStoredCredential: true,
    });
    expect(screen.queryByText('Login with Password')).not.toBeInTheDocument();
  });

  it('still captures the credential for sync when the login request errors', async () => {
    // Radio-down: the login request throws, but the panel authenticates
    // optimistically. Enabling sync must still store the entered credential.
    mockApi.roomLogin.mockRejectedValueOnce(new Error('Radio not connected'));
    mockApi.setRoomPoll.mockResolvedValueOnce({
      ...NO_STORED_CREDENTIAL,
      has_stored_credential: true,
      is_guest_credential: true,
      poll_enabled: true,
    });

    render(<RoomServerPanel contact={roomContact} />);
    fireEvent.click(screen.getByText('Login with Existing Access / Guest'));

    // The sync control now lives inside the Tools sheet.
    await waitFor(() => {
      expect(screen.getByText('Show Tools')).toBeInTheDocument();
    });
    fireEvent.click(screen.getByText('Show Tools'));

    await waitFor(() => {
      expect(screen.getByText('Keep this room synced')).toBeInTheDocument();
    });

    fireEvent.click(screen.getByLabelText('Keep this room synced'));

    await waitFor(() => {
      expect(mockApi.setRoomPoll).toHaveBeenCalledWith(roomContact.public_key, {
        enabled: true,
        credential_action: 'set',
        credential: '',
      });
    });
  });

  it('stores a guest ("") credential when enabling sync after a guest login', async () => {
    mockApi.roomLogin.mockResolvedValueOnce({ status: 'ok', authenticated: true, message: null });
    mockApi.setRoomPoll.mockResolvedValueOnce({
      ...NO_STORED_CREDENTIAL,
      has_stored_credential: true,
      is_guest_credential: true,
      poll_enabled: true,
    });

    render(<RoomServerPanel contact={roomContact} />);
    fireEvent.click(screen.getByText('Login with Existing Access / Guest'));

    // The sync control now lives inside the Tools sheet.
    await waitFor(() => {
      expect(screen.getByText('Show Tools')).toBeInTheDocument();
    });
    fireEvent.click(screen.getByText('Show Tools'));

    await waitFor(() => {
      expect(screen.getByText('Keep this room synced')).toBeInTheDocument();
    });

    fireEvent.click(screen.getByLabelText('Keep this room synced'));

    await waitFor(() => {
      // "" is a real guest credential, stored via credential_action 'set' — not
      // treated as "no credential".
      expect(mockApi.setRoomPoll).toHaveBeenCalledWith(roomContact.public_key, {
        enabled: true,
        credential_action: 'set',
        credential: '',
      });
    });
  });
});
