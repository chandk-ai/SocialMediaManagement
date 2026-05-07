'use client';
import { cn } from '@/lib/utils';
import { ButtonHTMLAttributes, forwardRef } from 'react';
import { cva, type VariantProps } from 'class-variance-authority';

const button = cva('btn', {
  variants: {
    variant: {
      primary: 'btn-primary',
      ghost: 'btn-ghost',
      outline: 'btn-outline',
      danger: 'btn bg-red-600 text-white hover:bg-red-700',
    },
    size: {
      sm: 'px-3 py-1.5 text-xs',
      md: '',
      lg: 'px-5 py-3 text-base',
    },
  },
  defaultVariants: { variant: 'primary', size: 'md' },
});

export interface ButtonProps
  extends ButtonHTMLAttributes<HTMLButtonElement>,
    VariantProps<typeof button> {}

export const Button = forwardRef<HTMLButtonElement, ButtonProps>(
  ({ className, variant, size, ...props }, ref) => (
    <button ref={ref} className={cn(button({ variant, size }), className)} {...props} />
  )
);
Button.displayName = 'Button';
