import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { useDecompose, useProgram, useSystemStatus } from '../../api/hooks'
import { api, unwrap } from '../../api/v1'
import { useQueryClient } from '@tanstack/react-query'
import { Button } from '../../ui/Button'
import { Dialog, DialogContent } from '../../ui/Dialog'
import { Select } from '../../ui/Field'
import { useToast } from '../../app/toast'
import { AUTONOMY } from './LaunchCampaignDialog'

type Sub = { objective: string; metric?: string | null; direction: 'min' | 'max'; gate?: number | null; rationale: string; keep: boolean }

/** A model splits the program's objective into separately testable sub-problems; the person keeps the ones that
 *  are real questions. Each kept one becomes its own research run, judged against its OWN gate, sharing the
 *  program's limits. Nothing is recorded or run until they press the button. */
export function SplitDialog({ open, onOpenChange, slug }: { open: boolean; onOpenChange: (o: boolean) => void; slug: string }) {
  const model = useSystemStatus().data?.model
  const program = useProgram(slug).data
  const decompose = useDecompose(slug)
  const toast = useToast()
  const nav = useNavigate()
  const qc = useQueryClient()
  const [subs, setSubs] = useState<Sub[] | null>(null)
  const [autonomy, setAutonomy] = useState<keyof typeof AUTONOMY>('gate')
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState<string | null>(null)

  useEffect(() => {
    if (open && model?.configured && !subs && !decompose.isPending && !decompose.isError)
      decompose.mutate(undefined, { onSuccess: (d) => setSubs(d.sub_problems.map((s) => ({ ...s, keep: true }))) })
  }, [open, model?.configured])  // eslint-disable-line

  const kept = (subs ?? []).filter((s) => s.keep)
  const go = async () => {
    setBusy(true); setErr(null)
    try {
      let first = ''
      for (const s of kept) {
        const c = await unwrap(api.POST('/api/v1/programs/{slug}/campaigns', { params: { path: { slug } }, body: {
          sub_problem: { objective: s.objective, metric: s.metric ?? null, direction: s.direction, gate: s.gate ?? null, priority: 1, rationale: s.rationale },
          share: kept.length, autonomy, rounds: Math.min(2, program?.limits.max_rounds ?? 2), replan: false, candidates: [], mode: 'agent' } }))
        first = first || c.id
      }
      await qc.invalidateQueries()
      toast({ tone: 'ok', text: `${kept.length} research run${kept.length > 1 ? 's' : ''} started, one per sub-problem. They share the program's limits.` })
      onOpenChange(false)
      nav(kept.length === 1 ? `/campaigns/${first}` : '/campaigns')
    } catch (e) { setErr((e as Error).message) } finally { setBusy(false) }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent title="Split into sub-problems" description="Keep the ones that are separate, testable questions. Each is judged against its own gate."
        className="!w-[min(640px,94vw)] !top-[6%]">
        {!model?.configured && <p role="alert" className="rounded-md bg-warn-soft px-3 py-2 text-xs text-warn"><b>No model is configured.</b> Splitting needs one (see docs/MODELS.md).</p>}
        {decompose.isPending && <p className="py-6 text-center text-sm text-muted">{model?.model} is reading the objective…</p>}
        {decompose.isError && <p role="alert" className="mb-2 text-xs text-bad">{(decompose.error as Error).message}</p>}
        {subs && !subs.length && <p className="py-4 text-sm text-muted">The model found nothing worth splitting: this is one question. Use Start research.</p>}
        {subs && subs.length > 0 && (
          <ul className="mb-3 space-y-2">
            {subs.map((s, i) => (
              <li key={i} className="rounded-lg border border-line px-3 py-2">
                <label className="flex items-start gap-2">
                  <input type="checkbox" className="mt-1" checked={s.keep} onChange={(e) => setSubs(subs.map((x, j) => (j === i ? { ...x, keep: e.target.checked } : x)))} />
                  <span className="min-w-0 text-sm">
                    <span className="font-medium">{s.objective}</span>
                    <span className="num mt-0.5 block text-xs text-muted">{s.metric && s.gate != null ? `judged on ${s.metric} ${s.direction === 'min' ? '≤' : '≥'} ${s.gate}` : 'no separate gate: uses the program’s'}</span>
                    {s.rationale && <span className="block text-xs text-faint">{s.rationale}</span>}
                  </span>
                </label>
              </li>
            ))}
          </ul>
        )}
        {subs && subs.length > 0 && (
          <div className="mb-3 grid grid-cols-[1fr] gap-3">
            <label className="text-xs text-muted">Approvals
              <Select value={autonomy} onChange={(e) => setAutonomy(e.target.value as keyof typeof AUTONOMY)}>
                {Object.entries(AUTONOMY).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
              </Select>
            </label>
          </div>
        )}
        {err && <p role="alert" className="mb-2 text-xs text-bad">{err}</p>}
        <div className="flex justify-end gap-2">
          <Button variant="ghost" onClick={() => onOpenChange(false)}>Cancel</Button>
          <Button variant="primary" disabled={busy || !kept.length} onClick={go}>{busy ? 'Starting…' : `Start research on ${kept.length || ''} sub-problem${kept.length === 1 ? '' : 's'}`}</Button>
        </div>
      </DialogContent>
    </Dialog>
  )
}
