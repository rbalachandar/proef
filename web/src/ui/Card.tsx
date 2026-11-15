import { cn } from './cn'

export const Card = ({ className, ...p }: React.HTMLAttributes<HTMLDivElement>) => (
  <div className={cn('panel border border-line bg-surface', className)} {...p} />
)

export const CardHeader = ({ title, hint, right, className }: {
  title: React.ReactNode; hint?: React.ReactNode; right?: React.ReactNode; className?: string
}) => (
  <div className={cn('flex items-baseline gap-3 border-b border-line px-4 py-2.5', className)}>
    <h2 className="label text-muted">{title}</h2>
    {hint && <span className="num text-[11px] text-faint">{hint}</span>}
    {right && <div className="ml-auto">{right}</div>}
  </div>
)

export const Kbd = ({ children }: { children: React.ReactNode }) => (
  <kbd className="num border border-line-strong bg-sunken px-1.5 py-px text-[10px] text-muted">{children}</kbd>
)

export const Skeleton = ({ className }: { className?: string }) => (
  <div className={cn('animate-pulse bg-sunken', className)} />
)

export function EmptyState({ icon, title, hint, action }: {
  icon?: React.ReactNode; title: string; hint?: React.ReactNode; action?: React.ReactNode
}) {
  return (
    <div className="flex flex-col items-center gap-2 px-6 py-14 text-center">
      {icon && <div className="text-faint">{icon}</div>}
      <p className="text-sm font-medium">{title}</p>
      {hint && <p className="max-w-md text-xs text-muted">{hint}</p>}
      {action}
    </div>
  )
}
