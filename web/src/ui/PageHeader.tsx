import { ContextStrip, type Cell } from './ContextStrip'

/** The same header on every page: a section label, the title, one plain sentence, and a strip of facts. */
export function PageHeader({ section, title, sub, cells, right }: {
  section?: string; title: React.ReactNode; sub?: React.ReactNode; cells?: Cell[]; right?: React.ReactNode
}) {
  return (
    <header className="mb-4">
      {section && <div className="label mb-2 text-faint">{section}</div>}
      <div className="mb-1 flex items-start gap-4">
        <h1 className="text-[22px] font-semibold tracking-tight">{title}</h1>
        {right && <div className="ml-auto flex shrink-0 gap-2">{right}</div>}
      </div>
      {sub && <p className="mb-3.5 max-w-3xl text-sm text-muted">{sub}</p>}
      {cells && <ContextStrip cells={cells} className="mb-0 mt-3.5" />}
    </header>
  )
}
