import { useCallback, useEffect, useMemo, useRef, useState } from 'react';

import { api } from '../api';
import { toast } from './ui/sonner';
import { Button } from './ui/button';
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from './ui/sheet';
import type {
  Contact,
  PaneState,
  RepeaterAclResponse,
  RepeaterLppTelemetryResponse,
  RepeaterStatusResponse,
  RoomPollStatus,
} from '../types';
import { TelemetryPane } from './repeater/RepeaterTelemetryPane';
import { AclPane } from './repeater/RepeaterAclPane';
import { LppTelemetryPane } from './repeater/RepeaterLppTelemetryPane';
import { ConsolePane } from './repeater/RepeaterConsolePane';
import { RepeaterLogin } from './RepeaterLogin';
import { ServerLoginStatusBanner } from './ServerLoginStatusBanner';
import { useRememberedServerPassword } from '../hooks/useRememberedServerPassword';
import {
  buildServerLoginAttemptFromError,
  buildServerLoginAttemptFromResponse,
  type ServerLoginAttemptState,
} from '../utils/serverLoginState';

interface RoomServerPanelProps {
  contact: Contact;
  onAuthenticatedChange?: (authenticated: boolean) => void;
}

type RoomPaneKey = 'status' | 'acl' | 'lppTelemetry';

type RoomPaneData = {
  status: RepeaterStatusResponse | null;
  acl: RepeaterAclResponse | null;
  lppTelemetry: RepeaterLppTelemetryResponse | null;
};

type RoomPaneStates = Record<RoomPaneKey, PaneState>;

type ConsoleEntry = {
  command: string;
  response: string;
  timestamp: number;
  outgoing: boolean;
};

const INITIAL_PANE_STATE: PaneState = {
  loading: false,
  attempt: 0,
  error: null,
  fetched_at: null,
};

function createInitialPaneStates(): RoomPaneStates {
  return {
    status: { ...INITIAL_PANE_STATE },
    acl: { ...INITIAL_PANE_STATE },
    lppTelemetry: { ...INITIAL_PANE_STATE },
  };
}

function createInitialPaneData(): RoomPaneData {
  return { status: null, acl: null, lppTelemetry: null };
}

// ---------------------------------------------------------------------------
// In-memory LRU cache so room login state survives conversation switches
// ---------------------------------------------------------------------------

interface RoomCacheEntry {
  authenticated: boolean;
  loginError: string | null;
  lastLoginAttempt: ServerLoginAttemptState | null;
  paneData: RoomPaneData;
  paneStates: RoomPaneStates;
  consoleHistory: ConsoleEntry[];
}

const MAX_CACHED_ROOMS = 8;
const roomCache = new Map<string, RoomCacheEntry>();

function getCachedRoom(publicKey: string): RoomCacheEntry | null {
  const cached = roomCache.get(publicKey);
  if (!cached) return null;
  // Touch for LRU
  roomCache.delete(publicKey);
  roomCache.set(publicKey, cached);
  return {
    ...cached,
    paneData: { ...cached.paneData },
    paneStates: {
      status: { ...cached.paneStates.status, loading: false },
      acl: { ...cached.paneStates.acl, loading: false },
      lppTelemetry: { ...cached.paneStates.lppTelemetry, loading: false },
    },
    consoleHistory: cached.consoleHistory.map((e) => ({ ...e })),
  };
}

function setCachedRoom(publicKey: string, entry: RoomCacheEntry) {
  roomCache.delete(publicKey);
  roomCache.set(publicKey, {
    ...entry,
    paneData: { ...entry.paneData },
    paneStates: {
      status: { ...entry.paneStates.status, loading: false },
      acl: { ...entry.paneStates.acl, loading: false },
      lppTelemetry: { ...entry.paneStates.lppTelemetry, loading: false },
    },
    consoleHistory: entry.consoleHistory.map((e) => ({ ...e })),
  });
  if (roomCache.size > MAX_CACHED_ROOMS) {
    const lruKey = roomCache.keys().next().value as string | undefined;
    if (lruKey) roomCache.delete(lruKey);
  }
}

