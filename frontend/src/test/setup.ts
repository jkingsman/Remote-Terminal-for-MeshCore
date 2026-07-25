import '@testing-library/jest-dom';

// Node 22+ exposes its own `localStorage`/`sessionStorage` globals, and without
// `--localstorage-file` they are inert stubs with no Storage methods. Vitest only
// copies a jsdom window key onto the global when the key is absent from Node's global
// or appears in its curated key list, and Web Storage is in neither — so Node's stub
// shadows jsdom's Storage, and every `localStorage.clear()` in a test throws
// "localStorage.clear is not a function". Vitest also makes `window === globalThis`,
// so `window.localStorage` resolves to the same stub.
//
// Install a spec-shaped in-memory Storage instead of depending on which of the two
// wins in a given Node version.
class MemoryStorage implements Storage {
  #entries = new Map<string, string>();

  get length(): number {
    return this.#entries.size;
  }

  key(index: number): string | null {
    return [...this.#entries.keys()][index] ?? null;
  }

  getItem(key: string): string | null {
    return this.#entries.get(String(key)) ?? null;
  }

  setItem(key: string, value: string): void {
    this.#entries.set(String(key), String(value));
  }

  removeItem(key: string): void {
    this.#entries.delete(String(key));
  }

  clear(): void {
    this.#entries.clear();
  }
}

for (const name of ['localStorage', 'sessionStorage'] as const) {
  Object.defineProperty(globalThis, name, {
    configurable: true,
    writable: true,
    value: new MemoryStorage(),
  });
}

class ResizeObserver {
  observe() {}
  unobserve() {}
  disconnect() {}
}

globalThis.ResizeObserver = ResizeObserver;

// Several components call matchMedia at import time for responsive detection.
// Use a configurable descriptor so individual tests can override the stub.
if (typeof globalThis.matchMedia === 'undefined') {
  Object.defineProperty(globalThis, 'matchMedia', {
    configurable: true,
    writable: true,
    value: (query: string) => ({
      matches: false,
      media: query,
      onchange: null,
      addListener: () => {},
      removeListener: () => {},
      addEventListener: () => {},
      removeEventListener: () => {},
      dispatchEvent: () => false,
    }),
  });
}
