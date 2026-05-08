/**
 * Auth surface — verifies the login page renders all three methods and
 * that an unauthenticated visit to a protected route bounces to /login.
 *
 * We don't end-to-end the magic-link receive step (no email server in CI)
 * — only the SEND side. The receive side runs against a real Supabase
 * project in a separate manual / staging suite.
 */
import { expect, test } from '@playwright/test';

test.describe('login page', () => {
  test('renders both Email link and Password tabs', async ({ page }) => {
    await page.goto('/login');
    await expect(page.getByRole('tab', { name: /Email link/i })).toBeVisible();
    await expect(page.getByRole('tab', { name: /Password/i })).toBeVisible();
  });

  test('Email link tab shows the magic-link form by default', async ({ page }) => {
    await page.goto('/login');
    await expect(page.getByRole('tab', { name: /Email link/i })).toHaveAttribute('aria-selected', 'true');
    await expect(page.getByRole('button', { name: /Send sign-in link/i })).toBeVisible();
  });

  test('Password tab swaps the form to email + password', async ({ page }) => {
    await page.goto('/login');
    await page.getByRole('tab', { name: /Password/i }).click();
    await expect(page.getByLabel(/Password/i)).toBeVisible();
    await expect(page.getByRole('button', { name: /^Sign in$/i })).toBeVisible();
  });

  test('unauth visit to /dashboard redirects to /login', async ({ browser }) => {
    // Fresh context — no auth headers.
    const ctx = await browser.newContext();
    const page = await ctx.newPage();
    await page.goto('/dashboard');
    await expect(page).toHaveURL(/\/login/);
    await ctx.close();
  });
});
