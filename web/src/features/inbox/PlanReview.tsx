import { Link } from 'react-router-dom'
import { useState } from 'react'
import { BadgeCheck, BadgeX, Check, ClipboardList, X } from 'lucide-react'
import { useDecideApproval } from '../../api/hooks'
import { useToast } from '../../app/toast'
import type { Schemas } from '../../api/v1'
import { Button } from '../../ui/Button'
import { Tag } from '../../ui/Chip'
import { Input } from '../../ui/Field'
import { cn } from '../../ui/cn'
import { ago } from '../campaign/format'

type Item = Pick<Schemas['InboxItem'], 'id' | 'title' | 'ts'> & Partial<Schemas['InboxItem']> & { plan: Schemas['Plan'] }

/** The research plan a model proposed, shown BEFORE anything reaches the lab: read each idea's mechanism and what
 *  would refute it, remove any hypothesis you do not want tested, then approve or decline. */
export function PlanReview({ item, compact }: { item: Item; compact?: boolean }) {
  const decide = useDecideApproval()
  const toast = useToast()
  const [removed, setRemoved] = useState<Set<string>>(new Set())
  const [note, setNote] = useState('')
  const all = item.plan.families.flatMap((f) => f.variants)
  const kept = all.length - removed.size
  const toggle = (id: string) => setRemoved((s) => { const n = new Set(s); n.has(id) ? n.delete(id) : n.add(id); return n })
  const go = (approve: boolean) => decide.mutate({ id: item.id, approve, exclude: [...removed], note }, {
    onSuccess: () => toast({ tone: 'ok', text: approve ? `Plan approved: ${kept} of ${all.length} hypotheses go ahead.` : 'Plan declined. Nothing will be submitted to the lab.' }),
  })
  return (
    <div className="px-4 py-3">
      <div className="flex items-start gap-3">
        <ClipboardList size={18} className="mt-0.5 shrink-0 text-warn" aria-hidden />
        <div className="min-w-0 flex-1">
          <div className="text-sm font-medium">Review the research plan</div>
          <div className="text-xs text-muted">
            A model proposed {all.length} hypotheses in {item.plan.families.length} famil{item.plan.families.length === 1 ? 'y' : 'ies'}. Nothing reaches the lab until a decision is made. Verdicts come from the fixed rule, not the model.
            <span className="text-faint"> {ago(item.ts)}{!compact && item.campaign_id && <> · <Link className="text-accent hover:underline" to={`/campaigns/${item.campaign_id}`}>{item.campaign_title}</Link></>}</span>
          </div>
        </div>
      </div>

      {(() => {
        const ws = item.plan.warnings ?? []
        const dup = ws.filter((w) => w.kind === 'duplicate_compound')
        const again = ws.filter((w) => w.kind === 'already_tested')
        const untraced = ws.filter((w) => w.kind === 'untraced_citation')
        const untestable = ws.filter((w) => w.kind === 'untestable_refutation')
        const notInLib = ws.filter((w) => w.kind === 'not_in_library')
        const badStruct = ws.filter((w) => w.kind === 'bad_structure')
        const ruledOut = ws.filter((w) => w.kind === 'ruled_out')
        const misquote = ws.filter((w) => w.kind === 'quoted_number' || w.kind === 'gate_misstated')
        const note = 'mt-3 border border-warn/50 bg-warn-soft px-3 py-2 text-[13px] text-warn'
        return <>
          {!!dup.length && (
            <div role="note" className={note}>
              <b className="font-medium">Same compound under more than one family.</b>{' '}
              {dup.map((w, i) => (
                <span key={w.candidate}>{i > 0 && ', '}<span className="num">{w.candidate}</span> ({w.families.join(', ')})</span>
              ))}
              . One measurement judges them when their designed tests match. A variant with a different designed test waits for its own run.
            </div>
          )}
          {!!again.length && (
            <div role="note" className={note}>
              <b className="font-medium">Already tested in this campaign.</b>{' '}
              {again.map((w, i) => (
                <span key={w.candidate}>{i > 0 && ', '}<span className="num">{w.candidate}</span> ({w.verdict}{w.prior_family ? `, ${w.prior_family}` : ''})</span>
              ))}
              . Approving measures it again.
            </div>
          )}
          {!!untraced.length && (
            <div role="note" className={note}>
              <b className="font-medium">Cited, but not read in this run.</b>{' '}
              {untraced.map((w, i) => (
                <span key={w.family}>{i > 0 && ', '}<span className="num">{w.family}</span> ({w.citation}{w.citation_status === 'invalid' ? ', does not resolve' : w.citation_status === 'verified' ? ', resolves' : ''})</span>
              ))}
              . A real paper can still be cited from memory, but nothing the model retrieved contains it.
            </div>
          )}
          {!!untestable.length && (
            <div role="note" className={note}>
              <b className="font-medium">A “refuted if” that nothing could observe.</b>{' '}
              {untestable.map((w, i) => (<span key={w.family}>{i > 0 && ', '}<span className="num">{w.family}</span> ({w.reason})</span>))}
              . As written, no result could refute it.
            </div>
          )}
          {!!notInLib.length && (
            <div role="note" className={note}>
              <b className="font-medium">Not in the offered library.</b>{' '}
              {notInLib.map((w, i) => (<span key={w.candidate}>{i > 0 && ', '}<span className="num">{w.candidate}</span></span>))}
              . The model named a compound it was not offered. A simulator would still measure it; a real lab cannot.
            </div>
          )}
          {!!badStruct.length && (
            <div role="note" className={note}>
              <b className="font-medium">Structure problem.</b>{' '}
              {badStruct.map((w, i) => (<span key={w.candidate}>{i > 0 && ', '}<span className="num">{w.candidate}</span> ({w.reason})</span>))}
            </div>
          )}
          {!!ruledOut.length && (
            <div role="note" className={note}>
              <b className="font-medium">Already ruled out.</b>{' '}
              {ruledOut.map((w, i) => (<span key={`${w.candidate}-${i}`}>{i > 0 && ', '}<span className="num">{w.candidate}</span> ({w.reason})</span>))}
              . An earlier campaign judged it a refutation. Approving tests it again.
            </div>
          )}
          {!!misquote.length && (
            <div role="note" className={note}>
              <b className="font-medium">A number or threshold that does not match.</b>{' '}
              {misquote.map((w, i) => (<span key={`${w.candidate}-${i}`}>{i > 0 && ', '}<span className="num">{w.candidate}</span> ({w.reason})</span>))}
              . Checked against the results and the gate, not against whether the argument is right.
            </div>
          )}
        </>
      })()}
      {item.plan.critique && (
        <div className="mt-3 rounded-lg border border-line bg-sunken px-3 py-2 text-[13px]">
          <div className="mb-1 text-xs font-medium text-muted" title="A second model reviews the plan first. Advisory only: it cannot remove, change or approve anything.">
            Critic’s notes · <span className="num">{item.plan.critique.model}</span>
          </div>
          {item.plan.critique.notes.length === 0
            ? <p className="text-muted">The critic found nothing to raise.</p>
            : <ul className="space-y-1">{item.plan.critique.notes.map((n, i) => (
                <li key={i} className="flex gap-2">
                  <span className={n.severity === 'concern' ? 'text-warn' : 'text-faint'} aria-label={n.severity}>{n.severity === 'concern' ? '⚠' : '·'}</span>
                  <span><b className="num text-xs">{n.target === 'plan' ? 'Plan' : n.target}</b> · {n.text}</span>
                </li>))}
              </ul>}
        </div>
      )}

      <div className="mt-3 space-y-3">
        {item.plan.families.map((f) => (
          <section key={f.id} className="rounded-lg border border-line">
            <header className="flex flex-wrap items-center gap-2 border-b border-line px-3 py-2">
              <span className="num text-xs font-semibold">{f.id}</span>
              {f.confidence != null && <Tag title="the model's own confidence in this idea">model confidence {Math.round(f.confidence * 100)}%</Tag>}
              {f.citation && (f.citation_status === 'verified'
                ? <Tag tone="ok"><BadgeCheck size={11} className="mr-1" />citation verified</Tag>
                : f.citation_status === 'invalid'
                  ? <Tag tone="bad" title="This citation could not be resolved: treat it as unsupported"><BadgeX size={11} className="mr-1" />citation unresolvable</Tag>
                  : <Tag tone="warn">citation unchecked</Tag>)}
            </header>
            <div className="space-y-1.5 px-3 py-2 text-[13px]">
              {f.claim && <p className="font-medium">{f.claim}</p>}
              {f.mechanism && <p className="text-muted"><span className="text-faint">Mechanism · </span>{f.mechanism}</p>}
              {f.refuted_if && <p><span className="bg-bad-soft px-1.5 py-0.5 text-bad">Refuted if · {f.refuted_if}</span></p>}
              {f.grounding && (
                <div className="mt-1 border-l-2 border-line pl-2" data-testid="grounding">
                  <div className="text-xs text-faint">Evidence the model looked for · {f.grounding.calls} lookup{f.grounding.calls === 1 ? '' : 's'}</div>
                  {f.grounding.items.length === 0
                    ? <p className="text-muted">Nothing found that traces to a source.</p>
                    : <ul className="space-y-0.5">{f.grounding.items.map((g, i) => (
                        <li key={i} className="flex flex-wrap items-baseline gap-1.5">
                          <Tag tone={g.kind === 'contradicts' ? 'bad' : g.kind === 'supports' ? 'ok' : undefined}>{g.kind.replace('_', ' ')}</Tag>
                          <span>{g.claim}</span> <span className="num text-xs text-faint">{g.source}</span>
                        </li>))}
                      </ul>}
                  {f.grounding.dropped > 0 && <p className="text-xs text-faint">{f.grounding.dropped} further claim{f.grounding.dropped === 1 ? '' : 's'} not shown: no source in anything the model retrieved.</p>}
                </div>
              )}
            </div>
            <ul className="divide-y divide-line border-t border-line">
              {f.variants.map((v) => {
                const off = removed.has(v.hypothesis_id)
                return (
                  <li key={v.hypothesis_id}>
                    <label className="flex cursor-pointer items-start gap-2.5 px-3 py-2 hover:bg-sunken">
                      <input type="checkbox" className="mt-1 shrink-0" checked={!off} onChange={() => toggle(v.hypothesis_id)} aria-label={`Test ${v.candidate}`} />
                      <span className={cn('min-w-0 flex-1 text-[13px]', off && 'text-faint line-through')}>
                        <b className="num">{v.candidate}</b> <span className="text-muted">{v.statement}</span>
                        {v.predicted && <span className="mt-0.5 block text-[11px] text-faint"><span className="font-medium">Predicts</span> · {v.predicted}</span>}
                        {v.rationale && <span className="block text-[11px] text-faint"><span className="font-medium">Why</span> · {v.rationale}</span>}
                        {v.protocol && <span className="block text-[11px] text-faint"><span className="font-medium">Designed test</span> · {v.protocol}</span>}
                        {(v.predictions ?? []).map((p) => (
                          <span key={p.tool} className={cn('block text-[11px]', p.status === 'ok' ? 'text-faint' : 'text-warn')}>
                            <span className="font-medium">{p.status === 'ok' ? 'Prediction, a prior and not evidence' : 'Prediction not run'}</span> · <span className="num">{p.tool}{p.version ? ` v${p.version}` : ''}</span>{p.input && <> for <span className="num" title="the structure the model gave for this candidate: check it is the compound named">{p.input}</span></>}
                            {p.status === 'ok'
                              ? <>: {Object.entries(p.values).map(([k, x]) => `${k} ${typeof x === 'boolean' ? (x ? 'yes' : 'no') : Number(x.toFixed?.(2) ?? x)}${p.units[k] ? ' ' + p.units[k] : ''}`).join(', ')}
                                  {p.uncertainty === 'none' ? ', no uncertainty reported' : ''}{p.in_domain === false ? ', outside its domain' : ''}</>
                              : <>: {p.reason}</>}
                          </span>
                        ))}
                        {v.lab && (
                          <span className={cn('block text-[11px]', v.lab.ok ? 'text-faint' : 'text-bad')}>
                            <span className="font-medium">Runs on</span> · <span className="num">{v.lab.runs_on ?? 'unknown'}</span>
                            {v.lab.pick && v.lab.pick !== v.lab.runs_on && <> (model chose <span className="num">{v.lab.pick}</span>)</>}
                            {v.lab.status === 'unverified' && ', instrument declares no assay list'}
                            {v.lab.flags.includes('simulated_over_validated') && ', simulated chosen over a validated instrument'}
                            {!v.lab.ok && <> · {v.lab.status === 'cannot_run' ? 'Will not be submitted. ' : ''}{v.lab.reasons.join(' ')}</>}
                          </span>
                        )}
                      </span>
                    </label>
                  </li>
                )
              })}
            </ul>
          </section>
        ))}
      </div>

      <div className="mt-3 flex flex-wrap items-center gap-2">
        <Input className="min-w-0 flex-1" placeholder="Note for the record (optional), e.g. reason for a removal" value={note} onChange={(e) => setNote(e.target.value)} />
        <Button size="sm" variant="secondary" disabled={decide.isPending} onClick={() => go(false)}><X size={14} /> Decline plan</Button>
        <Button size="sm" variant="primary" disabled={decide.isPending || kept === 0} onClick={() => go(true)}>
          <Check size={14} /> Approve {removed.size ? `${kept} of ${all.length}` : `all ${all.length}`}
        </Button>
      </div>
      {kept === 0 && <p className="mt-1 text-right text-[11px] text-warn">Every hypothesis is removed: decline the plan instead.</p>}
      {decide.error && <div role="alert" className="mt-1 text-xs text-bad">{(decide.error as Error).message}</div>}
    </div>
  )
}
