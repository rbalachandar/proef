import { Link } from 'react-router-dom'
import { CheckCircle2, CircleCheck, Contrast, ShieldCheck } from 'lucide-react'
import { useInbox } from '../../api/hooks'
import { Card, CardHeader, EmptyState, Skeleton } from '../../ui/Card'
import { PageHeader } from '../../ui/PageHeader'
import { ago } from '../campaign/format'
import { ApprovalCard } from './ApprovalCard'
import { ReviewControls } from './ReviewControls'
import type { Schemas } from '../../api/v1'

function ReviewRow({ item }: { item: Schemas['InboxItem'] }) {
  return (
    <div className="flex items-start gap-3 px-4 py-3">
      <Contrast size={18} className="mt-0.5 shrink-0 text-abstain" aria-hidden />
      <div className="min-w-0 flex-1">
        <div className="text-sm font-medium"><span className="num">{item.candidate}</span> abstained
          <Link className="ml-2 text-xs font-normal text-accent hover:underline" to={`/campaigns/${item.campaign_id}?h=${encodeURIComponent(item.hypothesis_id ?? '')}`}>open evidence</Link></div>
        <div className="mt-0.5 text-xs text-muted">{item.detail}</div>
        <div className="mt-0.5 text-[11px] text-faint">{item.campaign_title}</div>
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
        {item.campaign_id && <Link className="ml-2 text-xs text-accent hover:underline" to={`/campaigns/${item.campaign_id}`}>{item.campaign_title ?? 'campaign'}</Link>}
      </div>
      <time className="num shrink-0 text-[11px] text-faint">{ago(item.ts)}</time>
    </div>
  )
}

export default function InboxPage() {
  const { data, isLoading } = useInbox('all')
  const pending = data?.filter((i) => i.status === 'pending') ?? []
  const approvals = pending.filter((i) => i.kind === 'approval')
  const reviews = pending.filter((i) => i.kind === 'review')
  const decided = (data?.filter((i) => i.status !== 'pending') ?? []).slice(0, 10)
  return (
    <div className="mx-auto max-w-[1400px] p-6">
      <PageHeader title="Decisions"
        cells={[
          { k: 'Pending', v: `${pending.length}`, s: pending.length ? 'nothing proceeds without a person' : 'nothing is held' },
          { k: 'Approvals', v: `${approvals.length}`, s: 'research plans and lab submissions' },
          { k: 'Reviews', v: `${reviews.length}`, s: 'abstentions to follow up, accept or drop' },
          { k: 'Decided', v: `${data?.filter((i) => i.status !== 'pending').length ?? 0}`, s: 'recorded under a name' },
        ]} />
      <div>
      {isLoading && <Skeleton className="h-28" />}
      {data && !pending.length && (
        <Card className="mb-5"><EmptyState icon={<CheckCircle2 size={28} className="text-ok" />} title="All clear" hint="No lab submission is paused and no abstention is pending a decision." /></Card>
      )}
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
