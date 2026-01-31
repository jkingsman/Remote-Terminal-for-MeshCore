import { test, expect } from '@playwright/test';
import { createChannel, getChannels, getMessages } from '../helpers/api';

/**
 * These tests wait for real incoming messages from the mesh network.
 * They require a radio attached and other nodes actively transmitting.
 * Timeout is 10 minutes to allow for intermittent traffic.
 */

const ROOMS = [
  '#flightless', '#bot', '#snoco', '#skagit', '#edmonds', '#bachelorette',
  '#emergency', '#furry', '#public', '#puppy', '#foobar', '#capitolhill',
  '#hamradio', '#icewatch', '#saucefamily', '#scvsar', '#startrek', '#metalmusic',
  '#seattle', '#vanbot', '#bot-van', '#lynden', '#bham', '#sipesbot', '#psrg',
  '#testing', '#olybot', '#test', '#ve7rva', '#wardrive', '#kitsap', '#tacoma',
  '#rats', '#pdx', '#olympia', '#bot2', '#transit', '#salishmesh', '#meshwar',
  '#cats', '#jokes', '#decode', '#whatcom', '#bot-oly', '#sports', '#weather',
  '#wasma', '#ravenna', '#northbend', '#dsa', '#oly-bot', '#grove', '#cars',
  '#bellingham', '#baseball', '#mariners', '#eugene', '#victoria', '#vimesh',
  '#bot-pdx', '#chinese', '#miro', '#poop', '#papa', '#uw', '#renton',
  '#general', '#bellevue', '#eastside', '#bit', '#dev', '#farts', '#protest',
  '#gmrs', '#pri', '#boob', '#baga', '#fun', '#w7dk', '#wedgwood', '#bots',
  '#sounders', '#steelhead', '#uetfwf', '#ballard', '#at', '#1234567', '#funny',
  '#abbytest', '#abird', '#afterparty', '#arborheights', '#atheist', '#auburn',
  '#bbs', '#blog', '#bottest', '#cascadiamesh', '#chat', '#checkcheck',
  '#civicmesh', '#columbiacity', '#dad', '#dmaspace', '#droptable', '#duvall',
  '#dx', '#emcomm', '#finnhill', '#foxden', '#freebsd', '#greenwood', '#howlbot',
  '#idahomesh', '#junk', '#kraken', '#kremwerk', '#maplemesh', '#meshcore',
  '#meshmonday', '#methow', '#minecraft', '#newwestminster', '#northvan',
  '#ominous', '#pagan', '#party', '#place', '#pokemon', '#portland', '#rave',
  '#raving', '#rftest', '#richmond', '#rolston', '#salishtest', '#saved',
  '#seahawks', '#sipebot', '#slumbermesh', '#snoqualmie', '#southisland',
  '#sydney', '#tacobot', '#tdeck', '#trans', '#ubc', '#underground', '#van-bot',
  '#vancouver', '#vashon', '#wardriving', '#wormhole', '#yelling', '#zork',
];

