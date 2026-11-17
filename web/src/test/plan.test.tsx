import { render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { describe, expect, it, vi } from 'vitest'
import { ToastProvider } from '../app/toast'
import { PlanReview } from '../features/inbox/PlanReview'
import { ResultsTable } from '../features/campaign/ResultsTable'

vi.mock('../api/hooks', () => ({ useDecideApproval: () => ({ mutate: vi.fn(), isPending: false, error: null }) }))

const variant = (id: string, candidate: string) => ({ hypothesis_id: id, candidate, statement: `${candidate} binds`, predicted: '', rationale: '', assay: 'binding', protocol: '' })
const plan = (warnings: object[]) => ({
  n_hypotheses: 3, warnings,
  families: [
    { id: 'atp_site', claim: 'c', mechanism: 'm', refuted_if: 'r', variants: [variant('a', 'erlotinib'), variant('c', 'gefitinib')] },
    { id: 'covalent', claim: 'c', mechanism: 'm', refuted_if: 'r', variants: [variant('b', 'erlotinib')] },
  ],
}) as never

const wrap = (ui: React.ReactElement) => render(<MemoryRouter><ToastProvider onNavigate={() => {}}>{ui}</ToastProvider></MemoryRouter>)

describe('plan review', () => {
  it('warns before approval when one compound sits under two families', () => {
    wrap(<PlanReview item={{ id: 'x', title: 't', ts: 1, plan: plan([{ kind: 'duplicate_compound', candidate: 'erlotinib', families: ['atp_site', 'covalent'], hypothesis_ids: ['a', 'b'] }]) }} />)
    const note = screen.getByRole('note')
    expect(note).toHaveTextContent('Same compound under more than one family')
    expect(note).toHaveTextContent('erlotinib (atp_site, covalent)')
  })

  it('says when a compound was already tested in an earlier round, and what the verdict was', () => {
    wrap(<PlanReview item={{ id: 'x', title: 't', ts: 1, plan: plan([{ kind: 'already_tested', candidate: 'gefitinib', families: ['new'], verdict: 'invalidated', prior_family: 'atp_site' }]) }} />)
    const note = screen.getByRole('note')
    expect(note).toHaveTextContent('Already tested in this campaign')
    expect(note).toHaveTextContent('gefitinib (invalidated, atp_site)')
    expect(note).not.toHaveTextContent('Same compound')
  })

  it('shows where each hypothesis runs, and says plainly when one will not be submitted', () => {
    const lab = (over: object) => ({ pick: null, runs_on: 'plate-reader', status: 'ok', ok: true, reasons: [], flags: [], eligible: ['plate-reader'], ...over })
    const p = {
      n_hypotheses: 3, warnings: [],
      families: [{ id: 'f', claim: 'c', mechanism: 'm', refuted_if: 'r', variants: [
        { ...variant('a', 'erlotinib'), lab: lab({}) },
        { ...variant('b', 'gefitinib'), lab: lab({ pick: 'virtual-lab', status: 'other_instrument', ok: false, reasons: ['virtual-lab could run it, but this campaign runs on plate-reader'] }) },
        { ...variant('c', 'afatinib'), lab: lab({ status: 'cannot_run', ok: false, reasons: ['this campaign runs on plate-reader, which declares no cytotoxicity assay.'] }) },
      ] }],
    } as never
    wrap(<PlanReview item={{ id: 'x', title: 't', ts: 1, plan: p }} />)
    expect(screen.getAllByText('Runs on')).toHaveLength(3)
    expect(screen.getByText(/model chose/)).toBeInTheDocument()
    expect(screen.getByText(/Will not be submitted/)).toBeInTheDocument()
    expect(screen.getByText(/declares no cytotoxicity assay/)).toBeInTheDocument()
  })

  it('shows no warning when every compound appears once', () => {
    wrap(<PlanReview item={{ id: 'x', title: 't', ts: 1, plan: plan([]) }} />)
    expect(screen.queryByRole('note')).toBeNull()
  })
})

describe('results table', () => {
  const row = (id: string, cand: string, family: string) => ({
    id, head: cand, candidate: cand, family, statement: '', stage: '', state: 'validated', selected: false,
    verdict: { status: 'validated', observed: 2, ci: [1, 3], extrapolation: false },
  }) as never
  const gate = { metric: 'ic50_um', direction: 'min', threshold: 20, unit: 'µM', label: 'IC50 ≤ 20' } as never

  it('tells apart two rows for the same compound by their family, and only then', () => {
    render(<ResultsTable hyps={[row('a', 'erlotinib', 'atp_site'), row('b', 'erlotinib', 'covalent'), row('c', 'gefitinib', 'atp_site')]} gate={gate} onOpen={() => {}} />)
    expect(screen.getByText('atp_site', { selector: 'span' })).toBeInTheDocument()
    expect(screen.getByText('covalent')).toBeInTheDocument()
    expect(screen.queryAllByText('atp_site')).toHaveLength(1)        // gefitinib's row is not labelled: it is not repeated
  })
})
