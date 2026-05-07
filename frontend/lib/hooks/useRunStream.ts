/**
 * Live workflow-run trace via Supabase Realtime.
 *
 * The backend writes to `smms.workflow_runs` as the orchestrator progresses;
 * Postgres-changes events flow over the realtime websocket and we render the
 * trace as it arrives — no polling.
 */
'use client';
import { useEffect, useState } from 'react';
import { getSupabase } from '@/lib/auth/supabase';

type Run = {
  id: string;
  status: string;
  trace: Array<Record<string, unknown>>;
  started_at: string | null;
  finished_at: string | null;
  error: string | null;
};

export function useRunStream(runId: string | null) {
  const [run, setRun] = useState<Run | null>(null);

  useEffect(() => {
    if (!runId) return;
    const sb = getSupabase();
    let mounted = true;

    sb.from('workflow_runs').select('*').eq('id', runId).single().then(({ data }) => {
      if (mounted && data) setRun(data as Run);
    });

    const channel = sb
      .channel(`run-${runId}`)
      .on(
        'postgres_changes',
        { event: 'UPDATE', schema: 'smms', table: 'workflow_runs', filter: `id=eq.${runId}` },
        ({ new: row }) => {
          if (mounted) setRun(row as Run);
        }
      )
      .subscribe();

    return () => {
      mounted = false;
      sb.removeChannel(channel);
    };
  }, [runId]);

  return run;
}
