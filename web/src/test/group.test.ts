import { describe, expect, it } from 'vitest'
import { groupByProgram } from '../features/inbox/group'

const item = (id: string, slug: string | null, ts: number) =>
  ({ id, kind: 'approval', title: id, ts, status: 'pending', program: slug ? { slug, name: slug.toUpperCase() } : null }) as never

describe('groupByProgram', () => {
  it('lists only programs with pending items, longest-waiting first, standalone last', () => {
    const g = groupByProgram([item('a', 'egfr', 50), item('b', null, 1), item('c', 'braf', 20), item('d', 'egfr', 10)])
    expect(g.map((x) => x.slug)).toEqual(['egfr', 'braf', ''])
    expect(g[0].items.map((i) => i.id)).toEqual(['a', 'd'])
    expect(g[0].oldest).toBe(10)
    expect(g[2].name).toBe('No program')
  })
  it('is empty when nothing is pending', () => expect(groupByProgram([])).toEqual([]))
})
