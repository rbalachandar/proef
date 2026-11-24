import type { Schemas } from '../../api/v1'

type Item = Schemas['InboxItem']
export type ProgramGroup = { slug: string; name: string; items: Item[]; oldest: number | null }

/** Pending items by program, only for programs that have any. The program whose oldest item has waited longest comes
 *  first. Items with no program (a standalone campaign) go in one last group so nothing is hidden. */
export function groupByProgram(pending: Item[]): ProgramGroup[] {
  const by = new Map<string, ProgramGroup>()
  for (const i of pending) {
    const slug = i.program?.slug ?? ''
    const g = by.get(slug) ?? { slug, name: i.program?.name ?? 'No program', items: [], oldest: null }
    g.items.push(i)
    if (i.ts != null && (g.oldest == null || i.ts < g.oldest)) g.oldest = i.ts
    by.set(slug, g)
  }
  return [...by.values()].sort((a, b) =>
    (a.slug === '' ? 1 : 0) - (b.slug === '' ? 1 : 0) || (a.oldest ?? Infinity) - (b.oldest ?? Infinity))
}
