'use client';
/**
 * Tiny toast system — no dependencies, accessible, dismissable.
 * Usage:
 *   import { toast } from '@/components/ui/Toast';
 *   toast.success('Saved!');
 *   toast.error('Could not connect');
 */
import { createContext, useCallback, useContext, useEffect, useState } from 'react';
import { CheckCircle2, AlertCircle, Info, X } from 'lucide-react';

type Tone = 'success' | 'error' | 'info';
type Item = { id: number; tone: Tone; message: string };

const ICONS = { success: CheckCircle2, error: AlertCircle, info: Info } as const;
const TONE_CLS: Record<Tone, string> = {
  success: 'border-emerald-200 bg-emerald-50 text-emerald-900',
  error:   'border-red-200 bg-red-50 text-red-900',
  info:    'border-ink-200 bg-white text-ink-900',
};

const Ctx = createContext<{ push: (t: Tone, m: string) => void } | null>(null);

let externalPush: ((t: Tone, m: string) => void) | null = null;

export const toast = {
  success: (m: string) => externalPush?.('success', m),
  error:   (m: string) => externalPush?.('error', m),
  info:    (m: string) => externalPush?.('info', m),
};

export function ToastProvider({ children }: { children: React.ReactNode }) {
  const [items, setItems] = useState<Item[]>([]);

  const push = useCallback((tone: Tone, message: string) => {
    const id = Date.now() + Math.random();
    setItems(prev => [...prev, { id, tone, message }]);
    setTimeout(() => setItems(prev => prev.filter(i => i.id !== id)), 4500);
  }, []);

  useEffect(() => { externalPush = push; return () => { externalPush = null; }; }, [push]);

  return (
    <Ctx.Provider value={{ push }}>
      {children}
      <div className="fixed top-4 right-4 z-[60] space-y-2 max-w-sm" aria-live="polite">
        {items.map(t => {
          const Icon = ICONS[t.tone];
          return (
            <div
              key={t.id}
              role="status"
              className={`pointer-events-auto flex items-start gap-3 rounded-xl border px-4 py-3 shadow-card animate-in fade-in slide-in-from-top-2 ${TONE_CLS[t.tone]}`}
            >
              <Icon size={16} className="mt-0.5 shrink-0" />
              <p className="text-sm flex-1">{t.message}</p>
              <button
                onClick={() => setItems(prev => prev.filter(i => i.id !== t.id))}
                className="opacity-50 hover:opacity-100"
                aria-label="Dismiss notification"
              >
                <X size={14} />
              </button>
            </div>
          );
        })}
      </div>
    </Ctx.Provider>
  );
}

export function useToast() {
  const ctx = useContext(Ctx);
  if (!ctx) throw new Error('useToast must be inside ToastProvider');
  return ctx;
}
