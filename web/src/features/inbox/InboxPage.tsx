import { Link, useSearchParams } from 'react-router-dom'
import { CheckCircle2, CircleCheck, Contrast, ShieldCheck } from 'lucide-react'
import { useInbox, useToday } from '../../api/hooks'
import { Select } from '../../ui/Field'
import { Card, CardHeader, EmptyState, Skeleton } from '../../ui/Card'
import { PageHeader } from '../../ui/PageHeader'
import { ago } from '../campaign/format'
import { ApprovalCard } from './ApprovalCard'
import { ReviewControls } from './ReviewControls'
import { groupByProgram } from './group'
import type { Schemas } from '../../api/v1'

function ReviewRow({ item }: { item: Schemas['InboxItem'] }) {
  return (
    <div className="flex items-start gap-3 px-4 py-3">
      <Contrast size={18} className="mt-0.5 shrink-0 text-abstain" aria-hidden />
      <div className="min-w-0 flex-1">
        <div className="text-sm font-medium"><span className="num">{item.candidate}</span> abstained
          <Link className="ml-2 text-xs font-normal text-accent hover:underline" to={`/campaigns/${item.campaign_id}?h=${encodeURIComponent(item.hypothesis_id ?? '')}`}>open evidence</Link></div>
        <div className="mt-0.5 text-xs text-muted">{item.detail}</div>
        <div className="mt-0.5 text-[11px] text-faint">{item.program && <><Link className="text-accent hover:underline" to={`/programs/${item.program.slug}`}>{item.program.name}</Link> · </>}{item.campaign_title}</div>
        <div className="mt-2"><ReviewControls campaignId={item.campaign_id!} hid={item.hypothesis_id!} candidate={item.candidate!} suggestion={item.suggestion} /></div>
      </div>
    </div>
  )
}

/** What was decided, by whom, and where to look — so a decision never just vanishes. */
function HistoryRow({ item }: { item: Schemas['InboxItem'] }) {
  const approval = item.kind === 'approval'
  return (
    <div className="flex items-start gap-3 px-4 py-2.5 text-[13px]">
      {approval ? <ShieldCheck size={15} className="mt-0.5 shrink-0 text-muted" /> : <CircleCheck size={15} className="mt-0.5 shrink-0 text-muted" />}
      <div className="min-w-0 flex-1">
        <span className="font-medium">{approval ? item.title.replace(/\s*\[problem\/[^\]]+\]/, '') : item.title}</span>
        <span className="text-muted">: {approval ? item.status : 'recorded'} by <b>{item.decided_by ?? 'someone'}</b></span>
        {item.detail && <span className="text-muted"> {item.detail}</span>}
        {item.decision === 'follow_up' && (item.follow_up_campaign_id
          ? <Link className="ml-2 text-xs text-accent hover:underline" to={`/campaigns/${item.follow_up_campaign_id}`}>follow-up campaign</Link>
          : <Link className="ml-2 text-xs font-medium text-warn hover:underline" to={`/campaigns/${item.campaign_id}?h=${encodeURIComponent(item.hypothesis_id ?? '')}`}>no campaign started: start it</Link>)}
        {item.program && <Link className="ml-2 text-xs text-accent hover:underline" to={`/programs/${item.program.slug}`}>{item.program.name}</Link>}
        {item.campaign_id && <Link className="ml-2 text-xs text-accent hover:underline" to={`/campaigns/${item.campaign_id}`}>{item.campaign_title ?? 'campaign'}</Link>}
      </div>
      <time className="num shrink-0 text-[11px] text-faint">{ago(item.ts)}</time>
    </div>
  )
}

