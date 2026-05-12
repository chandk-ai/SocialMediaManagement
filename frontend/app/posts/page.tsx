'use client';
import { Card } from '@/components/ui/Card';
import { Badge } from '@/components/ui/Badge';
import { Button } from '@/components/ui/Button';
import { Input, Textarea } from '@/components/ui/Input';
import { Sidebar } from '@/components/layout/Sidebar';
import { TopBar } from '@/components/layout/TopBar';
import { EmptyState } from '@/components/ui/EmptyState';
import { useApi, api, ApiError } from '@/lib/api/client';
import type { Media, Post } from '@/lib/api/types';
import { FileText, Check, Edit3, Trash2, Send, X, AlertTriangle, Plus, Image as ImageIcon } from 'lucide-react';
import { useState } from 'react';
import { mutate } from 'swr';
import { formatDateTime } from '@/lib/utils';

const FILTERS = ['all', 'review', 'approved', 'scheduled', 'published', 'failed'] as const;

export default function PostsPage() {
  const [filter, setFilter] = useState<(typeof FILTERS)[number]>('all');
  const key = filter === 'all' ? '/posts' : `/posts?status=${filter}`;
  const { data } = useApi<Post[]>(key);
  const [editing, setEditing] = useState<Post | null>(null);

  return (
    <div className="flex h-screen">
      <Sidebar />
      <div className="flex-1 flex flex-col overflow-hidden">
        <TopBar title="Posts" />
        <main className="flex-1 overflow-y-auto p-6">
          <div className="mb-4 flex items-center gap-2">
            {FILTERS.map(f => (
              <button
                key={f}
                onClick={() => setFilter(f)}
                className={`badge ${filter === f ? 'bg-accent-muted text-accent' : 'bg-ink-100 text-ink-700'}`}
              >
                {f}
              </button>
            ))}
          </div>

          {data && data.length === 0 ? (
            <EmptyState
              icon={<FileText size={32} />}
              title="No posts in this view"
              description="Run a workflow to generate posts."
            />
          ) : (
            <div className="space-y-3">
              {(data ?? []).map(p => (
                <PostRow
                  key={p.id}
                  post={p}
                  swrKey={key}
                  onEdit={() => setEditing(p)}
                />
              ))}
            </div>
          )}
        </main>
      </div>
      {editing && (
        <EditDialog post={editing} swrKey={key} onClose={() => setEditing(null)} />
      )}
    </div>
  );
}

/* ───────── helpers ─────────────────────────────────────────────────── */

