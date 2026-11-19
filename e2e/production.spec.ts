/**
 * Production Command Center in browser Demo Mode (2.0 spec §16, §20 "Desktop E2E"):
 * blocked states, queue, pause/resume, review approval and partial delivery, all on simulation data.
 */
import { expect, test } from '@playwright/test';

test.beforeEach(async ({ page }) => {
  await page.goto('/home');
  await page.evaluate(() => localStorage.clear());
});

test('queue, pause/resume, approval and partial delivery', async ({ page }) => {
  await page.goto('/home');
  await page.getByRole('link', { name: 'Production' }).click();
  await expect(page).toHaveURL(/\/production$/);
  await expect(page.getByText('Demo Mode · Simulation')).toBeVisible();
  await expect(page.getByRole('heading', { level: 2, name: 'Podcast ep. 12 shorts' })).toBeVisible();

  // Blocked states are explicit.
  const attention = page.locator('div', { has: page.getByRole('heading', { name: 'Needs attention' }) }).last();
  await expect(attention.getByText('Waiting for assets')).toBeVisible();
  await expect(attention.getByText('Retry wait')).toBeVisible();

  // Pause and resume.
  await page.getByRole('button', { name: 'Pause' }).click();
  await expect(page.getByRole('button', { name: 'Resume' })).toBeVisible();
  await page.getByRole('tab', { name: 'Queue' }).click();
  await expect(page.getByText('Batch paused: no new tasks start')).toBeVisible();
  await expect(page.getByText('RATE_LIMITED: provider rate limit')).toBeVisible();
  await page.getByRole('tab', { name: 'Overview' }).click();
  await page.getByRole('button', { name: 'Resume' }).click();
  await expect(page.getByRole('button', { name: 'Pause' })).toBeVisible();

  // Review: approval needs a named person.
  await page.getByRole('tab', { name: /^Review/ }).click();
  await expect(page.getByRole('button', { name: 'Approve this revision' })).toBeDisabled();
  await page.getByLabel('Your name').fill('E2E Reviewer');
  await page.getByRole('button', { name: 'Approve this revision' }).click();
  await expect(page.getByRole('heading', { level: 2, name: 'Why we brew before sunrise' })).toBeVisible();

  // Delivery: only the approved clip is offered; a subset is a partial package.
  await page.getByRole('tab', { name: 'Delivery' }).click();
  await expect(page.getByLabel('Deliver The one tool we never skip')).toHaveCount(0);
  await page.getByLabel('Deliver Why we brew before sunrise').check();
  await page.getByRole('button', { name: 'Package 1 clip (partial)' }).click();
  await expect(page.getByText('Simulation: no files were created.')).toBeVisible();
  await expect(page.getByText('by E2E Reviewer')).toBeVisible();
});
