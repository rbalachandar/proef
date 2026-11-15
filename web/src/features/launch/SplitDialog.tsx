import { useEffect, useMemo, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { useDecompose, useProgram, useStartTree, useSystemStatus } from '../../api/hooks'
import { Button } from '../../ui/Button'
import { Dialog, DialogContent } from '../../ui/Dialog'
import { Input, Select } from '../../ui/Field'
import { useToast } from '../../app/toast'
import { AUTONOMY } from './LaunchCampaignDialog'

type Node = { id: string; parent?: string | null; objective: string; metric?: string | null; direction: 'min' | 'max'; gate?: number | null; priority: number; rationale: string; depth: number; keep: boolean }

/** A model splits the objective into a TREE of sub-problems, within the program's depth and size limits. The person
 *  keeps the real ones and sets each gate. Starting seals their decision and runs one budgeted campaign per leaf;
 *  the results roll up the tree. Nothing is recorded or run until they press the button. */
export function SplitDialog({ open, onOpenChange, slug }: { open: boolean; onOpenChange: (o: boolean) => void; slug: string }) {
  const model = useSystemStatus().data?.model
  const program = useProgram(slug).data
  const decompose = useDecompose(slug)
  const start = useStartTree(slug)
  const toast = useToast()
  const nav = useNavigate()
  const [nodes, setNodes] = useState<Node[] | null>(null)
  const [objective, setObjective] = useState('')
  const [autonomy, setAutonomy] = useState<keyof typeof AUTONOMY>('gate')

  useEffect(() => {
    if (open && model?.configured && !nodes && !decompose.isPending && !decompose.isError)
      decompose.mutate(undefined, { onSuccess: (d) => { setObjective(d.objective); setNodes(d.nodes.map((n) => ({ ...n, keep: true }))) } })
  }, [open, model?.configured])  // eslint-disable-line

  const byId = useMemo(() => new Map((nodes ?? []).map((n) => [n.id, n])), [nodes])
  const kept = (nodes ?? []).filter((n) => n.keep)
  const leaves = kept.filter((n) => !kept.some((k) => k.parent === n.id))
  const lim = program?.limits
  const toggle = (id: string, keep: boolean) => {
    // a sub-problem cannot outlive its parent: unticking a parent removes its subtree, ticking a child restores its parents
    const sub = new Set<string>([id])
    if (!keep) for (const n of nodes ?? []) if (n.parent && sub.has(n.parent)) sub.add(n.id)
    const up = new Set<string>()
    if (keep) for (let p = byId.get(id)?.parent; p; p = byId.get(p)?.parent) up.add(p)
    setNodes((nodes ?? []).map((n) => (sub.has(n.id) && !keep ? { ...n, keep: false } : n.id === id || up.has(n.id) ? { ...n, keep: true } : n)))
  }
  const patch = (id: string, p: Partial<Node>) => setNodes((nodes ?? []).map((n) => (n.id === id ? { ...n, ...p } : n)))
  const badGate = kept.some((n) => n.metric && n.gate == null)

  const go = () => start.mutate(
    { objective, autonomy, rounds: Math.min(2, lim?.max_rounds ?? 2),
      nodes: kept.map(({ keep: _k, ...n }) => ({ ...n, unit: null })) },
    { onSuccess: (t) => {
        toast({ tone: 'ok', text: `Research tree started: ${leaves.length} sub-problem${leaves.length > 1 ? 's' : ''} running, results roll up to the objective.` })
        onOpenChange(false)
        nav(`/programs/${slug}`)
        return t
      } })

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent title="Split into sub-problems" description="Keep the ones that are real, separate questions. Each leaf is judged against its own gate. Results roll up."
        className="!w-[min(680px,94vw)] !top-[5%]">
        {!model?.configured && <p role="alert" className="rounded-md bg-warn-soft px-3 py-2 text-xs text-warn"><b>No model is configured.</b> Splitting needs one (see docs/MODELS.md).</p>}
        {decompose.isPending && <p className="py-6 text-center text-sm text-muted">{model?.model} is reading the objective…</p>}
        {decompose.isError && <p role="alert" className="mb-2 text-xs text-bad">{(decompose.error as Error).message}</p>}
        {nodes && !nodes.length && <p className="py-4 text-sm text-muted">The model found nothing worth splitting: this is one question. Use Start research.</p>}
        {nodes && nodes.length > 0 && (
          <>
            <p className="mb-2 text-xs text-faint">Limits for this program: up to {lim?.max_depth} levels deep, {lim?.max_subproblems} sub-problems, {lim?.max_lab_runs} lab runs shared down the tree by priority. The server enforces them.</p>
            <ul className="mb-3 max-h-[46vh] space-y-1.5 overflow-auto pr-1">
              {nodes.map((n) => (
                <li key={n.id} style={{ marginLeft: (n.depth - 1) * 18 }} className={`rounded-lg border px-3 py-2 ${n.keep ? 'border-line' : 'border-line/50 opacity-55'}`}>
                  <label className="flex items-start gap-2">
                    <input type="checkbox" className="mt-1" checked={n.keep} onChange={(e) => toggle(n.id, e.target.checked)} />
                    <span className="min-w-0 text-sm">
                      <span className="font-medium">{n.objective}</span>
                      {!kept.some((k) => k.parent === n.id) && n.keep && <span className="ml-2 bg-sunken px-1.5 py-0.5 text-[10px] uppercase tracking-wide text-muted">runs as a campaign</span>}
                      {n.rationale && <span className="block text-xs text-faint">{n.rationale}</span>}
                    </span>
                  </label>
                  {n.keep && n.metric && n.gate != null && (
                    <span className="ml-6 mt-1 flex flex-wrap items-center gap-1.5 text-xs text-muted">
                      judged on <code className="num">{n.metric}</code>
                      <Select aria-label="direction" className="!h-7 !w-16 !py-0 text-xs" value={n.direction} onChange={(e) => patch(n.id, { direction: e.target.value as 'min' | 'max' })}>
                        <option value="min">≤</option><option value="max">≥</option>
                      </Select>
                      <Input aria-label={`gate for ${n.objective}`} type="number" step="any" className="num !h-7 !w-24 !py-0 text-xs" value={n.gate ?? ''}
                        onChange={(e) => patch(n.id, { gate: e.target.value === '' ? null : Number(e.target.value) })} />
                      <span className="text-faint">suggested by the model, editable</span>
                    </span>
                  )}
                </li>
              ))}
            </ul>
            <label className="mb-3 block text-xs text-muted">Approvals
              <Select value={autonomy} onChange={(e) => setAutonomy(e.target.value as keyof typeof AUTONOMY)}>
                {Object.entries(AUTONOMY).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
              </Select>
            </label>
          </>
        )}
        {start.isError && <p role="alert" className="mb-2 text-xs text-bad">{(start.error as Error).message}</p>}
        <div className="flex justify-end gap-2">
          <Button variant="ghost" onClick={() => onOpenChange(false)}>Cancel</Button>
          <Button variant="primary" disabled={start.isPending || !leaves.length || badGate} onClick={go}>
            {start.isPending ? 'Starting…' : `Start ${leaves.length || ''} sub-problem${leaves.length === 1 ? '' : 's'}`}
          </Button>
        </div>
      </DialogContent>
    </Dialog>
  )
}