/** The decisions list, for everything or for one program's campaigns. */
export function DecisionsPanel({ program, cells = false, grouped = false }: { program?: string; cells?: boolean; grouped?: boolean }) {
  const { data, isLoading } = useInbox('all', program)
  const pending = data?.filter((i) => i.status === 'pending') ?? []
  const approvals = pending.filter((i) => i.kind === 'approval')
  const reviews = pending.filter((i) => i.kind === 'review')
  const decided = (data?.filter((i) => i.status !== 'pending') ?? []).slice(0, 10)
  const header = (
      <PageHeader title="Decisions"
        cells={[
          { k: 'Pending', v: `${pending.length}`, s: pending.length ? 'nothing proceeds without a person' : 'nothing is held' },
          { k: 'Approvals', v: `${approvals.length}`, s: 'research plans and lab submissions' },
          { k: 'Reviews', v: `${reviews.length}`, s: 'abstentions to follow up, accept or drop' },
          { k: 'Decided', v: `${data?.filter((i) => i.status !== 'pending').length ?? 0}`, s: 'recorded under a name' },
        ]} />
  )
  return (
    <div>
      {cells && header}
      <div>
      {isLoading && <Skeleton className="h-28" />}
      {data && !pending.length && (
        <Card className="mb-5"><EmptyState icon={<CheckCircle2 size={28} className="text-ok" />} title="All clear" hint="No lab submission is paused and no abstention is pending a decision." /></Card>
      )}
      {grouped && !program ? groupByProgram(pending).map((g) => (
        <details key={g.slug} open className="mb-5 rounded-lg border border-line bg-surface">
          <summary className="flex cursor-pointer items-center gap-2 px-4 py-2.5 text-sm font-semibold">
            {g.slug ? <Link className="text-accent hover:underline" to={`/programs/${g.slug}`}>{g.name}</Link> : g.name}
            <span className="font-normal text-muted">· {g.items.length} pending{g.oldest ? ` · oldest ${ago(g.oldest)}` : ''}</span>
          </summary>
          <div className="divide-y divide-line border-t border-line">
            {g.items.map((i) => i.kind === 'approval' ? <ApprovalCard key={i.id} item={i} /> : <ReviewRow key={i.id} item={i} />)}
          </div>
        </details>
      )) : <>
      {!!approvals.length && (
        <Card className="mb-5">
          <CardHeader title="Approvals" hint="research plans and lab submissions on hold" />
          <div className="divide-y divide-line">{approvals.map((i) => <ApprovalCard key={i.id} item={i} />)}</div>
        </Card>
      )}
      {!!reviews.length && (
        <Card className="mb-5">
          <CardHeader title="Review" hint="abstentions nobody has decided on" />
          <div className="divide-y divide-line">{reviews.map((i) => <ReviewRow key={i.id} item={i} />)}</div>
        </Card>
      )}
      </>}
      {!!decided.length && (
        <Card>
          <CardHeader title="Recently decided" />
          <div className="divide-y divide-line">{decided.map((i) => <HistoryRow key={i.id} item={i} />)}</div>
        </Card>
      )}
      </div>
    </div>
  )
}

export default function InboxPage() {
  const [qs, setQs] = useSearchParams()
  const program = qs.get('program') ?? undefined
  const programs = useToday().data?.programs ?? []
  const grouped = qs.get('group') === 'program'
  const set = (next: { program?: string; group?: boolean }) => {
    const n: Record<string, string> = {}
    const p = 'program' in next ? next.program : program
    if (p) n.program = p
    if (('group' in next ? next.group : grouped) && !p) n.group = 'program'
    setQs(n, { replace: true })
  }
  return (
    <div className="mx-auto max-w-[1400px] p-6">
      <div className="mb-3 flex items-center gap-2 text-sm text-muted"><span>Program</span>
        <Select aria-label="Filter by program" className="!h-8 !w-56 !py-0" value={program ?? ''}
          onChange={(e) => set({ program: e.target.value || undefined })}>
          <option value="">All programs</option>
          {programs.map((p) => <option key={p.slug} value={p.slug}>{p.name}</option>)}
        </Select>
        {!program && (
          <label className="ml-4 flex items-center gap-1.5">
            <input type="checkbox" checked={grouped} onChange={(e) => set({ group: e.target.checked })} /> Group by program
          </label>
        )}
      </div>
      <DecisionsPanel program={program} cells grouped={grouped} />
    </div>
  )
}
