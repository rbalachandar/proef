import { useState } from 'react'
import { Link, useParams, useSearchParams } from 'react-router-dom'
import { ClipboardCheck, Upload } from 'lucide-react'
import { useCampaign, usePauseCampaign } from '../../api/hooks'
import { PauseControl, PausedBanner } from '../pause/PauseControl'
import { Card, CardHeader, EmptyState, Skeleton } from '../../ui/Card'
import { Button } from '../../ui/Button'
import { StateMark } from '../../ui/Chip'
import { ContextStrip, Ticks } from '../../ui/ContextStrip'
import { Tabs } from '../../ui/Tabs'
import { StatusTag } from '../../routes/Campaigns'
import { ApprovalCard } from '../inbox/ApprovalCard'
import { RecordTab } from './RecordTab'
import { HypothesisDrawer } from './HypothesisDrawer'
import { OutcomeBar } from './OutcomeBar'
import { ResultsTable } from './ResultsTable'
import { RoundsTimeline } from './RoundsTimeline'
import { SubmitResultsDialog } from './SubmitResultsDialog'
import { sig } from './format'

const TABS = [{ id: 'overview', label: 'overview' }, { id: 'rounds', label: 'rounds' }, { id: 'record', label: 'record' }] as const
type Tab = (typeof TABS)[number]['id']
const NOTHING_TO_PAUSE: Record<string, string> = {
  completed: 'This campaign has finished. Nothing is left to pause.',
  declined: 'The plan was declined, so nothing was submitted.',
  interrupted: 'This campaign’s process has stopped. Start a follow-up to continue.',
  awaiting_results: 'This campaign is waiting for results you run by hand, so it submits nothing to the lab.',
}
const MODE: Record<string, string> = { gate: 'asks before lab submissions', act: 'runs on its own', observe: 'preview only' }

/** Keyed by id so state (the open drawer, ?h=) never leaks from one campaign to the next. */
export default function CampaignPage() {
  const { id = '' } = useParams()
  return <CampaignView key={id} id={id} />
}