export function resetRoomCacheForTests() {
  roomCache.clear();
}

export function RoomServerPanel({ contact, onAuthenticatedChange }: RoomServerPanelProps) {
  const { password, setPassword, rememberPassword, setRememberPassword, persistAfterLogin } =
    useRememberedServerPassword('room', contact.public_key);

  const cached = useMemo(() => getCachedRoom(contact.public_key), [contact.public_key]);

  const [loginLoading, setLoginLoading] = useState(false);
  const [loginError, setLoginError] = useState<string | null>(cached?.loginError ?? null);
  const [authenticated, setAuthenticated] = useState(cached?.authenticated ?? false);
  const [lastLoginAttempt, setLastLoginAttempt] = useState<ServerLoginAttemptState | null>(
    cached?.lastLoginAttempt ?? null
  );
  const [advancedOpen, setAdvancedOpen] = useState(false);
  const [paneData, setPaneData] = useState<RoomPaneData>(cached?.paneData ?? createInitialPaneData);
  const [paneStates, setPaneStates] = useState<RoomPaneStates>(
    cached?.paneStates ?? createInitialPaneStates
  );
  const [consoleHistory, setConsoleHistory] = useState<ConsoleEntry[]>(
    cached?.consoleHistory ?? []
  );
  const [consoleLoading, setConsoleLoading] = useState(false);

  // Room-sync (background poller) state.
  const [pollStatus, setPollStatus] = useState<RoomPollStatus | null>(null);
  const [syncSaving, setSyncSaving] = useState(false);
  const [autoOpening, setAutoOpening] = useState(false);
  // The plaintext credential from the current interactive login, so enabling
  // sync can store it. null when we auto-opened (the server already has it) or
  // before any login this session. "" is a valid guest credential — never
  // collapse it to "no credential".
  const [sessionCredential, setSessionCredential] = useState<string | null>(null);
  // Guard: attempt the stored-credential auto-login at most once per room.
  const autoLoginTriedRef = useRef<string | null>(null);

  // Persist to cache on every state change
  useEffect(() => {
    setCachedRoom(contact.public_key, {
      authenticated,
      loginError,
      lastLoginAttempt,
      paneData,
      paneStates,
      consoleHistory,
    });
  }, [
    contact.public_key,
    authenticated,
    loginError,
    lastLoginAttempt,
    paneData,
    paneStates,
    consoleHistory,
  ]);

  useEffect(() => {
    onAuthenticatedChange?.(authenticated);
  }, [authenticated, onAuthenticatedChange]);

  const refreshPane = useCallback(
    async <K extends RoomPaneKey>(pane: K, loader: () => Promise<RoomPaneData[K]>) => {
      setPaneStates((prev) => ({
        ...prev,
        [pane]: {
          ...prev[pane],
          loading: true,
          attempt: prev[pane].attempt + 1,
          error: null,
        },
      }));

      try {
        const data = await loader();
        setPaneData((prev) => ({ ...prev, [pane]: data }));
        setPaneStates((prev) => ({
          ...prev,
          [pane]: {
            loading: false,
            attempt: prev[pane].attempt,
            error: null,
            fetched_at: Date.now(),
          },
        }));
      } catch (err) {
        setPaneStates((prev) => ({
          ...prev,
          [pane]: {
            ...prev[pane],
            loading: false,
            error: err instanceof Error ? err.message : 'Unknown error',
          },
        }));
      }
    },
    []
  );

  const performLogin = useCallback(
    async (nextPassword: string, method: 'password' | 'blank') => {
      if (loginLoading) return;

      setLoginLoading(true);
      setLoginError(null);
      try {
        const result = await api.roomLogin(contact.public_key, { password: nextPassword });
        // Remember the credential this session so "keep synced" can store it.
        setSessionCredential(nextPassword);
        setLastLoginAttempt(buildServerLoginAttemptFromResponse(method, result, 'room server'));
        setAuthenticated(true);
        if (result.authenticated) {
          toast.success('Login confirmed by the room server.');
        } else {
          toast.warning("Couldn't confirm room login", {
            description:
              result.message ??
              'No confirmation came back from the room server. You can still open tools and try again.',
          });
        }
      } catch (err) {
        const message = err instanceof Error ? err.message : 'Unknown error';
        setLastLoginAttempt(buildServerLoginAttemptFromError(method, message, 'room server'));
        setAuthenticated(true);
        setLoginError(message);
        toast.error('Room login request failed', {
          description: `${message}. You can still open tools and retry the login from here.`,
        });
      } finally {
        setLoginLoading(false);
      }
    },
    [contact.public_key, loginLoading]
  );

  const handleLogin = useCallback(
    async (nextPassword: string) => {
      await performLogin(nextPassword, 'password');
      persistAfterLogin(nextPassword);
    },
    [performLogin, persistAfterLogin]
  );

  const handleLoginAsGuest = useCallback(async () => {
    await performLogin('', 'blank');
    persistAfterLogin('');
  }, [performLogin, persistAfterLogin]);

  const attemptStoredLogin = useCallback(async () => {
    setAutoOpening(true);
    setLoginError(null);
    try {
      const result = await api.roomLogin(contact.public_key, { useStoredCredential: true });
      setLastLoginAttempt(buildServerLoginAttemptFromResponse('password', result, 'room server'));
      setAuthenticated(true);
      if (!result.authenticated) {
        toast.warning("Couldn't confirm room login", {
          description:
            result.message ?? 'The room server did not confirm the saved credential.',
        });
      }
    } catch (err) {
      // Stored credential failed (e.g. the server-side password changed) — fall
      // back to the manual login form rather than leaving the user stuck.
      setLoginError(err instanceof Error ? err.message : 'Unknown error');
    } finally {
      setAutoOpening(false);
    }
  }, [contact.public_key]);

  // On opening a room, load its poll/credential status; if a credential is
  // stored (password OR guest), auto-open by logging in with it — no prompt.
  useEffect(() => {
    let cancelled = false;
    autoLoginTriedRef.current = null;
    setPollStatus(null);
    api
      .getRoomPoll(contact.public_key)
      .then((status) => {
        if (cancelled) return;
        setPollStatus(status);
        if (
          status.has_stored_credential &&
          !authenticated &&
          autoLoginTriedRef.current !== contact.public_key
        ) {
          autoLoginTriedRef.current = contact.public_key;
          void attemptStoredLogin();
        }
      })
      .catch(() => {
        // No status or unreachable — fall through to the manual login form.
      });
    return () => {
      cancelled = true;
    };
    // Intentionally keyed on the room only: re-running on auth/callback changes
    // would re-fetch and risk a second auto-login.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [contact.public_key]);

  const handleToggleSync = useCallback(
    async (enabled: boolean) => {
      setSyncSaving(true);
      try {
        let status: RoomPollStatus;
        if (enabled && !pollStatus?.has_stored_credential) {
          // Polling needs a stored credential; save the one from this session.
          // sessionCredential === "" is a valid guest credential, so test for
          // null explicitly rather than falsiness.
          if (sessionCredential === null) {
            toast.error('Log in to this room first so its credential can be saved for syncing.');
            return;
          }
          status = await api.setRoomPoll(contact.public_key, {
            enabled: true,
            credential_action: 'set',
            credential: sessionCredential,
          });
        } else {
          status = await api.setRoomPoll(contact.public_key, { enabled });
        }
        setPollStatus(status);
      } catch (err) {
        toast.error('Failed to update room sync', {
          description: err instanceof Error ? err.message : undefined,
        });
      } finally {
        setSyncSaving(false);
      }
    },
    [contact.public_key, pollStatus, sessionCredential]
  );

  const handleIntervalChange = useCallback(
    async (minutes: number) => {
      setSyncSaving(true);
      try {
        const status = await api.setRoomPoll(contact.public_key, {
          interval_seconds: Math.max(5, minutes) * 60,
        });
        setPollStatus(status);
      } catch (err) {
        toast.error('Failed to update sync interval', {
          description: err instanceof Error ? err.message : undefined,
        });
      } finally {
        setSyncSaving(false);
      }
    },
    [contact.public_key]
  );

  const handleForgetCredential = useCallback(async () => {
    setSyncSaving(true);
    try {
      const status = await api.deleteRoomPoll(contact.public_key);
      setPollStatus(status);
      toast.success('Saved room credential removed.');
    } catch (err) {
      toast.error('Failed to remove saved credential', {
        description: err instanceof Error ? err.message : undefined,
      });
    } finally {
      setSyncSaving(false);
    }
  }, [contact.public_key]);

  const handleConsoleCommand = useCallback(
    async (command: string) => {
      setConsoleLoading(true);
      const timestamp = Date.now();
      setConsoleHistory((prev) => [
        ...prev,
        { command, response: command, timestamp, outgoing: true },
      ]);
      try {
        const response = await api.sendRepeaterCommand(contact.public_key, command);
        setConsoleHistory((prev) => [
          ...prev,
          {
            command,
            response: response.response,
            timestamp: Date.now(),
            outgoing: false,
          },
        ]);
      } catch (err) {
        const message = err instanceof Error ? err.message : 'Unknown error';
        setConsoleHistory((prev) => [
          ...prev,
          {
            command,
            response: `(error) ${message}`,
            timestamp: Date.now(),
            outgoing: false,
          },
        ]);
      } finally {
        setConsoleLoading(false);
      }
    },
    [contact.public_key]
  );

  const panelTitle = useMemo(() => contact.name || contact.public_key.slice(0, 12), [contact]);
  const showLoginFailureState =
    lastLoginAttempt !== null && lastLoginAttempt.outcome !== 'confirmed';

  if (!authenticated) {
    if (autoOpening) {
      return (
        <div className="flex-1 overflow-y-auto p-4">
          <div className="mx-auto flex w-full max-w-sm flex-col items-center gap-3 py-8 text-center">
            <p className="text-sm text-muted-foreground">
              Connecting to {panelTitle} with the saved credential…
            </p>
          </div>
        </div>
      );
    }
    return (
      <div className="flex-1 overflow-y-auto p-4">
        <div className="mx-auto flex w-full max-w-sm flex-col gap-4">
          <div className="rounded-md border border-warning/30 bg-warning/10 px-4 py-3 text-sm text-warning">
            Room server access is experimental and in public alpha. Please report any issues on{' '}
            <a
              href="https://github.com/jkingsman/Remote-Terminal-for-MeshCore/issues"
              target="_blank"
              rel="noreferrer"
              className="font-medium underline underline-offset-2 hover:text-warning/80"
            >
              GitHub
            </a>
            .
          </div>
          <RepeaterLogin
            repeaterName={panelTitle}
            loading={loginLoading}
            error={loginError}
            password={password}
            onPasswordChange={setPassword}
            rememberPassword={rememberPassword}
            onRememberPasswordChange={setRememberPassword}
            onLogin={handleLogin}
            onLoginAsGuest={handleLoginAsGuest}
            description="Log in with the room password or use ACL/guest access to enter this room server"
            passwordPlaceholder="Room server password..."
            guestLabel="Login with Existing Access / Guest"
          />
        </div>
      </div>
    );
  }

  return (
    <section className="border-b border-border bg-muted/20 px-4 py-3">
      <div className="space-y-3">
        {showLoginFailureState ? (
          <ServerLoginStatusBanner
            attempt={lastLoginAttempt}
            loading={loginLoading}
            canRetryPassword={password.trim().length > 0}
            onRetryPassword={() => handleLogin(password)}
            onRetryBlank={handleLoginAsGuest}
            blankRetryLabel="Retry Existing-Access Login"
            showRetryActions={false}
          />
        ) : null}
        <div className="flex flex-wrap items-center justify-between gap-2">
          {showLoginFailureState ? (
            <div className="flex flex-wrap gap-2">
              <Button
                type="button"
                variant="outline"
                size="sm"
                onClick={() => void handleLogin(password)}
                disabled={loginLoading || password.trim().length === 0}
              >
                Retry Password Login
              </Button>
              <Button
                type="button"
                variant="outline"
                size="sm"
                onClick={handleLoginAsGuest}
                disabled={loginLoading}
              >
                Retry Existing-Access Login
              </Button>
            </div>
          ) : (
            <div />
          )}
          <Button
            type="button"
            variant="outline"
            size="sm"
            onClick={() => setAdvancedOpen((prev) => !prev)}
          >
            {advancedOpen ? 'Hide Tools' : 'Show Tools'}
          </Button>
        </div>
        <div className="rounded-md border border-border bg-background/40 px-3 py-2.5">
          <label className="flex cursor-pointer items-center gap-2.5">
            <input
              type="checkbox"
              checked={pollStatus?.poll_enabled ?? false}
              disabled={syncSaving}
              onChange={(e) => void handleToggleSync(e.target.checked)}
              className="h-4 w-4 rounded border-input accent-primary"
            />
            <span className="text-[0.8125rem] font-medium">Keep this room synced</span>
          </label>
          <p className="mt-1 text-[0.8125rem] text-muted-foreground">
            Periodically logs in with the saved credential to pull new room messages while
            you&apos;re away. The room credential is stored on this server.
          </p>
          {pollStatus?.poll_enabled && (
            <div className="mt-2 flex flex-wrap items-center gap-x-2 gap-y-1 text-xs text-muted-foreground">
              <label className="flex items-center gap-1.5">
                Every
                <input
                  key={pollStatus.interval_seconds}
                  type="number"
                  min={5}
                  defaultValue={Math.round((pollStatus.interval_seconds ?? 1200) / 60)}
                  disabled={syncSaving}
                  onBlur={(e) => void handleIntervalChange(Number(e.target.value))}
                  className="h-7 w-16 rounded-md border border-input bg-transparent px-2 text-xs"
                  aria-label="Sync interval in minutes"
                />
                min
              </label>
              {pollStatus.last_poll_at ? (
                <span>
                  · last synced{' '}
                  {new Date(pollStatus.last_poll_at * 1000).toLocaleTimeString([], {
                    hour: '2-digit',
                    minute: '2-digit',
                  })}
                </span>
              ) : null}
              {pollStatus.last_error ? (
                <span className="text-destructive">· {pollStatus.last_error}</span>
              ) : null}
            </div>
          )}
          {pollStatus?.has_stored_credential ? (
            <button
              type="button"
              onClick={() => void handleForgetCredential()}
              disabled={syncSaving}
              className="mt-2 text-xs text-muted-foreground underline underline-offset-2 hover:text-foreground"
            >
              Forget saved credential
            </button>
          ) : null}
        </div>
      </div>
      <Sheet open={advancedOpen} onOpenChange={setAdvancedOpen}>
        <SheetContent side="right" className="w-full sm:max-w-4xl p-0 flex flex-col">
          <SheetHeader className="sr-only">
            <SheetTitle>Room Server Tools</SheetTitle>
            <SheetDescription>
              Room server telemetry, ACL tools, sensor data, and CLI console
            </SheetDescription>
          </SheetHeader>
          <div className="border-b border-border px-4 py-3 pr-14">
            <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
              <div className="min-w-0">
                <h2 className="truncate text-base font-semibold">Room Server Tools</h2>
                <p className="text-sm text-muted-foreground">{panelTitle}</p>
              </div>
            </div>
          </div>
          <div className="flex-1 overflow-y-auto p-4">
            <div className="grid gap-3 xl:grid-cols-2">
              <TelemetryPane
                data={paneData.status}
                state={paneStates.status}
                onRefresh={() => refreshPane('status', () => api.roomStatus(contact.public_key))}
              />
              <AclPane
                data={paneData.acl}
                state={paneStates.acl}
                onRefresh={() => refreshPane('acl', () => api.roomAcl(contact.public_key))}
              />
              <LppTelemetryPane
                data={paneData.lppTelemetry}
                state={paneStates.lppTelemetry}
                onRefresh={() =>
                  refreshPane('lppTelemetry', () => api.roomLppTelemetry(contact.public_key))
                }
              />
              <ConsolePane
                history={consoleHistory}
                loading={consoleLoading}
                onSend={handleConsoleCommand}
              />
            </div>
          </div>
        </SheetContent>
      </Sheet>
    </section>
  );
}
