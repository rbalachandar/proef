import { cva, type VariantProps } from 'class-variance-authority'
import { Slot } from '@radix-ui/react-slot'
import { forwardRef } from 'react'
import { cn } from './cn'

const button = cva(
  'inline-flex items-center justify-center gap-2 font-mono font-medium transition-colors ' +
    'disabled:opacity-50 disabled:pointer-events-none select-none',
  {
    variants: {
      variant: {
        primary: 'bg-ink text-canvas hover:opacity-85',
        secondary: 'bg-transparent text-ink border border-line-strong hover:bg-sunken',
        ghost: 'text-muted hover:bg-sunken hover:text-ink',
        danger: 'bg-bad text-canvas hover:opacity-90',
      },
      size: { sm: 'h-7 px-2.5 text-[11.5px]', md: 'h-8 px-3 text-xs', icon: 'h-8 w-8' },
    },
    defaultVariants: { variant: 'secondary', size: 'md' },
  },
)

export interface ButtonProps
  extends React.ButtonHTMLAttributes<HTMLButtonElement>,
    VariantProps<typeof button> {
  asChild?: boolean
}

export const Button = forwardRef<HTMLButtonElement, ButtonProps>(
  ({ className, variant, size, asChild, ...p }, ref) => {
    const Comp = asChild ? Slot : 'button'
    return <Comp ref={ref} className={cn(button({ variant, size }), className)} {...p} />
  },
)
Button.displayName = 'Button'
