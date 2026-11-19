/**
 * Visual review screenshots (spec §13). Writes PNGs to /screenshots.
 * Run: npm run test:e2e -- screenshots
 */
import { test, type Page } from '@playwright/test';

const OUT = 'screenshots';

async function fresh(page: Page, width = 1440, height = 900) {
  await page.setViewportSize({ width, height });
  await page.goto('/home');
  await page.evaluate(() => {
    localStorage.clear();
    localStorage.setItem('ageneditor_settings_v1', JSON.stringify({ hasSeenWelcome: true }));
  });
}

async function shot(page: Page, name: string) {
  await page.waitForTimeout(300);
  await page.screenshot({ path: `${OUT}/${name}.png` });
}

test('welcome', async ({ page }) => {
  await page.goto('/home');
  await page.evaluate(() => localStorage.clear());
  await page.goto('/');
  await page.getByRole('heading', { name: 'Turn raw footage into a finished edit.' }).waitFor();
  await shot(page, 'welcome-1440x900');
});

test('dashboard', async ({ page }) => {
  await fresh(page);
  await page.goto('/home');
  await page.getByText('Continue editing').waitFor();
  await shot(page, 'dashboard-1440x900');
});

test('projects', async ({ page }) => {
  await fresh(page);
  await page.goto('/projects');
  await page.getByRole('list', { name: 'Projects' }).waitFor();
  await shot(page, 'projects-grid-1440x900');
});

test('wizard', async ({ page }) => {
  await fresh(page);
  await page.goto('/projects/new');
  await page.getByLabel('Project title *').fill('Habit Loop — Episode 4 Reel');
  await page.getByRole('button', { name: 'Next' }).click();
  await page.getByRole('button', { name: 'Choose sample assets' }).click();
  await shot(page, 'wizard-assets-1440x900');
});

test('overview', async ({ page }) => {
  await fresh(page);
  await page.goto('/projects/reel_001');
  await page.getByRole('heading', { name: /Timeline revisions/ }).waitFor();
  await shot(page, 'project-overview-1440x900');
});

for (const [w, h] of [
  [1440, 900],
  [1920, 1080],
  [1280, 720],
  [1100, 760],
  [900, 760],
] as const) {
  test(`editor ${w}x${h}`, async ({ page }) => {
    await fresh(page, w, h);
    await page.goto('/projects/reel_001/editor');
    await page.getByTestId('simulated-label').waitFor();
    if (w >= 960) {
      await page.locator('[data-clip-id]').nth(1).click();
    }
    await shot(page, `editor-${w}x${h}`);
  });
}

test('review', async ({ page }) => {
  await fresh(page);
  await page.goto('/projects/reel_001/review');
  await page.getByTestId('qa-list').waitFor();
  await shot(page, 'review-1440x900');
});

test('export dialog', async ({ page }) => {
  await fresh(page);
  await page.goto('/projects/reel_001/editor');
  await page.getByRole('button', { name: 'Export', exact: true }).click();
  await page.getByRole('button', { name: 'Preview Export Settings' }).click();
  await page.getByTestId('export-summary').waitFor();
  await shot(page, 'export-summary-1440x900');
});

test('activity, templates, settings, help, 404', async ({ page }) => {
  await fresh(page);
  for (const [path, name] of [
    ['/activity', 'activity'],
    ['/templates', 'templates'],
    ['/settings', 'settings'],
    ['/help', 'help'],
    ['/does-not-exist', 'not-found'],
  ] as const) {
    await page.goto(path);
    await page.getByRole('heading', { level: 1 }).first().waitFor();
    await shot(page, `${name}-1440x900`);
  }
});

test('production', async ({ page }) => {
  await fresh(page);
  await page.goto('/production');
  await page.getByRole('heading', { level: 2, name: 'Podcast ep. 12 shorts' }).waitFor();
  await shot(page, 'production-overview-1440x900');
  await page.getByRole('tab', { name: /^Review/ }).click();
  await page.getByText('Sample finding: the second cut lingers.').waitFor();
  await shot(page, 'production-review-1440x900');
  await page.getByRole('tab', { name: 'Clients' }).click();
  await page.getByRole('form', { name: 'Global defaults' }).waitFor();
  await shot(page, 'production-clients-1440x900');
});
