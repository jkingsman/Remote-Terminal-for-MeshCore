# Frontend CLAUDE.md

This document provides context for AI assistants and developers working on the React frontend.

## Technology Stack

- **React 18** - UI framework with hooks
- **TypeScript** - Type safety
- **Vite** - Build tool with HMR
- **Vitest** - Testing framework
- **Sonner** - Toast notifications
- **shadcn/ui components** - Sheet, Tabs, Button (in `components/ui/`)
- **meshcore-cracker** - WebGPU-accelerated channel key bruteforcing

## Directory Structure

```
frontend/
├── src/
│   ├── main.tsx              # Entry point, renders App
│   ├── App.tsx               # Main component, all state management
│   ├── api.ts                # REST API client
│   ├── types.ts              # TypeScript interfaces
│   ├── useWebSocket.ts       # WebSocket hook with auto-reconnect
│   ├── styles.css            # Dark theme CSS
│   ├── utils/
│   │   ├── messageParser.ts  # Text parsing utilities
│   │   ├── conversationState.ts  # localStorage for unread tracking
│   │   ├── pubkey.ts         # Public key utilities (prefix matching, display names)
│   │   └── contactAvatar.ts  # Avatar generation (colors, initials/emoji)
│   ├── components/
│   │   ├── ui/               # shadcn/ui components
│   │   │   ├── sonner.tsx    # Toast notifications (Sonner wrapper)
│   │   │   ├── sheet.tsx     # Slide-out panel
│   │   │   ├── tabs.tsx      # Tab navigation
│   │   │   └── button.tsx    # Button component
│   │   ├── StatusBar.tsx     # Radio status, reconnect button, config button
│   │   ├── Sidebar.tsx       # Contacts/channels list, search, unread badges
│   │   ├── MessageList.tsx   # Message display, avatars, clickable senders
│   │   ├── MessageInput.tsx  # Text input with imperative handle
│   │   ├── ContactAvatar.tsx # Contact profile image component
│   │   ├── RawPacketList.tsx # Raw packet feed display
│   │   ├── CrackerPanel.tsx  # WebGPU channel key cracker
│   │   ├── NewMessageModal.tsx
│   │   └── ConfigModal.tsx   # Radio config + app settings
│   └── test/
│       ├── setup.ts          # Test setup (jsdom, matchers)
│       ├── messageParser.test.ts
│       ├── unreadCounts.test.ts
│       ├── contactAvatar.test.ts
│       ├── messageDeduplication.test.ts
│       └── websocket.test.ts
├── index.html
├── vite.config.ts            # API proxy config
├── tsconfig.json
└── package.json
```

## State Management

All application state lives in `App.tsx` using React hooks. No external state library.

### Core State

```typescript
const [health, setHealth] = useState<HealthStatus | null>(null);
const [config, setConfig] = useState<RadioConfig | null>(null);
const [contacts, setContacts] = useState<Contact[]>([]);
const [channels, setChannels] = useState<Channel[]>([]);
const [messages, setMessages] = useState<Message[]>([]);
const [rawPackets, setRawPackets] = useState<RawPacket[]>([]);
const [activeConversation, setActiveConversation] = useState<Conversation | null>(null);
const [unreadCounts, setUnreadCounts] = useState<Record<string, number>>({});
```

### State Flow

1. **WebSocket** pushes real-time updates (health, contacts, channels, messages)
2. **REST API** fetches initial data and handles user actions
3. **Components** receive state as props, call handlers to trigger changes

## WebSocket (`useWebSocket.ts`)

The `useWebSocket` hook manages real-time connection:

```typescript
const wsHandlers = useMemo(() => ({
  onHealth: (data: HealthStatus) => setHealth(data),
  onMessage: (msg: Message) => { /* add to list, track unread */ },
  onMessageAcked: (messageId: number) => { /* update acked status */ },
  // ...
}), []);

useWebSocket(wsHandlers);
```

### Features

- **Auto-reconnect**: Reconnects after 3 seconds on disconnect
- **Heartbeat**: Sends ping every 30 seconds
- **Event types**: `health`, `contacts`, `channels`, `message`, `contact`, `raw_packet`, `message_acked`, `error`
- **Error handling**: `onError` handler displays toast notifications for backend errors

### URL Detection

```typescript
const isDev = window.location.port === '5173';
const wsUrl = isDev
  ? 'ws://localhost:8000/api/ws'
  : `${protocol}//${window.location.host}/api/ws`;
```

## API Client (`api.ts`)

Typed REST client with consistent error handling:

```typescript
import { api } from './api';

// Health
await api.getHealth();

// Radio
await api.getRadioConfig();
await api.updateRadioConfig({ name: 'MyRadio' });
await api.sendAdvertisement(true);

// Contacts/Channels
await api.getContacts();
await api.getChannels();
await api.createChannel('#test');

