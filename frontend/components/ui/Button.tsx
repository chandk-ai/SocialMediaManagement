'use client';
/**
 * Button — thin wrapper around the .btn* CSS classes.
 *
 * Variants compose with the CSS in styles/globals.css. New variants
 * should be added there first so the class-based API
 * (``<button className="btn btn-primary">``) stays in sync.
 *
 * Loading prop: when ``true``, the button gets ``data-loading``
 * which (via globals.css) hides the inner content and overlays a
 * spinner. Pointer events disabled so the action can't double-fire.
 * The button remains accessible — screen readers announce
 * ``aria-busy``.
 */
import { cn } from '@/lib/utils';
import { ButtonHTMLAttributes, forwardRef } from 'react';
import { cva, type VariantProps } from 'class-variance-authority';

const button = cva('btn', {
  variants: {
    variant: {
      primary: 'btn-primary',
      ghost:   'btn-ghost',
      outline: 'btn-outline',
      danger:  'btn-danger',
    },
    size: {
      xs: 'btn-xs',
      sm: 'btn-sm',
      md: '',
      lg: 'btn-lg',
    },
  },
  defaultVariants: { variant: 'primary', size: 'md' },
});

export interface ButtonProps
  extends ButtonHTMLAttributes<HTMLButtonElement>,
    VariantProps<typeof button> {
  loading?: boolean;
}

export const Button = forwardRef<HTMLButtonElement, ButtonProps>(
  ({ className, variant, size, loading, children, disabled, ...props }, ref) => (
    <button
      ref={ref}
      data-loading={loading ? 'true' : undefined}
      aria-busy={loading || undefined}
      disabled={disabled || loading}
      className={cn(button({ variant, size }), className)}
      {...props}
    >
      {children}
      {loading && <span className="btn-spinner" aria-hidden="true" />}
    </button>
  ),
);
Button.displayName = 'Button';
