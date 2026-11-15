import { useState } from 'react'
import { useActivity, useAgents } from '../../api/hooks'
import { Button } from '../../ui/Button'
import { Card, CardHeader, Skeleton } from '../../ui/Card'
import { cn } from '../../ui/cn'
import { TraceDrawer } from './AgentsCard'
import { ago } from './format'

/** Filter groups are ledger event types, so a filter means exactly what the record says. */
const VERDICTS = ['hypothesis_validated', 'hypothesis_invalidated', 'hypothesis_inconclusive', 'hypothesis_confirmed', 'hypothesis_refuted']
const GROUPS = [
  { id: 'all', label: 'everything', events: null },
  { id: 'decisions', label: 'decisions', events: ['gate_decision'] },
  { id: 'hypotheses', label: 'hypotheses', events: ['hypothesis_proposed', 'hypotheses_filtered', 'tool_invocation', 'problem_defined'] },
  { id: 'lab', label: 'lab runs', events: ['lab_run', 'synthesis_complete', 'hypothesis_tested'] },
  { id: 'verdicts', label: 'verdicts', events: VERDICTS },
] as const

/** Humans, models and plain code are different kinds of actor and read differently in a record. */
const kindOf = (a: { kind: string; model_version?: string | null }) => (a.kind === 'human' ? 'human' : a.model_version || a.kind === 'agent' ? 'model' : 'code')

/** The campaign's record: who did what, and a filterable log of everything sealed in the ledger. */
export function RecordTab({ id }: { id: string }) {
  const { data: actors } = useAgents(id)
  const { data: log, isLoading } = useActivity(id, 200)
  const [who, setWho] = useState<string | null>(null)
  const [group, setGroup] = useState<(typeof GROUPS)[number]['id']>('all')
  const [trace, setTrace] = useState<string | null>(null)
  const events = GROUPS.find((g) => g.id === group)?.events
  const rows = (log ?? []).filter((r) => (!who || r.actor === who) && (!events || (events as readonly string[]).includes(r.event ?? '')))
  const total = (actors ?? []).reduce((n, a) => n + a.events, 0)

  return (
    <div className="grid gap-4 lg:grid-cols-[300px_minmax(0,1fr)]">
      <Card>
        <CardHeader title="Who did what" right={<span className="num text-[11px] text-ink">{actors?.length ?? ''}</span>} />
        {!actors ? <div className="p-4"><Skeleton className="h-16" /></div> : (
          <ul>
            <li>
              <button onClick={() => setWho(null)} aria-pressed={!who} className={cn('block w-full border-b border-line px-3.5 py-2.5 text-left', !who ? 'rowsel' : 'hover:bg-sunken')}>
                <span className="num flex justify-between text-[13px]">all actors <span className="text-muted">{total}</span></span>
                <span className="text-[11.5px] text-muted">every record in this campaign</span>
              </button>
            </li>
            {actors.map((a) => {
              const k = kindOf(a)
              return (
                <li key={a.id}>
                  <button onClick={() => setWho(a.id)} aria-pressed={who === a.id}
                    className={cn('block w-full border-b border-line px-3.5 py-2.5 text-left last:border-0', who === a.id ? 'rowsel' : 'hover:bg-sunken')}>
                    <span className="num flex items-center justify-between text-[13px]">
                      <span className="flex items-center gap-2">{a.id}
                        <span className={cn('label !text-[9.5px] border px-1', k === 'human' ? 'border-ink bg-ink text-canvas' : 'border-line-strong text-muted')}>{k}</span>
                      </span>
                      <span className="text-muted">{a.events}</span>
                    </span>
                    <span className="mt-0.5 block text-[11.5px] leading-snug text-muted">{a.role}</span>
                    <span className="mt-1.5 block h-[3px] bg-line"><i className="block h-full bg-ink opacity-75" style={{ width: `${Math.max(2, (a.events / Math.max(total, 1)) * 100)}%` }} /></span>
                  </button>
                </li>
              )
            })}
          </ul>
        )}
      </Card>

      <Card>
        <CardHeader title="Record" hint="newest first" right={who && <Button size="sm" onClick={() => setTrace(who)}>Full trace of {who}</Button>} />
        <div className="flex flex-wrap items-center gap-2 border-b border-line px-3.5 py-2 font-mono text-[11.5px] text-muted">
          <span>show</span>
          {GROUPS.map((g) => (
            <button key={g.id} onClick={() => setGroup(g.id)} aria-pressed={group === g.id}
              className={cn('border px-2.5 py-0.5', group === g.id ? 'border-ink bg-ink text-canvas' : 'border-line-strong hover:bg-sunken')}>{g.label}</button>
          ))}
        </div>
        {isLoading ? <div className="space-y-2 p-4">{[0, 1, 2, 3].map((i) => <Skeleton key={i} className="h-5" />)}</div>
          : !rows.length ? <p className="p-6 text-center text-sm text-faint">Nothing matches this filter.</p>
          : (
            <table className="w-full border-collapse text-left">
              <thead><tr className="label !text-[10px] text-faint"><th className="px-3.5 py-2 font-medium">Time</th><th className="px-3.5 py-2 font-medium">Actor</th><th className="px-3.5 py-2 font-medium">Event</th><th className="px-3.5 py-2 text-right font-medium">Hash</th></tr></thead>
              <tbody>
                {rows.map((r, i) => (
                  <tr key={i} className="border-t border-line align-top">
                    <td className="num whitespace-nowrap px-3.5 py-1.5 text-[11.5px] text-faint">{ago(r.ts)}</td>
                    <td className="num whitespace-nowrap px-3.5 py-1.5 text-[12px] text-muted">{r.actor ?? ''}</td>
                    <td className="px-3.5 py-1.5 text-[13px]">{r.text}</td>
                    <td className="num px-3.5 py-1.5 text-right text-[11.5px] text-faint" title="First characters of this record’s hash in the chain">{r.hash ? `#${r.hash}` : ''}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
      </Card>
      <TraceDrawer id={id} actor={trace} onClose={() => setTrace(null)} />
    </div>
  )
}
