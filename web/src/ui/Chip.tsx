import { cn } from './cn'

export type State =
  | 'confirmed' | 'validated' | 'abstained' | 'refuted' | 'refused'
  | 'in_lab' | 'selected' | 'not_selected' | 'proposed'

/** One visual language for hypothesis state. Shape is the first signal, colour the second, the label the third
 *  (WCAG 1.4.1): circle = claimed, square = refuted, hollow diamond = abstained (no claim, so no hue). */
type Shape = 'circle' | 'square' | 'diamond' | 'ring' | 'dash' | 'dot' | 'ban'
const META: Record<State, { label: string; shape: Shape; tone: string }> = {
  confirmed: { label: 'Confirmed', shape: 'circle', tone: 'text-ok' },
  validated: { label: 'Validated', shape: 'circle', tone: 'text-ok' },
  refuted: { label: 'Refuted', shape: 'square', tone: 'text-bad' },
  abstained: { label: 'Abstained', shape: 'diamond', tone: 'text-abstain' },
  refused: { label: 'Refused', shape: 'ban', tone: 'text-refused' },
  in_lab: { label: 'In lab', shape: 'ring', tone: 'text-lab' },
  selected: { label: 'Selected', shape: 'dash', tone: 'text-muted' },
  not_selected: { label: 'Not selected', shape: 'dot', tone: 'text-faint' },
  proposed: { label: 'Proposed', shape: 'dot', tone: 'text-muted' },
}

export function StateMark({ state, size = 11, className }: { state: State; size?: number; className?: string }) {
  const m = META[state] ?? META.proposed
  const c = 'currentColor', s = size, h = s / 2
  const body = {
    circle: <circle cx={h} cy={h} r={h - 0.5} fill={c} />,
    square: <rect x=".5" y=".5" width={s - 1} height={s - 1} fill={c} />,
    diamond: <path d={`M${h} 1 L${s - 1} ${h} L${h} ${s - 1} L1 ${h} Z`} fill="none" stroke={c} strokeWidth="1.8" />,
    ring: <><circle cx={h} cy={h} r={h - 1} fill="none" stroke={c} strokeWidth="1.4" strokeDasharray="2.4 2" /><circle cx={h} cy={h} r="1.7" fill={c} /></>,
    dash: <circle cx={h} cy={h} r={h - 1.2} fill="none" stroke={c} strokeWidth="1.4" />,
    dot: <circle cx={h} cy={h} r="1.8" fill={c} />,
    ban: <><circle cx={h} cy={h} r={h - 1} fill="none" stroke={c} strokeWidth="1.4" /><path d={`M2.5 ${s - 2.5} L${s - 2.5} 2.5`} stroke={c} strokeWidth="1.4" /></>,
  }[m.shape]
  return <svg width={s} height={s} viewBox={`0 0 ${s} ${s}`} aria-hidden className={cn('shrink-0', m.tone, className)}>{body}</svg>
}

export function StateChip({ state, className }: { state: State; className?: string }) {
  const m = META[state] ?? META.proposed
  return (
    <span className={cn('label inline-flex items-center gap-1.5 !text-[10.5px] font-medium', m.tone, className)}>
      <StateMark state={state} size={10} /> {m.label}
    </span>
  )
}

export function Tag({ children, tone = 'neutral', className, title }: {
  children: React.ReactNode; tone?: 'neutral' | 'ok' | 'warn' | 'bad' | 'accent'; className?: string; title?: string
}) {
  const tones = {
    neutral: 'border-line-strong text-muted', ok: 'border-ok/40 text-ok', warn: 'border-warn/45 text-warn',
    bad: 'border-bad/40 text-bad', accent: 'border-accent/40 text-accent',
  }
  return <span title={title} className={cn('num inline-flex items-center border px-1.5 py-px text-[11px]', tones[tone], className)}>{children}</span>
}
