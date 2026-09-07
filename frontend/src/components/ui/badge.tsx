import * as React from 'react'
import { cva, type VariantProps } from 'class-variance-authority'
import { cn } from '@/lib/utils'

const badgeVariants = cva(
  'inline-flex items-center rounded-sm border px-2 py-0.5 font-mono text-[0.65rem] uppercase tracking-widest transition-colors',
  {
    variants: {
      variant: {
        default: 'border-transparent bg-neon/15 text-neon',
        secondary: 'border-edge-2 bg-panel-2 text-ink-dim',
        cyan: 'border-transparent bg-cyan/15 text-cyan',
        alert: 'border-transparent bg-alert/15 text-alert',
        amber: 'border-transparent bg-amber/15 text-amber',
        ok: 'border-transparent bg-ok/15 text-ok',
        outline: 'border-edge-2 text-ink-dim',
      },
    },
    defaultVariants: {
      variant: 'default',
    },
  },
)

export interface BadgeProps
  extends React.HTMLAttributes<HTMLDivElement>,
    VariantProps<typeof badgeVariants> {}

function Badge({ className, variant, ...props }: BadgeProps) {
  return <div className={cn(badgeVariants({ variant }), className)} {...props} />
}

export { Badge, badgeVariants }