// Messages
await api.getMessages({ type: 'CHAN', conversation_key: channelKey, limit: 200 });
await api.sendChannelMessage(channelKey, 'Hello');
await api.sendDirectMessage(publicKey, 'Hello');

// Historical decryption
await api.decryptHistoricalPackets({ key_type: 'channel', channel_name: '#test' });

// Radio reconnection
await api.reconnectRadio();  // Returns { status, message, connected }
```

### API Proxy (Development)

Vite proxies `/api/*` to backend (backend routes are already prefixed with `/api`):

```typescript
// vite.config.ts
server: {
  proxy: {
    '/api': {
      target: 'http://localhost:8000',
      changeOrigin: true,
    },
  },
}
```

## Type Definitions (`types.ts`)

### Key Type Aliases

```typescript
type PublicKey = string;     // 64-char hex identifying a contact/node
type PubkeyPrefix = string;  // 12-char hex prefix (used in message routing)
type ChannelKey = string;    // 32-char hex identifying a channel
```

### Key Interfaces

```typescript
interface Contact {
  public_key: PublicKey;
  name: string | null;
  type: number;            // 0=unknown, 1=client, 2=repeater, 3=room
  on_radio: boolean;
  // ...
}

interface Channel {
  key: ChannelKey;
  name: string;
  is_hashtag: boolean;
  on_radio: boolean;
}

interface Message {
  id: number;
  type: 'PRIV' | 'CHAN';
  conversation_key: string;  // PublicKey for PRIV, ChannelKey for CHAN
  text: string;
  outgoing: boolean;
  acked: boolean;
  // ...
}

interface Conversation {
  type: 'contact' | 'channel' | 'raw';
  id: string;              // PublicKey for contacts, ChannelKey for channels
  name: string;
}

interface AppSettings {
  max_radio_contacts: number;
}
```

## Component Patterns

### MessageInput with Imperative Handle

Exposes `appendText` method for click-to-mention:

```typescript
export interface MessageInputHandle {
  appendText: (text: string) => void;
}

export const MessageInput = forwardRef<MessageInputHandle, MessageInputProps>(
  function MessageInput({ onSend, disabled }, ref) {
    useImperativeHandle(ref, () => ({
      appendText: (text: string) => {
        setText((prev) => prev + text);
        inputRef.current?.focus();
      },
    }));
    // ...
  }
);

// Usage in App.tsx
const messageInputRef = useRef<MessageInputHandle>(null);
messageInputRef.current?.appendText(`@[${sender}] `);
```

### Unread Count Tracking

Uses refs to avoid stale closures in memoized handlers:

```typescript
const activeConversationRef = useRef<Conversation | null>(null);

// Keep ref in sync
useEffect(() => {
  activeConversationRef.current = activeConversation;
}, [activeConversation]);

// In WebSocket handler (can safely access current value)
const activeConv = activeConversationRef.current;
```

### State Tracking Keys

State tracking keys (for unread counts and message times) are generated by `getStateKey()`:

```typescript
import { getStateKey } from './utils/conversationState';

// Channels: "channel-{channelKey}"
getStateKey('channel', channelKey)  // e.g., "channel-8B3387E9C5CDEA6AC9E5EDBAA115CD72"

// Contacts: "contact-{12-char-prefix}"
getStateKey('contact', publicKey)   // e.g., "contact-abc123def456"
```

**Note:** `getStateKey()` is NOT the same as `Message.conversation_key`. The state key is prefixed
for localStorage tracking, while `conversation_key` is the raw database field.

## Utility Functions

### Message Parser (`utils/messageParser.ts`)

```typescript
// Parse "sender: message" format from channel messages
parseSenderFromText(text: string): { sender: string | null; content: string }

// Format Unix timestamp to time string
formatTime(timestamp: number): string
```

### Public Key Utilities (`utils/pubkey.ts`)

Consistent handling of 64-char full keys and 12-char prefixes:

```typescript
import { getPubkeyPrefix, pubkeysMatch, getContactDisplayName } from './utils/pubkey';

// Extract 12-char prefix (works with full keys or existing prefixes)
getPubkeyPrefix(key)  // "abc123def456..."

// Compare keys by prefix (handles mixed full/prefix comparisons)
pubkeysMatch(key1, key2)  // true if prefixes match

// Get display name with fallback to prefix
getContactDisplayName(name, publicKey)  // name or first 12 chars of key
```

### Conversation State (`utils/conversationState.ts`)

```typescript
import { getStateKey, setLastMessageTime, setLastReadTime } from './utils/conversationState';

// Generate state tracking key (NOT the same as Message.conversation_key)
getStateKey('channel', channelKey)
getStateKey('contact', publicKey)

// Track message times for unread detection
setLastMessageTime(stateKey, timestamp)
setLastReadTime(stateKey, timestamp)
```

### Contact Avatar (`utils/contactAvatar.ts`)

Generates consistent profile "images" for contacts using hash-based colors:

```typescript
import { getContactAvatar, CONTACT_TYPE_REPEATER } from './utils/contactAvatar';

// Get avatar info for a contact
const avatar = getContactAvatar(name, publicKey, contactType);
// Returns: { text: 'JD', background: 'hsl(180, 60%, 40%)', textColor: '#ffffff' }

// Repeaters (type=2) always show 🛜 with gray background
const repeaterAvatar = getContactAvatar('Some Repeater', key, CONTACT_TYPE_REPEATER);
// Returns: { text: '🛜', background: '#444444', textColor: '#ffffff' }
```

Avatar text priority:
1. First emoji in name
2. Initials (first letter + first letter after space)
3. Single first letter
4. First 2 chars of public key (fallback)

## CSS Patterns

The app uses a minimal dark theme in `styles.css`.

### Key Classes

```css
.app             /* Root container */
.status-bar      /* Top bar with radio info */
.sidebar         /* Left panel with contacts/channels */
.sidebar-item    /* Individual contact/channel row */
.sidebar-item.unread  /* Bold with badge */
.message-area    /* Main content area */
.message-list    /* Scrollable message container */
.message         /* Individual message */
.message.outgoing    /* Right-aligned, different color */
.message .sender     /* Clickable sender name */
```

### Unread Badge

```css
.sidebar-item.unread .name {
  font-weight: 700;
  color: #fff;
}
.sidebar-item .unread-badge {
  background: #4caf50;
  color: #fff;
  font-size: 10px;
  padding: 2px 6px;
  border-radius: 10px;
}
```

## Testing

Run tests with:
```bash
cd frontend
npm run test:run    # Single run
npm run test        # Watch mode
```

### Test Files

- `messageParser.test.ts` - Sender extraction, time formatting, conversation keys
- `unreadCounts.test.ts` - Unread tracking logic
- `contactAvatar.test.ts` - Avatar text extraction, color generation, repeater handling
- `messageDeduplication.test.ts` - Message deduplication logic
- `websocket.test.ts` - WebSocket message routing

### Test Setup

Tests use jsdom environment with `@testing-library/react`:

```typescript
// src/test/setup.ts
import '@testing-library/jest-dom';
```

## Common Tasks

### Adding a New Component

1. Create component in `src/components/`
2. Add TypeScript props interface
3. Import and use in `App.tsx` or parent component
4. Add styles to `styles.css`

### Adding a New API Endpoint

1. Add method to `api.ts`
2. Add/update types in `types.ts`
3. Call from `App.tsx` or component

### Adding New WebSocket Event

1. Add handler option to `UseWebSocketOptions` interface in `useWebSocket.ts`
2. Add case to `onmessage` switch
3. Provide handler in `wsHandlers` object in `App.tsx`

### Adding State

1. Add `useState` in `App.tsx`
2. Pass down as props to components
3. If needed in WebSocket handler, also use a ref to avoid stale closures

## Development Workflow

```bash
# Start dev server (hot reload)
npm run dev

