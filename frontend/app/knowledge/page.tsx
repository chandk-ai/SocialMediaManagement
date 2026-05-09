'use client';

/**
 * Knowledge base — Pillar 4.
 *
 * Operator-facing UI for the per-org RAG corpus. List documents grouped
 * by source kind (brand examples / style guides / compliance docs /
 * past posts). Add new documents (auto-chunked + embedded server-side).
 * Test the retriever with the search box. Delete what's stale.
 *
 * The corpus drives the Tailor agent (Pillar 5) and the Executor +
 * Critique steps — each retrieved chunk becomes context the agents
 * read at generation time.
 */
import { useEffect, useMemo, useState } from 'react';
import { fetchJSON } from '@/lib/api';
import { showToast } from '@/components/Toast';
import { BookOpen, Plus, Trash2, Search, Sparkles } from 'lucide-react';

type Doc = {
  id: string; title: string; source_kind: string;
  source_ref: string | null; metadata: Record<string, any>;
  created_at: string | null; updated_at: string | null;
  chunk_count: number;
};

type Hit = {
  id: string; doc_id: string; chunk_idx: number;
  content: string; doc_title: string; source_kind: string;
  similarity: number;
};

const KIND_LABEL: Record<string, string> = {
  manual: 'Brand example',
  style_guide: 'Style guide',
  compliance: 'Compliance',
  past_post: 'Past post',
};
const KIND_TONE: Record<string, string> = {
  manual: 'bg-blue-100 text-blue-800',
  style_guide: 'bg-purple-100 text-purple-800',
  compliance: 'bg-amber-100 text-amber-800',
  past_post: 'bg-green-100 text-green-800',
};

