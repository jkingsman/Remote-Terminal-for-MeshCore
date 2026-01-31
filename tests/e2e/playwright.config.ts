import { defineConfig } from '@playwright/test';
import path from 'path';

const projectRoot = path.resolve(__dirname, '..', '..');
const tmpDir = path.resolve(__dirname, '.tmp');

export default defineConfig({
  testDir: './specs',
  globalSetup: './global-setup.ts',

  // Radio operations are slow — generous timeouts
  timeout: 60_000,
  expect: { timeout: 15_000 },

  // Don't retry — failures likely indicate real hardware/app issues
  retries: 0,

  // Run tests serially — single radio means no parallelism
  fullyParallel: false,
  workers: 1,

  reporter: [['list'], ['html', { open: 'never' }]],

  use: {
    baseURL: 'http://localhost:8000',
    trace: 'on-first-retry',
    screenshot: 'only-on-failure',
  },

  projects: [
    {
      name: 'chromium',
      use: { browserName: 'chromium' },
    },
  ],

  webServer: {
    command: 'uv run uvicorn app.main:app --host 127.0.0.1 --port 8000',
    cwd: projectRoot,
    port: 8000,
    reuseExistingServer: false,
    timeout: 30_000,
    env: {
      MESHCORE_DATABASE_PATH: path.join(tmpDir, 'e2e-test.db'),
      // Pass through the serial port from the environment
      ...(process.env.MESHCORE_SERIAL_PORT
        ? { MESHCORE_SERIAL_PORT: process.env.MESHCORE_SERIAL_PORT }
        : {}),
    },
  },
});