function CampaignView({ id }: { id: string }) {
  const { data: d, isLoading, error } = useCampaign(id)
  const [qs, setQs] = useSearchParams()
  const [open, setOpen] = useState<string | null>(qs.get('h'))
  const [submitting, setSubmitting] = useState(false)
  const pauseCampaign = usePauseCampaign(id)
  const tab = (TABS.find((t) => t.id === qs.get('tab'))?.id ?? 'overview') as Tab
  const setTab = (t: Tab) => setQs((q) => { const n = new URLSearchParams(q); t === 'overview' ? n.delete('tab') : n.set('tab', t); return n }, { replace: true })

  if (isLoading) return <div className="mx-auto max-w-7xl space-y-4 p-6"><Skeleton className="h-10 w-2/3" /><Skeleton className="h-24" /><Skeleton className="h-72" /></div>
  if (error || !d) return <div className="mx-auto max-w-3xl p-10"><EmptyState title="Campaign not found" hint={(error as Error | null)?.message ?? 'It may have been archived.'} /></div>

  const undecided = d.hypotheses.filter((h) => h.state === 'abstained' && !h.review)
  const plan = d.plan_review
  return (
    <div className="mx-auto max-w-[1400px] p-6">
      <nav aria-label="Breadcrumb" className="label text-faint">
        <Link to="/campaigns" className="hover:text-ink">campaigns</Link>
        {d.program && <> / <Link to={`/programs/${d.program.slug}`} className="hover:text-ink">{d.program.name}</Link></>} / <span className="normal-case">{d.id}</span>
      </nav>
      <div className="mb-3.5 mt-2 flex items-start gap-4">
        <h1 className="max-w-[40em] text-[22px] font-semibold leading-snug tracking-tight">{d.objective ?? d.title}</h1>
        <div className="ml-auto flex shrink-0 items-center gap-2">
          <StatusTag status={d.status} paused={d.paused} />
          <PauseControl scope="campaign" paused={!!d.pause} busy={pauseCampaign.isPending}
            disabledReason={['running', 'executing'].includes(d.status) ? undefined : NOTHING_TO_PAUSE[d.status] ?? 'This campaign is not running, so there is nothing to pause.'}
            onSet={(paused, reason) => pauseCampaign.mutateAsync({ paused, reason })} />
          <Button size="sm" onClick={() => setSubmitting(true)}><Upload size={13} />Submit results</Button>
        </div>
      </div>

      <ContextStrip cells={[
        { k: 'Gate, pre-registered', v: d.gate.label ?? 'none', s: 'fixed before any data' },
        d.llm
          ? { k: 'Hypotheses', v: `${d.llm.models.join(', ')}${d.llm.proposed_hypotheses ? ` wrote ${d.llm.proposed_hypotheses}` : ''}`, s: d.llm.reviewed ? 'model reviewed the verdicts' : 'verdicts by the fixed rule' }
          : { k: 'Hypotheses', v: 'listed by hand', s: 'no language model involved' },
        plan
          ? { k: 'Plan', v: plan.approved ? `approved, ${plan.kept} of ${plan.proposed}` : 'declined', s: `${plan.by}${plan.removed ? ` · ${plan.removed} removed` : ''}`, tone: plan.approved ? undefined : 'warn' }
          : { k: 'Approvals', v: d.autonomy ? MODE[d.autonomy] ?? d.autonomy : 'none', s: 'no plan review' },
        { k: 'Runs', v: `${d.budget.runs_used}${d.budget.runs_max ? ` of ${d.budget.runs_max}` : ''}`, extra: d.budget.runs_max ? <Ticks used={d.budget.runs_used} total={d.budget.runs_max} /> : undefined },
        { k: 'Ledger', v: d.chain.verified ? 'verified' : 'integrity failed', s: `${d.chain.records} records`, tone: d.chain.verified ? undefined : 'bad' },
      ]} />

      <SubmitResultsDialog id={d.id} open={submitting} onOpenChange={setSubmitting} />
      {d.pause && <PausedBanner pause={d.pause} />}
      {!d.pause && d.paused_by_program && <PausedBanner pause={d.paused_by_program} via="program" />}
      {!!d.awaiting_approvals.length && (
        <Card className="mb-4 border-warn/50 bg-warn-soft">
          <div className="label px-4 pt-3 text-warn">Pending approval: the agent has paused</div>
          <div className="divide-y divide-line">{d.awaiting_approvals.map((a) => <ApprovalCard key={a.id} item={a} compact />)}</div>
        </Card>
      )}

      <Tabs tabs={[...TABS]} value={tab} onChange={setTab} />

      {tab === 'overview' && (
        <div className="grid gap-4 lg:grid-cols-[minmax(0,1fr)_320px]">
          <div className="min-w-0 space-y-4">
            <Card className="px-5 py-4"><OutcomeBar d={d} /></Card>
            <Card>
              <CardHeader title="Results" hint="click a candidate for the evidence" />
              <ResultsTable hyps={d.hypotheses} gate={d.gate} onOpen={(h) => setOpen(h.id)} selected={open} />
            </Card>
            {d.reviewer && (
              <Card className="flex items-start gap-3 px-4 py-3 text-sm">
                <ClipboardCheck size={16} className={d.reviewer.findings.length ? 'mt-0.5 text-warn' : 'mt-0.5 text-ok'} />
                <div className="flex-1">
                  <b>Independent reviewer</b>: {d.reviewer.findings.length
                    ? `${d.reviewer.critical} critical · ${d.reviewer.warnings} warnings`
                    : 'no findings. The analysis is internally consistent.'}
                  {d.reviewer.findings.map((f, i) => <p key={i} className="mt-1 text-xs text-muted"><b>{f.severity}</b> {f.candidate ? `${f.candidate}: ` : ''}{f.detail}</p>)}
                </div>
              </Card>
            )}
          </div>
          <aside className="space-y-4">
            <Card>
              <CardHeader title="Pending decision" right={<span className="num text-[11px] text-ink">{undecided.length}</span>} />
              {undecided.length === 0
                ? <p className="px-4 py-4 text-sm text-muted">No decision is pending.</p>
                : <ul className="divide-y divide-line">{undecided.map((h) => (
                    <li key={h.id} className="px-4 py-3">
                      <div className="flex items-center gap-2"><StateMark state="abstained" /><span className="num text-[13px]">{h.head}</span></div>
                      <p className="num mt-1 text-[11.5px] text-muted">
                        {h.verdict?.observed != null ? <><b className="font-medium text-ink">{sig(h.verdict.observed)}</b>{h.verdict.ci ? ` [${sig(h.verdict.ci[0])}, ${sig(h.verdict.ci[1])}]` : ''}. </> : null}
                        The interval crosses the gate{d.gate.threshold != null ? ` (${sig(d.gate.threshold)})` : ''}: no claim.
                      </p>
                      <Button size="sm" variant="primary" className="mt-2" onClick={() => setOpen(h.id)}>Decide</Button>
                    </li>))}</ul>}
              <p className="border-t border-line px-4 py-2.5 text-[11.5px] text-faint">Each decision is recorded in the ledger with the decider’s name.</p>
            </Card>
          </aside>
        </div>
      )}

      {tab === 'rounds' && (
        <div className="space-y-4">
          <Card>
            <CardHeader title="Rounds" hint="what was tested, and why" />
            <RoundsTimeline d={d} onOpen={setOpen} />
          </Card>
          {!!d.eliminated.length && (
            <Card>
              <CardHeader title="Eliminated before testing" right={<span className="num text-[11px] text-ink">{d.eliminated.length}</span>} />
              <ul className="divide-y divide-line">
                {d.eliminated.map((e) => (
                  <li key={e.candidate} className="px-4 py-2 text-[13px]"><span className="num font-medium">{e.candidate}</span> <span className="text-muted">: {e.reason}</span></li>
                ))}
              </ul>
            </Card>
          )}
        </div>
      )}

      {tab === 'record' && <RecordTab id={d.id} />}
      <HypothesisDrawer campaign={d} hid={open} onClose={() => setOpen(null)} />
    </div>
  )
}
