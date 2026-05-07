import { cn } from '@/lib/utils';

const tones: Record<string, string> = {
  default: 'bg-ink-100 text-ink-700',
  success: 'bg-emerald-50 text-emerald-700',
  warning: 'bg-amber-50 text-amber-800',
  danger: 'bg-red-50 text-red-700',
  info:    'bg-accent-muted text-accent',
};

export function Badge({ children, tone = 'default', className }:
  { children: React.ReactNode; tone?: keyof typeof tones; className?: string }) {
  return <span className={cn('badge', tones[tone], className)}>{children}</span>;
}
