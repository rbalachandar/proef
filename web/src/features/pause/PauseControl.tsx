import { useState } from 'react'
import { Pause, Play } from 'lucide-react'
import { Button } from '../../ui/Button'
import { Dialog, DialogContent } from '../../ui/Dialog'
import { useToast } from '../../app/toast'
import type { Schemas } from '../../api/v1'

type Pause = Schemas['PauseState']

/** A routine pause, not the emergency stop: new lab submissions are held, running plates finish, and the work
 *  carries on from where it stopped when resumed. The global Halt in the top bar always overrides it. */
export function PauseControl({ scope, paused, busy, disabledReason, onSet }: {
  scope: 'program' | 'campaign'; paused: boolean; busy?: boolean; disabledReason?: string; onSet: (paused: boolean, reason: string) => Promise<unknown> | void
}) {
  const [open, setOpen] = useState(false)
  const [reason, setReason] = useState('')
  const toast = useToast()
  const go = async (next: boolean) => {
    try {
      await onSet(next, reason)
      setOpen(false); setReason('')
      toast({ tone: 'ok', text: next ? `This ${scope} is paused. New lab submissions are held until it is resumed.` : `Resumed. The ${scope} carries on.` })
    } catch (e) { toast({ tone: 'bad', text: (e as Error).message }) }
  }
  if (disabledReason && !paused) return <Button size="sm" disabled title={disabledReason}><Pause size={13} /> Pause</Button>
  if (paused) return <Button size="sm" variant="primary" disabled={busy} onClick={() => go(false)}><Play size={13} /> Resume</Button>
  return (
    <>
      <Button size="sm" onClick={() => setOpen(true)}><Pause size={13} /> Pause</Button>
      <Dialog open={open} onOpenChange={setOpen}>
        <DialogContent title={`Pause this ${scope}?`}
          description="New lab submissions are held until it is resumed. Running plates finish. Nothing is lost, and the work carries on from where it stopped.">
          <input autoFocus value={reason} onChange={(e) => setReason(e.target.value)} onKeyDown={(e) => e.key === 'Enter' && go(true)}
            placeholder="Reason (recorded in the ledger, optional)" className="mb-3 h-9 w-full border border-line-strong bg-canvas px-3 text-sm" />
          <div className="flex justify-end gap-2">
            <Button variant="ghost" onClick={() => setOpen(false)}>Cancel</Button>
            <Button variant="primary" disabled={busy} onClick={() => go(true)}>Pause {scope}</Button>
          </div>
        </DialogContent>
      </Dialog>
    </>
  )
}

/** Shown wherever something is held, so a paused program or campaign is never mistaken for a stuck one. */
export function PausedBanner({ pause, via, action }: { pause: Pause; via?: 'program'; action?: React.ReactNode }) {
  return (
    <div role="status" className="mb-4 flex items-center gap-3 border border-warn/50 bg-warn-soft px-4 py-2.5 text-sm text-warn">
      <Pause size={15} aria-hidden className="shrink-0" />
      <span className="min-w-0 flex-1">
        <b className="font-medium">{via === 'program' ? 'Held by its program.' : 'Paused.'}</b>{' '}
        {pause.by} paused it{pause.reason ? `: ${pause.reason}` : ''}. New lab submissions are held until it is resumed. Running plates finish.
      </span>
      {action}
    </div>
  )
}