export default function KnowledgePage() {
  const [docs, setDocs] = useState<Doc[]>([]);
  const [loading, setLoading] = useState(true);
  const [showAdd, setShowAdd] = useState(false);
  const [title, setTitle] = useState('');
  const [content, setContent] = useState('');
  const [kind, setKind] = useState('manual');
  const [saving, setSaving] = useState(false);
  const [searchQ, setSearchQ] = useState('');
  const [hits, setHits] = useState<Hit[] | null>(null);
  const [searching, setSearching] = useState(false);

  async function load() {
    setLoading(true);
    try {
      const data = await fetchJSON('/api/v1/knowledge/documents?limit=500');
      setDocs(data.documents || []);
    } catch (e: any) {
      showToast({ title: 'Failed to load KB', body: e?.message || 'unknown', tone: 'error' });
    } finally {
      setLoading(false);
    }
  }
  useEffect(() => { load(); }, []);

  async function addDoc() {
    if (!title.trim() || !content.trim()) return;
    setSaving(true);
    try {
      await fetchJSON('/api/v1/knowledge/documents', {
        method: 'POST',
        body: JSON.stringify({ title, content, source_kind: kind }),
      });
      showToast({ title: 'Added to KB', body: title, tone: 'success' });
      setTitle(''); setContent(''); setShowAdd(false);
      load();
    } catch (e: any) {
      showToast({ title: 'Add failed', body: e?.message || 'unknown', tone: 'error' });
    } finally {
      setSaving(false);
    }
  }

  async function delDoc(id: string) {
    if (!confirm('Delete this document and all its chunks?')) return;
    try {
      await fetchJSON(`/api/v1/knowledge/documents/${id}`, { method: 'DELETE' });
      showToast({ title: 'Deleted', tone: 'success' });
      load();
    } catch (e: any) {
      showToast({ title: 'Delete failed', body: e?.message || 'unknown', tone: 'error' });
    }
  }

  async function runSearch() {
    if (!searchQ.trim()) { setHits(null); return; }
    setSearching(true);
    try {
      const data = await fetchJSON('/api/v1/knowledge/search', {
        method: 'POST',
        body: JSON.stringify({ query: searchQ, top_k: 8 }),
      });
      setHits(data.hits || []);
    } catch (e: any) {
      showToast({ title: 'Search failed', body: e?.message || 'unknown', tone: 'error' });
    } finally {
      setSearching(false);
    }
  }

  const grouped = useMemo(() => {
    const out: Record<string, Doc[]> = {};
    for (const d of docs) {
      const k = d.source_kind;
      if (!out[k]) out[k] = [];
      out[k].push(d);
    }
    return out;
  }, [docs]);

  return (
    <div className="p-6 max-w-6xl mx-auto space-y-6">
      <header className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-semibold flex items-center gap-2"><BookOpen size={20}/> Brand-voice knowledge base</h1>
          <p className="text-sm text-ink-600">Pillar 4 — examples, style guides, and compliance docs the AI agents retrieve from at generation time.</p>
        </div>
        <button className="btn btn-primary" onClick={() => setShowAdd(s => !s)}>
          <Plus size={14}/> Add document
        </button>
      </header>

      {showAdd && (
        <div className="rounded border bg-white p-4 space-y-3">
          <h2 className="text-base font-semibold">New document</h2>
          <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
            <input className="input md:col-span-2" placeholder="Title (e.g. 'Q4 launch tone notes')" value={title} onChange={e => setTitle(e.target.value)} />
            <select className="input" value={kind} onChange={e => setKind(e.target.value)}>
              <option value="manual">Brand example</option>
              <option value="style_guide">Style guide</option>
              <option value="compliance">Compliance</option>
              <option value="past_post">Past post</option>
            </select>
          </div>
          <textarea className="input min-h-[200px]" placeholder="Paste content. Long docs are auto-chunked + embedded." value={content} onChange={e => setContent(e.target.value)} />
          <div className="flex gap-2 justify-end">
            <button className="btn btn-ghost" onClick={() => setShowAdd(false)}>Cancel</button>
            <button className="btn btn-primary" onClick={addDoc} disabled={saving || !title.trim() || !content.trim()}>
              {saving ? 'Saving…' : 'Add'}
            </button>
          </div>
        </div>
      )}

      <div className="rounded border bg-white p-4">
        <h2 className="text-base font-semibold flex items-center gap-2"><Search size={14}/> Test the retriever</h2>
        <p className="text-xs text-ink-600 mb-3">Type a query — same path the agents use during generation.</p>
        <div className="flex gap-2">
          <input className="input flex-1" placeholder="e.g. 'how do we talk about pricing'" value={searchQ} onChange={e => setSearchQ(e.target.value)} onKeyDown={e => e.key === 'Enter' && runSearch()} />
          <button className="btn btn-outline" onClick={runSearch} disabled={searching}><Sparkles size={14}/> Search</button>
        </div>
        {hits !== null && (
          <div className="mt-3 space-y-2">
            {hits.length === 0 ? (
              <p className="text-sm text-ink-500">No hits. Add some examples or check that embeddings are configured.</p>
            ) : hits.map(h => (
              <div key={h.id} className="border rounded p-3 text-sm bg-stone-50">
                <div className="flex items-center justify-between text-xs text-ink-500 mb-1">
                  <span><strong>{h.doc_title}</strong> — {KIND_LABEL[h.source_kind] || h.source_kind} • chunk {h.chunk_idx}</span>
                  <span>sim {h.similarity.toFixed(3)}</span>
                </div>
                <p className="text-sm text-ink-800">{h.content}</p>
              </div>
            ))}
          </div>
        )}
      </div>

      {loading ? (
        <p className="text-sm text-ink-500 py-8 text-center">Loading…</p>
      ) : docs.length === 0 ? (
        <div className="rounded border bg-white p-8 text-center">
          <p className="text-sm text-ink-600">No documents yet. Add your first brand-voice example to get started.</p>
        </div>
      ) : (
        Object.entries(grouped).map(([k, ds]) => (
          <div key={k} className="rounded border bg-white">
            <div className="px-4 py-2 border-b flex items-center justify-between bg-stone-50">
              <span className={`px-2 py-0.5 rounded-full text-xs ${KIND_TONE[k] || 'bg-stone-100'}`}>{KIND_LABEL[k] || k}</span>
              <span className="text-xs text-ink-500">{ds.length} document{ds.length === 1 ? '' : 's'}</span>
            </div>
            <ul className="divide-y">
              {ds.map(d => (
                <li key={d.id} className="px-4 py-2 flex items-center gap-3">
                  <div className="flex-1 min-w-0">
                    <p className="text-sm font-medium truncate">{d.title}</p>
                    <p className="text-xs text-ink-500">{d.chunk_count} chunks • added {d.created_at ? new Date(d.created_at).toLocaleDateString() : '—'}</p>
                  </div>
                  <button className="btn btn-ghost btn-xs text-red-600" onClick={() => delDoc(d.id)} title="Delete">
                    <Trash2 size={12}/>
                  </button>
                </li>
              ))}
            </ul>
          </div>
        ))
      )}
    </div>
  );
}
