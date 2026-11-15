import { cn } from './cn'

/** Page tabs with visible boundaries: each tab is a boxed cell, the active one is a white surface with an ink top rule
 *  that joins the content below. The active tab is state in the URL (?tab=), so a view can be linked. */
export function Tabs<T extends string>({ tabs, value, onChange }: { tabs: { id: T; label: string }[]; value: T; onChange: (id: T) => void }) {
  return (
    <div role="tablist" className="mb-4 flex border-b border-line-strong">
      {tabs.map((t) => (
        <button key={t.id} role="tab" aria-selected={value === t.id} onClick={() => onChange(t.id)}
          className={cn('-mb-px border border-r-0 px-5 py-2 font-mono text-xs lowercase last:border-r',
            value === t.id
              ? 'border-line-strong border-t-2 border-t-ink border-b-canvas bg-canvas font-semibold text-ink'
              : 'border-line bg-sunken text-muted hover:bg-surface hover:text-ink')}>
          {t.label}
        </button>
      ))}
    </div>
  )
}
