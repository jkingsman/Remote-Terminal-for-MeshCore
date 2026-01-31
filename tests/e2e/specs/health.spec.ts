import { test, expect } from '@playwright/test';

test.describe('Health & UI basics', () => {
  test('page loads and shows connected status', async ({ page }) => {
    await page.goto('/');

    // Status bar shows "Connected"
    await expect(page.getByText('Connected')).toBeVisible();

    // Sidebar is visible with key sections
    await expect(page.getByRole('heading', { name: 'Conversations' })).toBeVisible();
    await expect(page.getByText('Packet Feed')).toBeVisible();
    await expect(page.getByText('Node Map')).toBeVisible();
  });

  test('sidebar shows Channels and Contacts sections', async ({ page }) => {
    await page.goto('/');

    await expect(page.getByText('Channels')).toBeVisible();
    await expect(page.getByText('Contacts')).toBeVisible();
  });
});