/** Strip the placeholder mock-LLM debug envelope so users see real content. */
function cleanText(raw: string): string {
  if (!raw) return '';
  // Mock LLM emits lines like: '{"echo": "Tone: professional...","system":"You are..."}'
  // Heuristic: if the value starts with `{"echo"`, drop everything inside that JSON object.
  let s = raw;
  s = s.replace(/\{"echo":[^}]*"system":[^}]*\}/g, '[draft pending real LLM output]');
  s = s.replace(/Hook:\s*\{"echo".*$/gm, 'Hook: (LLM output not yet wired)');
  s = s.replace(/Key messages:\s*\['?\{"echo".*$/gm, 'Key messages: (LLM output not yet wired)');
  return s.trim();
}

function PostRow({ post, swrKey, onEdit }: {
  post: Post;
  swrKey: string;
  onEdit: () => void;
}) {
  const [busy, setBusy] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);

  async function call(action: string, fn: () => Promise<unknown>) {
    setBusy(action); setErr(null);
    try {
      await fn();
      await mutate(swrKey);
    } catch (e) {
      const ae = e as ApiError;
      setErr(ae?.detail || (e as Error)?.message || 'Action failed.');
    } finally {
      setBusy(null);
    }
  }

  const canApprove = post.status === 'review' || post.status === 'draft';
  const canPublish = ['review', 'draft', 'approved', 'scheduled'].includes(post.status);

  return (
    <Card>
      <div className="flex items-start gap-4">
        <Badge tone={
          post.status === 'published' ? 'success' :
          post.status === 'failed'    ? 'danger'  :
          post.status === 'review'    ? 'warning' : 'default'
        }>{post.status}</Badge>

        <div className="min-w-0 flex-1">
          <p className="text-sm text-ink-900 whitespace-pre-line break-words">
            {cleanText(post.text)}
          </p>
          {post.hashtags.length > 0 && (
            <div className="mt-2 text-xs text-accent">{post.hashtags.join(' ')}</div>
          )}
          {post.media && post.media.length > 0 && (
            <div className="mt-2 flex gap-2 flex-wrap">
              {post.media.slice(0, 4).map((m, i) => (
                m.kind === 'video' ? (
                  <div
                    key={i}
                    className="w-16 h-16 rounded border border-ink-200 bg-ink-50 flex items-center justify-center text-[10px] text-ink-500"
                    title={m.url}
                  >
                    ▶ video
                  </div>
                ) : (
                  // eslint-disable-next-line @next/next/no-img-element
                  <img
                    key={i}
                    src={m.url}
                    alt={m.alt_text ?? ''}
                    className="w-16 h-16 rounded object-cover border border-ink-200 bg-ink-50"
                    onError={(e) => {
                      (e.currentTarget as HTMLImageElement).style.display = 'none';
                    }}
                  />
                )
              ))}
              {post.media.length > 4 && (
                <div className="w-16 h-16 rounded border border-ink-200 bg-ink-50 flex items-center justify-center text-xs text-ink-500">
                  +{post.media.length - 4}
                </div>
              )}
            </div>
          )}
          <div className="mt-2 flex flex-wrap items-center gap-2 text-xs text-ink-500">
            <span>created {formatDateTime(post.created_at)}</span>
            {post.scheduled_for && <span>· scheduled for {formatDateTime(post.scheduled_for)}</span>}
            {post.published_at && <span>· published {formatDateTime(post.published_at)}</span>}
            {post.error && <span className="text-red-600">· {post.error}</span>}
            {post.external_post_id && (
              <span className="font-mono">· id {post.external_post_id}</span>
            )}
          </div>
          {err && (
            <div className="mt-2 flex items-start gap-2 text-[11px] text-red-800 bg-red-50 border border-red-200 rounded p-2">
              <AlertTriangle size={12} className="mt-0.5 shrink-0" />
              <span>{err}</span>
            </div>
          )}
          <div className="mt-3 flex flex-wrap gap-1.5">
            <Button size="sm" variant="outline" onClick={onEdit}>
              <Edit3 size={12} /> Edit
            </Button>
            {canApprove && (
              <Button
                size="sm"
                variant="outline"
                disabled={busy !== null}
                onClick={() => call('approve', () => api.post(`/posts/${post.id}/approve`))}
              >
                <Check size={12} /> {busy === 'approve' ? 'Approving…' : 'Approve'}
              </Button>
            )}
            {canPublish && (
              <Button
                size="sm"
                disabled={busy !== null}
                onClick={() => call('publish', () => api.post(`/posts/${post.id}/publish_now`))}
              >
                <Send size={12} /> {busy === 'publish' ? 'Publishing…' : 'Publish now'}
              </Button>
            )}
            {post.status === 'failed' && (
              <Button
                size="sm"
                variant="outline"
                disabled={busy !== null}
                onClick={() => call('republish', () => api.post(`/posts/${post.id}/republish`))}
              >
                <Send size={12} /> Retry
              </Button>
            )}
            <Button
              size="sm"
              variant="ghost"
              disabled={busy !== null}
              onClick={async () => {
                if (!confirm('Delete this post?')) return;
                await call('delete', () => api.del(`/posts/${post.id}`));
              }}
            >
              <Trash2 size={12} /> Delete
            </Button>
          </div>
        </div>
      </div>
    </Card>
  );
}

function EditDialog({ post, swrKey, onClose }: {
  post: Post;
  swrKey: string;
  onClose: () => void;
}) {
  const [text, setText] = useState(cleanText(post.text));
  const [hashtags, setHashtags] = useState(post.hashtags.join(' '));
  const [scheduledFor, setScheduledFor] = useState(post.scheduled_for ? post.scheduled_for.slice(0, 16) : '');
  const [media, setMedia] = useState<Media[]>(post.media ?? []);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  function addMediaRow() {
    setMedia(prev => [...prev, { url: '', kind: 'image', alt_text: '' }]);
  }
  function updateMedia(idx: number, patch: Partial<Media>) {
    setMedia(prev => prev.map((m, i) => (i === idx ? { ...m, ...patch } : m)));
  }
  function removeMedia(idx: number) {
    setMedia(prev => prev.filter((_, i) => i !== idx));
  }

  async function save() {
    setBusy(true); setErr(null);
    try {
      const tags = hashtags
        .split(/\s+/)
        .map(s => s.trim())
        .filter(Boolean)
        .map(s => (s.startsWith('#') ? s : `#${s}`));

      // Reject obviously-bad media URLs client-side so the server
      // doesn't have to. Empty rows are silently dropped (lets the
      // user start typing a row then cancel).
      const cleanedMedia = media
        .map(m => ({ ...m, url: m.url.trim() }))
        .filter(m => m.url !== '');
      for (const m of cleanedMedia) {
        if (!/^https?:\/\//i.test(m.url)) {
          throw new Error(
            `Media URL must start with http:// or https://. Got "${m.url.slice(0, 60)}".`,
          );
        }
      }

      const body: Record<string, unknown> = {
        text, hashtags: tags, media: cleanedMedia,
      };
      if (scheduledFor) body.scheduled_for = new Date(scheduledFor).toISOString();
      await api.patch(`/posts/${post.id}`, body);
      await mutate(swrKey);
      onClose();
    } catch (e) {
      const ae = e as ApiError;
      setErr(ae?.detail || (e as Error)?.message || 'Save failed.');
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="fixed inset-0 bg-black/40 flex items-center justify-center p-4 z-50" onClick={onClose}>
      <Card className="w-full max-w-2xl max-h-[90vh] overflow-y-auto" onClick={(e: any) => e.stopPropagation()}>
        <div className="flex items-start justify-between mb-3">
          <h3 className="text-base font-semibold">Edit post</h3>
          <button onClick={onClose} className="text-ink-500 hover:text-ink-700">
            <X size={18} />
          </button>
        </div>
        <label className="block text-xs text-ink-500 mb-1">Text</label>
        <Textarea value={text} onChange={(e: any) => setText(e.target.value)} rows={6} />
        <label className="block text-xs text-ink-500 mt-3 mb-1">
          Hashtags (space-separated)
        </label>
        <Input value={hashtags} onChange={(e: any) => setHashtags(e.target.value)} placeholder="#brand #launch" />

        {/* ── Media section ───────────────────────────────────────── */}
        <div className="mt-4">
          <div className="flex items-center justify-between mb-1">
            <label className="block text-xs text-ink-500">
              Media (paste a public image / video URL)
            </label>
            <button
              type="button"
              onClick={addMediaRow}
              className="text-xs flex items-center gap-1 text-accent hover:underline"
            >
              <Plus size={12} /> Add media
            </button>
          </div>
          {media.length === 0 ? (
            <div className="text-xs text-ink-500 bg-ink-50 border border-dashed border-ink-200 rounded p-3 flex items-start gap-2">
              <ImageIcon size={14} className="mt-0.5 shrink-0" />
              <span>
                No media attached. Some platforms (Instagram, Pinterest)
                require an image or video. Paste a publicly reachable HTTPS
                URL — works with Google Drive (sharing link → "Anyone with
                the link"), Supabase Storage public buckets, S3 presigned
                URLs, Unsplash, your CDN, etc.
              </span>
            </div>
          ) : (
            <div className="space-y-2">
              {media.map((m, idx) => (
                <div key={idx} className="flex gap-2 items-start">
                  <div className="flex-1 space-y-1">
                    <Input
                      value={m.url}
                      onChange={(e: any) => updateMedia(idx, { url: e.target.value })}
                      placeholder="https://example.com/photo.jpg"
                    />
                    <div className="flex gap-2 items-center">
                      <select
                        value={m.kind}
                        onChange={e => updateMedia(idx, { kind: e.target.value as Media['kind'] })}
                        className="text-xs border border-ink-200 rounded px-2 py-1 bg-white"
                      >
                        <option value="image">Image</option>
                        <option value="video">Video</option>
                        <option value="gif">GIF</option>
                      </select>
                      <Input
                        value={m.alt_text ?? ''}
                        onChange={(e: any) => updateMedia(idx, { alt_text: e.target.value })}
                        placeholder="Alt text (accessibility, optional)"
                        className="flex-1 text-xs"
                      />
                    </div>
                  </div>
                  <button
                    type="button"
                    onClick={() => removeMedia(idx)}
                    className="text-ink-500 hover:text-red-600 p-1"
                    title="Remove this attachment"
                  >
                    <Trash2 size={14} />
                  </button>
                </div>
              ))}
            </div>
          )}
        </div>

        <label className="block text-xs text-ink-500 mt-4 mb-1">
          Scheduled for (optional)
        </label>
        <Input
          type="datetime-local"
          value={scheduledFor}
          onChange={(e: any) => setScheduledFor(e.target.value)}
        />
        {err && (
          <div className="mt-3 flex items-start gap-2 text-xs text-red-800 bg-red-50 border border-red-200 rounded p-2">
            <AlertTriangle size={14} className="mt-0.5 shrink-0" />
            <span>{err}</span>
          </div>
        )}
        <div className="mt-4 flex justify-end gap-2">
          <Button variant="ghost" onClick={onClose} disabled={busy}>Cancel</Button>
          <Button onClick={save} disabled={busy}>
            {busy ? 'Saving…' : 'Save changes'}
          </Button>
        </div>
      </Card>
    </div>
  );
}

