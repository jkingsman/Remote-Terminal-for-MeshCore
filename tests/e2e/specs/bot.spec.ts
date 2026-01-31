import { test, expect } from '@playwright/test';
import { ensureFlightlessChannel, getSettings, updateSettings } from '../helpers/api';
import type { BotConfig } from '../helpers/api';

const BOT_CODE = `def bot(sender_name, sender_key, message_text, is_dm, channel_key, channel_name, sender_timestamp, path):
    if channel_name == "#flightless" and "!e2etest" in message_text.lower():
        return "[BOT] e2e-ok"
    return None`;

test.describe('Bot functionality', () => {
  let originalBots: BotConfig[];

  test.beforeAll(async () => {
    await ensureFlightlessChannel();
    const settings = await getSettings();
    originalBots = settings.bots ?? [];
  });

  test.afterAll(async () => {
    // Restore original bot config
    try {
      await updateSettings({ bots: originalBots });
    } catch {
      console.warn('Failed to restore bot config');
    }
  });

  test('create a bot via API, verify it in UI, trigger it, and verify response', async ({
    page,
  }) => {
    // --- Step 1: Create and enable bot via API ---
    // CodeMirror is difficult to drive via Playwright (contenteditable, lazy-loaded),
    // so we set the bot code via the REST API and verify it through the UI.
    const testBot: BotConfig = {
      id: crypto.randomUUID(),
      name: 'E2E Test Bot',
      enabled: true,
      code: BOT_CODE,
    };
    await updateSettings({ bots: [...originalBots, testBot] });

    // --- Step 2: Verify bot appears in settings UI ---
    await page.goto('/');
    await expect(page.getByText('Connected')).toBeVisible();

    await page.getByText('Radio & Config').click();
    await page.getByRole('tab', { name: 'Bot' }).click();

    // The bot name should be visible in the bot list
    await expect(page.getByText('E2E Test Bot')).toBeVisible();

    // Close settings
    await page.keyboard.press('Escape');

    // --- Step 3: Trigger the bot ---
    await page.getByText('#flightless', { exact: true }).first().click();

    const triggerMessage = `!e2etest ${Date.now()}`;
    const input = page.getByPlaceholder(/type a message|message #flightless/i);
    await input.fill(triggerMessage);
    await page.getByRole('button', { name: 'Send' }).click();

    // --- Step 4: Verify bot response appears ---
    // Bot has ~2s delay before responding, plus radio send time
    await expect(page.getByText('[BOT] e2e-ok')).toBeVisible({ timeout: 30_000 });
  });
});
