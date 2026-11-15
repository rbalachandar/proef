import { Link } from 'react-router-dom'
import { Split } from 'lucide-react'
import { useTrees } from '../../api/hooks'
import { Button } from '../../ui/Button'
import { Card, CardHeader, EmptyState } from '../../ui/Card'
import { cn } from '../../ui/cn'
import type { Schemas } from '../../api/v1'

type Node = Schemas['TreeNodeView']
type Tree = Schemas['TreeView']

const LABEL: Record<string, string> = { achieved: 'achieved', partial: 'partly met', failed: 'not met', inconclusive: 'undecided', 'in lab': 'in the lab', open: 'not started' }

/** Shape first, colour second. circle = achieved, half = partly met, square = not met, diamond = undecided,
 *  dashed ring = in the lab, dotted ring = not started. */
export function NodeMark({ status, size = 12 }: { status: string; size?: number }) {
  const s = size, h = s / 2, c = 'currentColor'
  const tone = status === 'achieved' || status === 'partial' || status === 'in lab' ? 'text-ok' : status === 'failed' ? 'text-bad' : status === 'inconclusive' ? 'text-abstain' : 'text-faint'
  const body = {
    achieved: <circle cx={h} cy={h} r={h - 0.5} fill={c} />,
    partial: <><circle cx={h} cy={h} r={h - 1} fill="none" stroke={c} strokeWidth="1.6" /><path d={`M${h} 1 A${h - 1} ${h - 1} 0 0 1 ${h} ${s - 1} Z`} fill={c} /></>,
    failed: <rect x=".5" y=".5" width={s - 1} height={s - 1} fill={c} />,
    inconclusive: <path d={`M${h} 1 L${s - 1} ${h} L${h} ${s - 1} L1 ${h} Z`} fill="none" stroke={c} strokeWidth="1.8" />,
    'in lab': <><circle cx={h} cy={h} r={h - 1} fill="none" stroke={c} strokeWidth="1.5" strokeDasharray="2.4 2" /><circle cx={h} cy={h} r="1.8" fill={c} /></>,
    open: <circle cx={h} cy={h} r={h - 1.4} fill="none" stroke={c} strokeWidth="1.4" strokeDasharray="1.6 2.2" />,
  }[status] ?? <circle cx={h} cy={h} r={h - 1.4} fill="none" stroke={c} strokeWidth="1.4" strokeDasharray="1.6 2.2" />
  return <svg role="img" aria-label={LABEL[status] ?? status} width={s} height={s} viewBox={`0 0 ${s} ${s}`} className={cn('shrink-0', tone)}>{body}</svg>
}

const gateText = (n: Node) => (n.metric && n.gate != null ? `${n.metric} ${n.direction === 'min' ? '≤' : '≥'} ${n.gate}` : null)

