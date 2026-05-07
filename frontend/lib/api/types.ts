export type PluginInfo = {
  name: string;
  kind: 'platform' | 'source' | 'llm';
  display_name: string;
  api_version: string;
  description?: string;
  metadata?: Record<string, unknown>;
  config_schema?: Record<string, unknown>;
  capabilities?: Record<string, unknown>;
};

export type Platform = {
  id: string;
  plugin_name: string;
  display_name: string;
  account_handle?: string | null;
  account_external_id?: string | null;
  status: 'disconnected' | 'connected' | 'expired' | 'error';
  config: Record<string, unknown>;
  is_default?: boolean;
  tags?: string[];
  created_at: string;
  last_used_at: string | null;
};

export type PlatformGroup = {
  plugin_name: string;
  display_name: string;
  accounts: Platform[];
};

export type Source = {
  id: string;
  plugin_name: string;
  display_name: string;
  is_active: boolean;
  config: Record<string, unknown>;
  last_fetched_at: string | null;
  last_failure_at?: string | null;
  last_error?: string | null;
  error_count?: number;
  item_count?: number;
  created_at: string;
};

export type WorkflowConfig = {
  tone: string;
  audience: string;
  voice_guide?: string | null;
  max_revisions?: number;
  quality_threshold?: number;
  low_quality_threshold?: number;
  require_human_approval?: boolean;
  llm_provider?: string;
  llm_model?: string;
  extra?: Record<string, unknown>;
};

export type Workflow = {
  id: string;
  name: string;
  description: string;
  status: 'draft' | 'active' | 'paused' | 'archived';
  source_ids: string[];
  platform_ids: string[];
  config?: WorkflowConfig;
  schedule: { kind: string; cron?: string; interval_minutes?: number; run_at?: string; timezone: string };
  created_at: string;
  updated_at: string;
};

export type Post = {
  id: string;
  workflow_id: string;
  run_id: string;
  platform_id: string;
  text: string;
  hashtags: string[];
  status: 'draft' | 'review' | 'approved' | 'scheduled' | 'published' | 'failed';
  scheduled_for: string | null;
  published_at: string | null;
  external_post_id: string | null;
  error: string | null;
  created_at: string;
};

export type Trigger = {
  id: string;
  workflow_id: string;
  plugin_name: string;
  display_name: string;
  kind: 'manual' | 'schedule' | 'webhook' | 'whatsapp' | 'instagram' | 'telegram' | 'email' | 'slack';
  is_active: boolean;
  config: Record<string, unknown>;
  allowed_senders: string[];
  review_channel: string | null;
  review_recipient: string | null;
  created_at: string;
  last_fired_at: string | null;
};

export type Review = {
  id: string;
  run_id: string;
  workflow_id: string;
  channel: string;
  recipient: string;
  status: 'pending' | 'approved' | 'revision_requested' | 'rejected' | 'expired' | 'cancelled';
  drafts_snapshot: Array<{ platform_name: string; text: string; hashtags: string[] }>;
  feedback: string | null;
  decision_at: string | null;
  expires_at: string | null;
  created_at: string;
};
