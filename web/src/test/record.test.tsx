import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import { RecordTab } from '../features/campaign/RecordTab'

const ACTORS = [
  { id: 'campaign', kind: 'service', role: 'Selects, tests and judges', events: 5, model_version: null },
  { id: 'gpt-6-luna', kind: 'agent', role: 'Wrote the hypotheses', events: 2, model_version: 'gpt-6-luna' },
  { id: 'local-user', kind: 'human', role: 'Approvals and review decisions', events: 1, model_version: null },
  { id: 'llm-reviewer', kind: 'service', role: 'Advisory LLM reviewer: can only add flags', events: 1, model_version: null },
]
const LOG = [
  { ts: 3, kind: 'review', text: 'local-user approved the research plan', actor: 'local-user', event: 'gate_decision', hash: 'a3f91c0' },
  { ts: 2, kind: 'verdict', text: 'EG-05: VALIDATED', actor: 'campaign', event: 'hypothesis_validated', hash: '51be07d' },
  { ts: 1, kind: 'run', text: 'Run sim-1 submitted', actor: 'campaign', event: 'lab_run', hash: '9c41e0b' },
]

vi.mock('../api/hooks', () => ({
  useAgents: () => ({ data: ACTORS }),
  useActivity: () => ({ data: LOG, isLoading: false }),
  useTrace: () => ({ data: undefined, isLoading: false }),
}))

describe('the record tab', () => {
  it('shows every record with its hash prefix and classifies actors as human, model or code', () => {
    render(<RecordTab id="c1" />)
    expect(screen.getAllByRole('row')).toHaveLength(4)            // header + 3 records
    expect(screen.getByText('#a3f91c0')).toBeInTheDocument()
    const kinds = Object.fromEntries(ACTORS.map((a) => [a.id, within(screen.getByRole('button', { name: new RegExp(`^${a.id}`) })).getByText(/^(human|model|code)$/).textContent]))
    expect(kinds).toEqual({ campaign: 'code', 'gpt-6-luna': 'model', 'local-user': 'human', 'llm-reviewer': 'model' })
  })

  it('filters the log to one actor', async () => {
    render(<RecordTab id="c1" />)
    await userEvent.click(screen.getByRole('button', { name: /^campaign/ }))
    expect(screen.getAllByRole('row')).toHaveLength(3)             // header + the two campaign records
    expect(screen.queryByText('local-user approved the research plan')).not.toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: /^all actors/ }))
    expect(screen.getAllByRole('row')).toHaveLength(4)
  })

  it('filters by ledger event type, so a chip means exactly what the record says', async () => {
    render(<RecordTab id="c1" />)
    await userEvent.click(screen.getByRole('button', { name: 'decisions' }))
    expect(screen.getAllByRole('row')).toHaveLength(2)
    expect(screen.getByText('local-user approved the research plan')).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: 'verdicts' }))
    expect(screen.getByText('EG-05: VALIDATED')).toBeInTheDocument()
    expect(screen.queryByText('Run sim-1 submitted')).not.toBeInTheDocument()
  })

  it('says so when a filter matches nothing', async () => {
    render(<RecordTab id="c1" />)
    await userEvent.click(screen.getByRole('button', { name: /^gpt-6-luna/ }))
    expect(screen.getByText('Nothing matches this filter.')).toBeInTheDocument()
  })
})
