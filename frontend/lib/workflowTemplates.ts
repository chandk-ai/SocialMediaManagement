/**
 * Pre-built workflow templates. Each one is a partial config; the wizard
 * fills in the dynamic parts (sources, platforms, schedule) based on what
 * the user has connected. Templates make the empty-state actionable —
 * "Use a template" is a single click instead of figuring out config.
 */

export type WorkflowTemplate = {
  /** Stable id used to look up this template inside the wizard. */
  key: string;
  /** Card heading. */
  title: string;
  /** One-liner shown under the title. */
  tagline: string;
  /** Longer pitch, shown when the card is selected/expanded. */
  description: string;
  /** Lucide icon name (the consumer maps to the actual component). */
  icon: 'Newspaper' | 'Calendar' | 'MessageSquare' | 'FolderOpen' | 'Sparkles';
  /** Recommended source plugins. The wizard will guide the user to add at least one. */
  recommendedSources: string[];
  /** Recommended platform plugins. */
  recommendedPlatforms: string[];
  /** Pre-filled config for the workflow. */
  defaults: {
    name: string;
    description: string;
    config: {
      tone: string;
      audience: string;
      require_human_approval: boolean;
    };
    schedule: {
      kind: 'manual' | 'cron' | 'interval' | 'once';
      cron?: string;
      interval_minutes?: number;
      timezone: string;
    };
  };
};

export const WORKFLOW_TEMPLATES: WorkflowTemplate[] = [
  {
    key: 'daily_blog_to_social',
    title: 'Daily blog → LinkedIn + Instagram',
    tagline: 'Pull each new blog post and adapt it for LinkedIn + IG, daily at 9am.',
    description:
      "Polls one or more RSS feeds once a day. For each new post, the agents " +
      "draft a professional LinkedIn share and a punchy Instagram caption " +
      "with hashtags. Human approval before publishing.",
    icon: 'Newspaper',
    recommendedSources: ['rss'],
    recommendedPlatforms: ['linkedin', 'instagram'],
    defaults: {
      name: 'Daily blog → LinkedIn + Instagram',
      description: 'Adapt each new RSS post for LinkedIn and Instagram, daily.',
      config: {
        tone: 'professional',
        audience: 'general',
        require_human_approval: true,
      },
      schedule: {
        kind: 'cron',
        cron: '0 9 * * *',
        timezone: 'America/New_York',
      },
    },
  },
  {
    key: 'weekly_rss_digest',
    title: 'Weekly RSS digest',
    tagline: 'Summarise the week\'s top items into one Facebook + LinkedIn post.',
    description:
      "Every Monday morning, the agents read the past week of items from your " +
      "RSS feeds, pick the 3-5 most interesting, and craft a single round-up " +
      "post. Goes out after your approval.",
    icon: 'Calendar',
    recommendedSources: ['rss'],
    recommendedPlatforms: ['facebook', 'linkedin'],
    defaults: {
      name: 'Weekly RSS digest',
      description: 'A weekly round-up post from the past 7 days of feeds.',
      config: {
        tone: 'conversational',
        audience: 'general',
        require_human_approval: true,
      },
      schedule: {
        kind: 'cron',
        cron: '0 8 * * 1',
        timezone: 'America/New_York',
      },
    },
  },
  {
    key: 'manual_chat_driven',
    title: 'On-demand from chat',
    tagline: 'Type "post about X" in WhatsApp/Telegram → drafts everywhere.',
    description:
      "No schedule. You message a directive (e.g. \"post about today's food " +
      "drive\") in WhatsApp/Telegram/Slack and the agents fan it out as drafts " +
      "to all your connected accounts. You approve in the same chat.",
    icon: 'MessageSquare',
    recommendedSources: [],
    recommendedPlatforms: ['facebook', 'instagram', 'linkedin'],
    defaults: {
      name: 'On-demand from chat',
      description: 'Trigger by message; review in the same channel.',
      config: {
        tone: 'conversational',
        audience: 'general',
        require_human_approval: true,
      },
      schedule: { kind: 'manual', timezone: 'UTC' },
    },
  },
  {
    key: 'drive_doc_to_social',
    title: 'Google Drive doc → social',
    tagline: 'Draft a doc, save it, agents post excerpts everywhere.',
    description:
      "Watches a Drive folder. When a new doc lands (or an existing one is " +
      "edited), the agents extract the key points and draft platform-specific " +
      "posts. You approve before they go live.",
    icon: 'FolderOpen',
    recommendedSources: ['google_drive'],
    recommendedPlatforms: ['linkedin', 'facebook'],
    defaults: {
      name: 'Drive doc → social posts',
      description: 'Turn each Drive doc into ready-to-publish social copy.',
      config: {
        tone: 'professional',
        audience: 'general',
        require_human_approval: true,
      },
      schedule: { kind: 'interval', interval_minutes: 60, timezone: 'UTC' },
    },
  },
];

export function findTemplate(key: string): WorkflowTemplate | undefined {
  return WORKFLOW_TEMPLATES.find((t) => t.key === key);
}