function TreeView({ t, limits }: { t: Tree; limits: Schemas['Limits'] }) {
  const leaves = t.nodes.filter((n) => !t.nodes.some((k) => k.parent === n.id))
  const depth = Math.max(0, ...t.nodes.map((n) => n.depth))
  return (
    <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_300px]">
      <Card>
        <CardHeader title="Research tree" hint="a parent is achieved only when its required children are" />
        <div role="tree">
          <div role="treeitem" aria-selected className="rowsel grid grid-cols-[22px_1fr_auto] items-center gap-2.5 border-b border-line px-3.5 py-2.5">
            <NodeMark status={t.status} /><span className="text-sm font-semibold leading-snug">{t.objective}</span>
            <span className="num text-[11.5px] text-muted">{t.run_budget} runs shared</span>
          </div>
          {t.nodes.map((n) => {
            const g = gateText(n)
            const isLeaf = leaves.includes(n)
            const kids = t.nodes.filter((k) => k.parent === n.id).length
            return (
              <div key={n.id} role="treeitem" className="relative grid grid-cols-[22px_1fr_auto] items-center gap-2.5 border-b border-line px-3.5 py-2 last:border-0"
                style={{ paddingLeft: 14 + n.depth * 30 }}>
                <span aria-hidden className="absolute bottom-0 top-0 w-px bg-line-strong" style={{ left: 14 + n.depth * 30 - 14 }} />
                <span aria-hidden className="absolute top-1/2 h-px w-3 bg-line-strong" style={{ left: 14 + n.depth * 30 - 14 }} />
                <NodeMark status={n.status} />
                <div className="min-w-0">
                  <div className="text-[13px] leading-snug">
                    {n.campaign_id ? <Link className="hover:underline" to={`/campaigns/${n.campaign_id}`}>{n.objective}</Link> : n.objective}
                  </div>
                  <div className="num mt-0.5 text-[11.5px] text-muted">
                    {g ? <b className="font-medium text-ink">{g}</b> : isLeaf ? 'uses the program’s gate' : `split into ${kids} sub-problems`}
                    {n.status !== 'open' && <span className="text-faint"> · {LABEL[n.status] ?? n.status}</span>}
                  </div>
                </div>
                <span className="num text-[11.5px] text-muted">{n.runs != null ? `${n.runs} runs` : ''}</span>
              </div>
            )
          })}
        </div>
        <div className="flex flex-wrap gap-4 border-t border-line px-3.5 py-2.5 text-[11px] text-muted">
          {['achieved', 'partial', 'failed', 'inconclusive', 'in lab', 'open'].map((s) => (
            <span key={s} className="num inline-flex items-center gap-1.5"><NodeMark status={s} size={11} />{LABEL[s]}</span>
          ))}
        </div>
      </Card>
      <aside className="space-y-4">
        <Card className="px-3.5 py-3">
          <div className="label !text-[10px] text-faint">Budget flows down</div>
          <div className="mt-2.5 flex gap-[3px]" role="img" aria-label="Lab runs split across the leaves">
            {leaves.map((l) => <div key={l.id} style={{ flex: l.runs ?? 1 }} className="h-3.5 bg-accent" title={`${l.objective}: ${l.runs ?? '?'} runs`} />)}
          </div>
          <div className="mt-1.5 flex gap-[3px]">
            {leaves.map((l) => (
              <div key={l.id} style={{ flex: l.runs ?? 1 }} className="num min-w-0 truncate text-[10.5px] leading-tight text-muted">
                <b className="block font-medium text-ink">{l.runs ?? '?'}</b>{l.objective.split(' ')[0].toLowerCase()}
              </div>
            ))}
          </div>
          <p className="mt-2 text-xs text-muted">{t.run_budget} runs split by priority. A leaf never spends more than its slice.</p>
        </Card>
        <Card className="px-3.5 py-3">
          <div className="label !text-[10px] text-faint">Roll-up rule</div>
          <p className="num mt-1.5 text-[12px] leading-[1.8] text-muted">
            <b className="font-medium text-ink">achieved</b> all required children are<br />
            <b className="font-medium text-ink">not met</b> any required child is<br />
            <b className="font-medium text-ink">undecided</b> abstentions are not failures
          </p>
        </Card>
        <Card className="px-3.5 py-3">
          <div className="label !text-[10px] text-faint">Limits in use</div>
          <dl className="num mt-1.5 grid grid-cols-[1fr_auto] gap-y-1 text-[12px] text-muted">
            <dt>depth</dt><dd className="text-ink">{depth} of {limits.max_depth}</dd>
            <dt>sub-problems</dt><dd className="text-ink">{t.nodes.length} of {limits.max_subproblems}</dd>
            <dt>lab runs</dt><dd className="text-ink">{t.run_budget} of {limits.max_lab_runs}</dd>
          </dl>
        </Card>
      </aside>
    </div>
  )
}

/** The program's research trees: an objective split into sub-problems, each leaf its own campaign, results rolling up. */
export function ResearchTrees({ slug, limits, onSplit }: { slug: string; limits: Schemas['Limits']; onSplit: () => void }) {
  const { data: trees } = useTrees(slug)
  if (!trees?.length)
    return (
      <Card>
        <EmptyState title="No research tree yet"
          hint="A model can split the objective into separate, testable sub-problems. Each runs on its own gate, and the results roll up here."
          action={<Button size="sm" onClick={onSplit}><Split size={13} /> Split into sub-problems</Button>} />
      </Card>
    )
  return (
    <div className="space-y-6">
      {trees.map((t) => (
        <div key={t.id}>
          <p className="num mb-2 text-[11.5px] text-faint">started by {t.by}{t.ts ? ` · ${new Date(t.ts * 1000).toLocaleDateString(undefined, { day: 'numeric', month: 'short' })}` : ''}</p>
          <TreeView t={t} limits={limits} />
        </div>
      ))}
    </div>
  )
}
