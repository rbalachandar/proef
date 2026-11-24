import { useState } from 'react'
import { Link, useNavigate, useParams, useSearchParams } from 'react-router-dom'
import { ArrowRight, CheckCircle2, ChevronLeft, CircleDashed, Compass, Plus, Sparkles, Split } from 'lucide-react'
import { usePauseProgram, useProgram } from '../../api/hooks'
import { PauseControl, PausedBanner } from '../pause/PauseControl'
import { limitsLine } from '../launch/limits'
import { Button } from '../../ui/Button'
import { Card, CardHeader, EmptyState, Skeleton } from '../../ui/Card'
import { Tag } from '../../ui/Chip'
import { StatusTag, when } from '../../routes/Campaigns'
import { MiniOutcome } from '../campaign/MiniOutcome'
import { ResultsTable } from '../campaign/ResultsTable'
import type { Hyp } from '../campaign/format'
import { sig } from '../campaign/format'
import { goalLine } from './goal'
import { LaunchCampaignDialog, type How } from '../launch/LaunchCampaignDialog'
import { NewProgramDialog } from '../launch/NewProgramDialog'
import { SplitDialog } from '../launch/SplitDialog'
import { DecisionsPanel } from '../inbox/InboxPage'
import { ResearchTrees } from './TreesCard'
import { ContextStrip } from '../../ui/ContextStrip'
import { Tabs } from '../../ui/Tabs'

