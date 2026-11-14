/**
 * Full demo flow at 1440×900 (spec §13):
 * Welcome → New Project → Editor → Review → Export configuration.
 */
import { expect, test } from '@playwright/test';

test.beforeEach(async ({ page }) => {
  await page.goto('/home');
  await page.evaluate(() => localStorage.clear()); // first-run, fixtures reseeded on load
});

test('Welcome → New Project → Editor → Review → Export settings', async ({ page }) => {
  // Welcome (first run)
  await page.goto('/');
  await expect(page).toHaveURL(/\/welcome$/);
  await expect(page.getByText('UI Prototype · Demo Mode')).toBeVisible();
  await page.getByRole('button', { name: 'Create Project' }).click();

  // Wizard
  await expect(page).toHaveURL(/\/projects\/new$/);
  await page.getByRole('button', { name: 'Next' }).click();
  await expect(page.getByText('Enter a project name.')).toBeVisible();
  await page.getByLabel('Project title *').fill('E2E Habit Reel');
  await page.getByRole('button', { name: 'Next' }).click();
  await page.getByRole('button', { name: 'Choose sample assets' }).click();
  await page.getByRole('button', { name: 'Next' }).click();
  await expect(page.getByRole('heading', { name: 'Editing brief' })).toBeVisible();
  await page.getByRole('button', { name: 'Next' }).click();
  await expect(page.getByTestId('wizard-summary')).toContainText('E2E Habit Reel');
  await page.getByRole('button', { name: 'Create Demo Project' }).click();

  // Overview → Editor
  await expect(page.getByRole('heading', { level: 1, name: 'E2E Habit Reel' })).toBeVisible();
  await page.getByRole('button', { name: 'Open Editor' }).click();
  await page.getByRole('button', { name: 'Create sample edit plan' }).click();
  await expect(page.getByTestId('simulated-label')).toHaveText('Simulated timeline preview — no rendered video or audio');

  // Select a clip → inspector
  const firstV1 = page.locator('[data-clip-id]', { hasText: 'Segment 2' });
  await firstV1.click();
  await expect(page.getByRole('complementary', { name: 'Inspector' }).getByRole('heading', { name: 'Segment 2' })).toBeVisible();

  // Playhead keyboard + Space simulation
  await page.keyboard.press('Home');
  await expect(page.getByTestId('preview-timecode')).toHaveText('00:00:00');
  await page.keyboard.press('ArrowRight');
  await expect(page.getByTestId('preview-timecode')).toHaveText('00:00:01');

  // Delete + undo
  const count = await page.locator('[data-clip-id]').count();
  await firstV1.click();
  await page.keyboard.press('Delete');
  await expect(page.locator('[data-clip-id]')).toHaveCount(count - 1);
  await page.keyboard.press('Control+z');
  await expect(page.locator('[data-clip-id]')).toHaveCount(count);

  // Review
  await page.getByRole('button', { name: 'Review', exact: true }).click();
  await expect(page.getByRole('heading', { level: 1, name: 'Review · v1' })).toBeVisible();
  await expect(page.getByTestId('qa-list')).toContainText('Not tested');
  await page.getByLabel('New review note').fill('Hook needs more punch');
  await page.getByRole('button', { name: 'Add note' }).click();
  await expect(page.getByTestId('review-notes')).toContainText('Hook needs more punch');

  // Export configuration (no files)
  await page.getByRole('button', { name: 'Export settings' }).click();
  const dialog = page.getByRole('dialog');
  await expect(dialog.getByText('Configuration only — no render')).toBeVisible();
  await dialog.getByRole('button', { name: 'Preview Export Settings' }).click();
  await expect(dialog.getByTestId('export-summary')).toContainText('final.mp4');
  await expect(dialog.getByText('Planned · not created').first()).toBeVisible();
  await expect(dialog.locator('a[download], a[href$=".mp4"]')).toHaveCount(0);
  await dialog.getByRole('button', { name: 'Queue simulation job' }).click();
  await dialog.getByRole('button', { name: 'View in Activity' }).click();

  // Activity shows a simulation that completes deterministically
  await expect(page).toHaveURL(/\/activity$/);
  const job = page.getByTestId('job-card').first();
  await expect(job).toContainText('Simulation');
  await expect(job).toContainText('Completed', { timeout: 15_000 });
  await expect(job).toContainText('No file was rendered or written');
});

test('keyboard: Ctrl+K palette, ? shortcuts, Escape closes and restores focus', async ({ page }) => {
  await page.evaluate(() => localStorage.setItem('ageneditor_settings_v1', JSON.stringify({ hasSeenWelcome: true })));
  await page.goto('/home');
  await expect(page.getByRole('heading', { level: 1, name: 'Home' })).toBeVisible(); // lazy route mounted
  await page.keyboard.press('Control+k');
  const search = page.getByRole('combobox', { name: 'Search projects and screens' });
  await expect(search).toBeFocused();
  await search.fill('podcast');
  await page.keyboard.press('Enter');
  await expect(page).toHaveURL(/reel_003\/editor/);

  await page.goto('/home');
  await expect(page.getByRole('heading', { level: 1, name: 'Home' })).toBeVisible();
  const trigger = page.getByRole('button', { name: 'Keyboard shortcuts' });
  await trigger.focus();
  await page.keyboard.press('Enter');
  await expect(page.getByRole('dialog', { name: 'Keyboard shortcuts' })).toBeVisible();
  await page.keyboard.press('Escape');
  await expect(page.getByRole('dialog')).toHaveCount(0);
  await expect(trigger).toBeFocused();
});

test('Reset Demo Data restores fixtures', async ({ page }) => {
  await page.evaluate(() => localStorage.setItem('ageneditor_settings_v1', JSON.stringify({ hasSeenWelcome: true })));
  await page.goto('/projects');
  await page.getByRole('button', { name: 'More actions for Podcast Highlight: Distribution Engines' }).click();
  await page.getByRole('menuitem', { name: 'Archive…' }).click();
  await page.getByRole('dialog').getByRole('button', { name: 'Archive' }).click();
  await expect(page.getByRole('list', { name: 'Projects' }).getByRole('listitem')).toHaveCount(2);

  await page.getByRole('button', { name: 'Reset Demo Data' }).click();
  await page.getByRole('dialog').getByRole('button', { name: 'Reset demo data' }).click();
  await page.goto('/projects');
  await expect(page.getByRole('list', { name: 'Projects' }).getByRole('listitem')).toHaveCount(3);
});

test('no horizontal overflow at 1280×720 and narrow editor tabs work', async ({ page }) => {
  await page.evaluate(() => localStorage.setItem('ageneditor_settings_v1', JSON.stringify({ hasSeenWelcome: true })));
  await page.setViewportSize({ width: 1280, height: 720 });
  for (const path of ['/home', '/projects', '/projects/reel_001', '/projects/reel_001/editor', '/projects/reel_001/review', '/settings']) {
    await page.goto(path);
    await page.waitForLoadState('networkidle');
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
    expect(overflow, path).toBeLessThanOrEqual(0);
  }
  await page.setViewportSize({ width: 900, height: 760 });
  await page.goto('/projects/reel_001/editor');
  await expect(page.getByText('Best experienced at 1280px or wider.')).toBeVisible();
  await page.getByRole('tab', { name: 'Timeline' }).click();
  await expect(page.getByRole('region', { name: 'Timeline' })).toBeVisible();
});
