import { test, expect } from '@playwright/test';
import { getRadioConfig, updateRadioConfig } from '../helpers/api';

test.describe('Radio settings', () => {
  let originalName: string;

  test.beforeAll(async () => {
    const config = await getRadioConfig();
    originalName = config.name;
  });

  test.afterAll(async () => {
    // Restore original name via API
    try {
      await updateRadioConfig({ name: originalName });
    } catch {
      console.warn('Failed to restore radio name — manual intervention may be needed');
    }
  });

  test('change radio name via settings UI and verify persistence', async ({ page }) => {
    // Radio names are limited to 8 characters
    const testName = 'E2Etest1';

    await page.goto('/');
    await expect(page.getByText('Connected')).toBeVisible();

    // --- Step 1: Change the name via settings UI ---
    await page.getByText('Radio & Config').click();
    await page.getByRole('tab', { name: 'Identity' }).click();

    const nameInput = page.locator('#name');
    await nameInput.clear();
    await nameInput.fill(testName);

    await page.getByRole('button', { name: 'Save Identity Settings' }).click();
    await expect(page.getByText('Identity settings saved')).toBeVisible({ timeout: 10_000 });

    // Close settings
    await page.keyboard.press('Escape');

    // --- Step 2: Verify via API (now returns fresh data after send_appstart fix) ---
    const config = await getRadioConfig();
    expect(config.name).toBe(testName);

    // --- Step 3: Verify persistence across page reload ---
    await page.reload();
    await expect(page.getByText('Connected')).toBeVisible({ timeout: 15_000 });

    await page.getByText('Radio & Config').click();
    await page.getByRole('tab', { name: 'Identity' }).click();
    await expect(page.locator('#name')).toHaveValue(testName, { timeout: 10_000 });
  });
});