export default function ProgramPage() {
  const { slug = '' } = useParams()
  const nav = useNavigate()
  const { data: p, isLoading, error } = useProgram(slug)
  const [launch, setLaunch] = useState(false)
  const [how, setHow] = useState<How>('research')
  const [follow, setFollow] = useState<string[] | null>(null)
  const [np, setNp] = useState(false)
  const [split, setSplit] = useState(false)
  const [splitObjective, setSplitObjective] = useState<string | undefined>()
  const pauseProgram = usePauseProgram(slug)
  const [qs, setQs] = useSearchParams()
  const tab = (['overview', 'tree', 'campaigns', 'decisions'].includes(qs.get('tab') ?? '') ? qs.get('tab') : 'overview') as 'overview' | 'tree' | 'campaigns' | 'decisions'
  const setTab = (t: typeof tab) => setQs((q) => { const n = new URLSearchParams(q); t === 'overview' ? n.delete('tab') : n.set('tab', t); return n }, { replace: true })
  if (isLoading) return <div className="mx-auto max-w-6xl space-y-4 p-6"><Skeleton className="h-10 w-1/2" /><Skeleton className="h-24" /><Skeleton className="h-64" /></div>
  if (error || !p) return <div className="mx-auto max-w-3xl p-10"><EmptyState title="Program not found" hint={(error as Error | null)?.message} /></div>

  const g = goalLine(p.goal, p.gate)
  const unit = p.gate.unit ?? ''
  // every judged candidate of every campaign, as rows against the SAME gate
  const hyps: Hyp[] = p.results.map((r) => ({
    id: `${r.campaign_id}|${r.hypothesis_id}`, head: r.candidate, candidate: r.candidate, statement: '', stage: '',
    state: r.state as Hyp['state'], selected: false,
    verdict: { status: (r.state === 'refuted' ? 'refuted' : r.state === 'abstained' ? 'abstained' : r.state) as 'validated', observed: r.observed, ci: r.ci, extrapolation: false },
  }))
  const campaignOf = new Map(p.results.map((r) => [`${r.campaign_id}|${r.hypothesis_id}`, r]))

  return (
    <div className="mx-auto max-w-[1400px] p-6">
      <nav aria-label="Breadcrumb" className="label text-faint"><Link to="/" className="hover:text-ink">programs</Link> / <span className="normal-case">{p.name}</span></nav>
      <div className="mb-3.5 mt-2 flex flex-wrap items-start gap-4">
        <h1 className="text-[22px] font-semibold tracking-tight">{p.name}</h1>
        <div className="ml-auto flex gap-2">
          <PauseControl scope="program" paused={!!p.pause} busy={pauseProgram.isPending}
            onSet={(paused, reason) => pauseProgram.mutateAsync({ paused, reason })} />
          <Button size="sm" onClick={() => { setHow('list'); setLaunch(true) }}><Plus size={14} /> Candidate list</Button>
          <Button size="sm" onClick={() => { setSplitObjective(undefined); setSplit(true) }} title="A model proposes how to split the objective into separately testable sub-problems"><Split size={14} /> Split into sub-problems</Button>
          <Button variant="primary" size="sm" onClick={() => { setHow('research'); setLaunch(true) }}><Sparkles size={14} /> Start research</Button>
        </div>
        {split && <SplitDialog open onOpenChange={setSplit} slug={p.slug} objective={splitObjective} />}
        <LaunchCampaignDialog open={launch} onOpenChange={setLaunch} slug={p.slug} initialHow={how}
          onSplit={(o) => { setSplitObjective(o || undefined); setSplit(true) }} />
      </div>
      {p.objective && <p className="mb-3.5 max-w-3xl text-sm text-muted">{p.objective}</p>}
      {p.pause && <PausedBanner pause={p.pause} />}

      <ContextStrip cells={[
        { k: 'Gate, pre-registered', v: p.gate.label ?? 'none', s: p.modality ?? undefined },
        { k: 'State', v: p.pause ? 'paused' : 'active', s: p.pause ? `by ${p.pause.by}` : 'submissions flow', tone: p.pause ? 'warn' : undefined },
        { k: 'Goal', v: g.title, s: g.tone === 'ok' ? 'something met the gate' : 'not met yet' },
        { k: 'Limits', v: `${p.limits.max_lab_runs} runs · ${p.limits.max_rounds} rounds`, s: `${p.limits.max_hypotheses} hypotheses · ${p.limits.breadth} per round` },
        { k: 'Splitting', v: `depth ${p.limits.max_depth} · ${p.limits.max_subproblems} sub-problems`, s: 'caps on a research tree' },
        { k: 'Campaigns', v: `${p.campaigns}`, s: `${p.goal.claimed} claimed · ${p.goal.refuted} refuted · ${p.goal.abstained} abstained` },
      ]} />
      <Tabs tabs={[{ id: 'overview', label: 'overview' }, { id: 'tree', label: 'research tree' }, { id: 'campaigns', label: 'campaigns' }, { id: 'decisions', label: 'decisions' }]} value={tab} onChange={setTab} />
      <NewProgramDialog open={np} onOpenChange={setNp} />
      {follow && <LaunchCampaignDialog open onOpenChange={() => setFollow(null)} slug={p.slug} title="Follow up"
        defaults={{ objective: `Follow-up: resolve ${follow.join(', ')}`, candidates: follow }} />}

      {tab === 'overview' && (
        <div className="space-y-4">
          <Card className={`px-5 py-4 ${g.tone === 'ok' ? 'rowsel' : ''}`}>
            <div className="flex items-start gap-3">
              {g.tone === 'ok' ? <CheckCircle2 className="mt-0.5 text-ok" size={18} /> : <CircleDashed className="mt-0.5 text-muted" size={18} />}
              <div><div className="text-[15px] font-semibold">{g.title}</div><div className="num text-[12.5px] text-muted">{g.detail}</div></div>
            </div>
          </Card>
          {!!p.next_steps.length && (
            <Card>
              <CardHeader title="What next" hint="from the evidence, no model needed" />
              <ul className="divide-y divide-line">
                {p.next_steps.map((n, i) => (
                  <li key={i} className="flex items-start gap-3 px-4 py-3">
                    <Compass size={16} className="mt-0.5 shrink-0 text-accent" aria-hidden />
                    <div className="min-w-0 flex-1"><div className="text-sm font-medium">{n.title}</div><div className="text-xs text-muted">{n.detail}</div></div>
                    <Button size="sm" variant="secondary" onClick={() => {
                      if (n.action === 'inbox') setQs({ tab: 'decisions' })
                      else if (n.action === 'open_campaign') nav(`/campaigns/${n.campaign_id}`)
                      else if (n.action === 'new_program') setNp(true)
                      else if (n.kind === 'follow_up') setFollow(n.candidates)
                      else setLaunch(true)
                    }}>{n.action === 'inbox' ? 'Open decisions' : n.action === 'open_campaign' ? 'Open campaign' : n.action === 'new_program' ? 'New program' : n.kind === 'follow_up' ? 'Follow up' : 'New campaign'} <ArrowRight size={13} /></Button>
                  </li>
                ))}
              </ul>
            </Card>
          )}
          <Card>
            <CardHeader title="Every candidate, one gate" hint="click one to open it in its campaign" />
            {hyps.length ? (
              <ResultsTable hyps={hyps} gate={p.gate} sub={(h) => campaignOf.get(h.id)?.campaign_title}
                onOpen={(h) => { const r = campaignOf.get(h.id); if (r) nav(`/campaigns/${r.campaign_id}?h=${encodeURIComponent(r.hypothesis_id)}`) }} />
            ) : <EmptyState title="Nothing judged yet" hint={`Candidates appear here once a campaign measures them against ${p.gate.label ?? 'the gate'}.`} />}
          </Card>
        </div>
      )}

      {tab === 'decisions' && <DecisionsPanel program={p.slug} />}
      {tab === 'tree' && <ResearchTrees slug={p.slug} limits={p.limits} onSplit={() => setSplit(true)} />}

      {tab === 'campaigns' && (
        <Card>
          <CardHeader title="Campaigns" hint={`${p.campaign_list.length}`} />
          {p.campaign_list.length ? (
            <ul className="divide-y divide-line">
              {p.campaign_list.map((c) => (
                <li key={c.id}>
                  <Link to={`/campaigns/${c.id}`} className="block px-4 py-3 hover:bg-sunken">
                    <div className="flex items-start gap-2"><span className="min-w-0 flex-1 text-sm font-medium leading-snug">{c.title}</span><StatusTag status={c.status} /></div>
                    <div className="mt-1.5 flex items-center gap-3 text-xs text-muted">
                      <MiniOutcome f={c.funnel} />
                      {c.best && <span>best <b className="num text-ink">{c.best.candidate}</b> {sig(c.best.observed)} {unit}</span>}
                      <span className="num ml-auto text-faint">{when(c.updated_at)}</span>
                    </div>
                  </Link>
                </li>
              ))}
            </ul>
          ) : <EmptyState title="No campaigns yet" hint="Launch one to start testing candidates against the gate." />}
        </Card>
      )}
    </div>
  )
}
