import { useState } from 'react'
import { Link } from 'react-router-dom'
import { useReviewHypothesis } from '../../api/hooks'
import { useToast } from '../../app/toast'
import type { Schemas } from '../../api/v1'
import { Button } from '../../ui/Button'
import { Input } from '../../ui/Field'
import { ago } from '../campaign/format'
import { FollowUpDialog } from '../launch/FollowUpDialog'

type Decision = Schemas['ReviewIn']['decision']
const DONE: Record<Decision, string> = { follow_up: 'followed up', accept: 'accepted as inconclusive', drop: 'dropped' }

/** Record a decision on an abstained candidate. "Follow up" starts the follow-up
 *  campaign FIRST and records the decision (linked to it) only once it started;
 *  accept / drop are recorded at once. Each is sealed in the ledger under YOUR name. */
const ACTION: Record<Schemas['Suggestion']['action'], string> = { follow_up: 'follow up', accept: 'accept as inconclusive', drop: 'drop' }

/** What the model suggested, after the lab's checks. A suggestion only: a person records every decision. */
export function SuggestionNote({ s }: { s: Schemas['Suggestion'] }) {
  const ladder = s.concentrations?.length ? `, doses ${s.concentrations[0].replace('uM', '')} to ${s.concentrations[s.concentrations.length - 1].replace('uM', '')} µM` : ''
  const reps = s.replicates_requested ? `, ${s.replicates_requested} replicates${s.replicates ? '' : ' requested'}` : ''
  return (
    <div role="note" className={`mb-2 border px-3 py-2 text-[13px] ${s.ok ? 'border-line bg-sunken' : 'border-warn/50 bg-warn-soft text-warn'}`}>
      <b className="font-medium">Model suggestion, not a decision.</b>{' '}
      {s.ok
        ? <>Suggests <b className="font-medium">{ACTION[s.action]}</b>{s.action === 'follow_up' ? `${reps}${ladder}` : ''}.{s.ran && ' A follow-up run was made from it.'}</>
        : <>Suggested {ACTION[s.action]}, but it did not pass the checks: {s.reasons.join(' ') || s.status}.</>}
      {s.ok && s.reasons.length > 0 && <span className="block text-[11px] text-faint">{s.reasons.join(' ')}</span>}
      {s.why && <span className="block text-[11px] text-faint">“{s.why}”</span>}
    </div>
  )
}

export function ReviewControls({ campaignId, hid, candidate, review, suggestion }: {
  campaignId: string; hid: string; candidate: string; review?: Schemas['Review'] | null; suggestion?: Schemas['Suggestion'] | null
}) {
  const m = useReviewHypothesis(campaignId)
  const toast = useToast()
  const [note, setNote] = useState('')
  const [showNote, setShowNote] = useState(false)
  const [fu, setFu] = useState(false)

  const record = (decision: Decision, followUpId?: string) =>
    m.mutateAsync({ hid, decision, note, followUpCampaignId: followUpId })
  if (review) {
    const pending = review.decision === 'follow_up' && !review.follow_up_campaign_id
    return (
      <div>
        <p className="text-[13px]"><b>{review.by}</b> {DONE[review.decision]} <span className="num">{candidate}</span>
          {review.note && <>: “{review.note}”</>}
          {review.follow_up_campaign_id && <> · <Link className="text-accent hover:underline" to={`/campaigns/${review.follow_up_campaign_id}`}>open the follow-up campaign</Link></>}
          <span className="text-faint"> · {ago(review.ts)}</span></p>
        {pending && (
          <div className="mt-2 flex items-center gap-2 text-[13px]">
            <span className="text-muted">No follow-up campaign was started for this.</span>
            <Button size="sm" variant="primary" onClick={() => setFu(true)}>Start it now…</Button>
          </div>
        )}
        <FollowUpDialog open={fu} onOpenChange={setFu} campaignId={campaignId} candidates={[candidate]} onLaunched={(cid) => record('follow_up', cid)} />
      </div>
    )
  }
  const quick = (decision: Decision) => record(decision).then(() =>
    toast({ tone: 'ok', text: `Recorded: ${DONE[decision]} ${candidate}. Sealed in the ledger.` }))

  return (
    <div>
      {suggestion && <SuggestionNote s={suggestion} />}
      {showNote && <Input autoFocus value={note} onChange={(e) => setNote(e.target.value)} placeholder="Note (recorded with the decision)" aria-label="Decision note" className="mb-2" />}
      <div className="flex flex-wrap items-center gap-2">
        <Button size="sm" variant="primary" disabled={m.isPending} onClick={() => setFu(true)}>Follow up…</Button>
        <Button size="sm" variant="secondary" disabled={m.isPending} onClick={() => quick('accept')}>Accept as inconclusive</Button>
        <Button size="sm" variant="ghost" disabled={m.isPending} onClick={() => quick('drop')}>Drop</Button>
        {!showNote && <button className="text-xs text-faint hover:text-ink" onClick={() => setShowNote(true)}>+ add a note</button>}
      </div>
      {m.error && <p role="alert" className="mt-1 text-xs text-bad">{(m.error as Error).message}</p>}
      <FollowUpDialog open={fu} onOpenChange={setFu} campaignId={campaignId} candidates={[candidate]}
        onLaunched={(cid) => record('follow_up', cid)} />
    </div>
  )
}
