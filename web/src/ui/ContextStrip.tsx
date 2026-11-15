import { cn } from './cn'

export type Cell = { k: string; v: React.ReactNode; s?: React.ReactNode; tone?: 'warn' | 'bad'; extra?: React.ReactNode }

/** Facts about THIS page's subject (a campaign, a program), in one hairline strip under the title.
 *  The top bar holds only what is true everywhere; anything specific lives here. */
export function ContextStrip({ cells, className }: { cells: Cell[]; className?: string }) {
  return (
    <dl className={cn('panel mb-4 grid border border-line bg-surface', className)} style={{ gridTemplateColumns: `repeat(${cells.length}, minmax(0, 1fr))` }}>
      {cells.map((c, i) => (
        <div key={c.k} className={cn('min-w-0 px-3.5 py-2.5', i > 0 && 'border-l border-line')}>
          <dt className="label !text-[10px] text-faint">{c.k}</dt>
          <dd className={cn('num mt-1 truncate text-[12.5px]', c.tone === 'warn' && 'text-warn', c.tone === 'bad' && 'text-bad')}>{c.v}</dd>
          {c.s && <div className="num mt-px text-[11px] leading-snug text-muted">{c.s}</div>}
          {c.extra}
        </div>
      ))}
    </dl>
  )
}

/** A row of ticks: used of total, e.g. lab runs. */
export function Ticks({ used, total }: { used: number; total: number }) {
  const n = Math.min(Math.max(total, 1), 24)
  const f = Math.round((used / Math.max(total, 1)) * n)
  return <div aria-hidden className="mt-1.5 flex gap-[2px]">{Array.from({ length: n }, (_, i) => <i key={i} className={cn('block h-[5px] flex-1', i < f ? 'bg-ink' : 'bg-line-strong')} />)}</div>
}