# Build for production
npm run build

# Preview production build
npm run preview

# Run tests
npm run test:run
```

The dev server runs on port 5173 and proxies API requests to `localhost:8000`.

### Production Build

In production, the FastAPI backend serves the compiled frontend from `frontend/dist`:

```bash
npm run build
# Then run backend: uv run uvicorn app.main:app --host 0.0.0.0 --port 8000
```

## CrackerPanel

The `CrackerPanel` component provides WebGPU-accelerated brute-forcing of channel keys for undecrypted GROUP_TEXT packets.

### Features

- **Dictionary attack first**: Uses `words_alpha.txt` wordlist
- **GPU bruteforce**: Falls back to character-by-character search
- **Queue management**: Automatically processes new packets as they arrive
- **Auto-channel creation**: Cracked channels are automatically added to the channel list
- **Configurable max length**: Adjustable while running (default: 6)
- **Retry failed**: Option to retry failed packets at increasing lengths

### Key Implementation Patterns

Uses refs to avoid stale closures in async callbacks:

```typescript
const isRunningRef = useRef(false);
const isProcessingRef = useRef(false);  // Prevents concurrent GPU operations
const queueRef = useRef<Map<number, QueueItem>>(new Map());
const retryFailedRef = useRef(false);
const maxLengthRef = useRef(6);
```

Progress reporting shows rate in Mkeys/s or Gkeys/s depending on speed.

## Toast Notifications

The app uses Sonner for toast notifications via a custom wrapper at `components/ui/sonner.tsx`:

```typescript
import { toast } from './components/ui/sonner';

// Success toast
toast.success('Operation completed', { description: 'Details here' });

// Error toast (muted red styling for readability)
toast.error('Operation failed', { description: 'Error details' });
```

Toasts are automatically shown for:
- Radio connection/disconnection status changes
- Backend errors received via WebSocket `error` events
- Manual reconnection success/failure

The `<Toaster />` component is rendered in `App.tsx` with `position="top-right"`.
