/**
 * Core publish loop — source + platform + workflow CRUD, then verify the
 * workflow appears in the list and the dashboard renders it.
 *
 * We avoid actually triggering a workflow run because that would require
 * real LLM credits + a real social platform endpoint. Instead, we drive
 * the data-plane through the API and the UI side through the page so a
 * regression in either layer (schema mismatch, missing field, broken
 * SWR cache) is caught.
 */
import { expect, test } from './fixtures';

test('admin can create a manual-mode source, platform, and workflow', async ({ authedPage, request }) => {
  const stamp = Date.now();

  // 1. Create a Source (RSS — needs no creds)
  const sourceResp = await request.post('/api/proxy/sources', {
    data: {
      plugin_name: 'rss',
      display_name: `e2e-rss-${stamp}`,
      config: { feed_url: 'https://example.com/feed.xml' },
    },
  });
  expect(sourceResp.ok()).toBeTruthy();
  const source = await sourceResp.json();
  expect(source.plugin_name).toBe('rss');

  // 2. Create a Platform (Discord — config is a webhook URL string, no OAuth)
  const platformResp = await request.post('/api/proxy/platforms', {
    data: {
      plugin_name: 'discord',
      display_name: `e2e-discord-${stamp}`,
      account_handle: 'e2e-channel',
      config: { webhook_url: 'https://discord.com/api/webhooks/0/test' },
      is_default: false,
      tags: ['e2e'],
    },
  });
  expect(platformResp.ok()).toBeTruthy();
  const platform = await platformResp.json();
  expect(platform.plugin_name).toBe('discord');

  // 3. Create a Workflow
  const workflowResp = await request.post('/api/proxy/workflows', {
    data: {
      name: `e2e-flow-${stamp}`,
      description: 'Created by Playwright',
      source_ids: [source.id],
      platform_ids: [platform.id],
      schedule: { kind: 'manual' },
      config: {
        tone: 'neutral', audience: 'general', voice_guide: '',
        max_revisions: 1, quality_threshold: 0.7, low_quality_threshold: 0.3,
        require_human_approval: false,
        llm_provider: 'mock', llm_model: 'mock-1', extra: {},
      },
      target_selector: {
        explicit_platform_ids: [platform.id],
        all_of_platforms: [], by_handle: [], tagged: [],
        exclude_platform_ids: [], exclude_plugins: [],
      },
    },
  });
  expect(workflowResp.ok()).toBeTruthy();
  const workflow = await workflowResp.json();
  expect(workflow.name).toContain('e2e-flow-');

  // 4. UI smoke — workflows page lists it
  await authedPage.goto('/workflows');
  await expect(authedPage.getByText(workflow.name)).toBeVisible({ timeout: 10_000 });

  // 5. Cleanup — delete the workflow so subsequent runs stay tidy.
  const cleanupResp = await request.delete(`/api/proxy/workflows/${workflow.id}`);
  expect(cleanupResp.status()).toBe(204);
});
