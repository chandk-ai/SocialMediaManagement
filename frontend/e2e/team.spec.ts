/**
 * Team / invitation flow.
 *
 * Drives the API directly through `request.fetch` so we don't have to
 * model the UI's debounce + SWR cache invalidation. The UI layer is
 * exercised by a separate page-load assertion at the end.
 *
 * Verifies the email-claim contract:
 *   1. Admin POSTs an invitation → row visible in GET /team/invitations.
 *   2. Revoke → invitation disappears from the pending list.
 *   3. UI page renders the invite count + members count without crashing.
 */
import { expect, test } from './fixtures';

test('admin can create + revoke a pending invitation', async ({ authedPage, request }) => {
  const email = `e2e-${Date.now()}@example.com`;

  // Create
  const createResp = await request.post('/api/proxy/team/invitations', {
    data: { email, role: 'editor' },
  });
  expect(createResp.ok()).toBeTruthy();
  const created = await createResp.json();
  expect(created.email).toBe(email);
  expect(created.role).toBe('editor');
  expect(typeof created.id).toBe('string');

  // List — should appear
  const list1 = await request.get('/api/proxy/team/invitations');
  expect(list1.ok()).toBeTruthy();
  const body1 = await list1.json();
  expect(body1.invitations.some((i: { email: string }) => i.email === email)).toBeTruthy();

  // Revoke
  const del = await request.delete(`/api/proxy/team/invitations/${created.id}`);
  expect(del.status()).toBe(204);

  // List — should disappear
  const list2 = await request.get('/api/proxy/team/invitations');
  const body2 = await list2.json();
  expect(body2.invitations.some((i: { email: string }) => i.email === email)).toBeFalsy();

  // UI smoke — the team page loads without crashing.
  await authedPage.goto('/settings/team');
  await expect(authedPage.getByRole('heading', { name: /Invite a teammate/i })).toBeVisible();
  await expect(authedPage.getByRole('heading', { name: /Pending invitations/i })).toBeVisible();
  await expect(authedPage.getByRole('heading', { name: /Members/i })).toBeVisible();
});

test('rejects an invitation with a malformed email', async ({ request }) => {
  const resp = await request.post('/api/proxy/team/invitations', {
    data: { email: 'not-an-email', role: 'viewer' },
  });
  // Service rejects anything missing '@' with 409 (ValueError → 409 in the route).
  expect([400, 409, 422]).toContain(resp.status());
});