// 10 minute timeout for waiting on mesh traffic
test.describe('Incoming mesh messages', () => {
  test.setTimeout(600_000);

  test.beforeAll(async () => {
    // Ensure all rooms exist — create any that are missing
    const existing = await getChannels();
    const existingNames = new Set(existing.map((c) => c.name));

    for (const room of ROOMS) {
      if (!existingNames.has(room)) {
        try {
          await createChannel(room);
        } catch {
          // May already exist from a concurrent creation, ignore
        }
      }
    }
  });

  test('receive an incoming message in any room', async ({ page }) => {
    await page.goto('/');
    await expect(page.getByText('Connected')).toBeVisible();

    // Record existing message counts per channel so we can detect new ones
    const channels = await getChannels();
    const baselineCounts = new Map<string, number>();
    for (const ch of channels) {
      const msgs = await getMessages({ type: 'CHAN', conversation_key: ch.key, limit: 1 });
      baselineCounts.set(ch.key, msgs.length > 0 ? msgs[0].id : 0);
    }

    // Poll for a new incoming message across all channels
    let foundChannel: string | null = null;
    let foundMessageText: string | null = null;

    await expect(async () => {
      for (const ch of channels) {
        const msgs = await getMessages({
          type: 'CHAN',
          conversation_key: ch.key,
          limit: 5,
        });
        const baseline = baselineCounts.get(ch.key) ?? 0;
        const newIncoming = msgs.find((m) => m.id > baseline && !m.outgoing);
        if (newIncoming) {
          foundChannel = ch.name;
          foundMessageText = newIncoming.text;
          return;
        }
      }
      throw new Error('No new incoming messages yet');
    }).toPass({ intervals: [5_000], timeout: 570_000 });

    // Navigate to the channel that received a message
    console.log(`Received message in ${foundChannel}: "${foundMessageText}"`);
    await page.getByText(foundChannel!, { exact: true }).first().click();

    // Verify the message text is visible in the message list area (not sidebar)
    const messageArea = page.locator('.break-words');
    const messageContent = foundMessageText!.includes(': ')
      ? foundMessageText!.split(': ').slice(1).join(': ')
      : foundMessageText!;
    await expect(messageArea.getByText(messageContent, { exact: false }).first()).toBeVisible({
      timeout: 15_000,
    });
  });

  test('incoming message with path shows hop badge and path modal', async ({ page }) => {
    await page.goto('/');
    await expect(page.getByText('Connected')).toBeVisible();

    // Record baselines
    const channels = await getChannels();
    const baselineCounts = new Map<string, number>();
    for (const ch of channels) {
      const msgs = await getMessages({ type: 'CHAN', conversation_key: ch.key, limit: 1 });
      baselineCounts.set(ch.key, msgs.length > 0 ? msgs[0].id : 0);
    }

    // Wait for any incoming message that has path data
    let foundChannel: string | null = null;

    await expect(async () => {
      for (const ch of channels) {
        const msgs = await getMessages({
          type: 'CHAN',
          conversation_key: ch.key,
          limit: 10,
        });
        const baseline = baselineCounts.get(ch.key) ?? 0;
        const withPath = msgs.find(
          (m) => m.id > baseline && !m.outgoing && m.paths && m.paths.length > 0
        );
        if (withPath) {
          foundChannel = ch.name;
          return;
        }
      }
      throw new Error('No new incoming messages with path data yet');
    }).toPass({ intervals: [5_000], timeout: 570_000 });

    console.log(`Found message with path in ${foundChannel}`);

    // Navigate to the channel that received a message with path data
    await page.getByText(foundChannel!, { exact: true }).first().click();

    // Find any hop badge on the page — they all have title="View message path"
    // We don't care which specific message; just that a path badge exists and works.
    const badge = page.getByTitle('View message path').first();
    await expect(badge).toBeVisible({ timeout: 15_000 });

    // The badge text should match the pattern: (d), (1), (d/1/3), etc.
    const badgeText = await badge.textContent();
    console.log(`Badge text: ${badgeText}`);
    expect(badgeText).toMatch(/^\([d\d]+(\/[d\d]+)*\)$/);

    // Click the badge to open the path modal
    await badge.click();

    const modal = page.getByRole('dialog');
    await expect(modal).toBeVisible();

    // Verify the modal has the basic structural elements every path modal should have
    await expect(modal.getByText('Sender')).toBeVisible();
    await expect(modal.getByText('Receiver (me)')).toBeVisible();

    // Title should be either "Message Path" (single) or "Message Paths (N)" (multiple)
    const titleEl = modal.locator('h2, [class*="DialogTitle"]').first();
    const titleText = await titleEl.textContent();
    console.log(`Modal title: ${titleText}`);
    expect(titleText).toMatch(/^Message Paths?(\s+\(\d+\))?$/);

    // Close the modal
    await modal.getByRole('button', { name: 'Close', exact: true }).first().click();
    await expect(modal).not.toBeVisible();
  });
});
