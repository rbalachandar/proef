import { render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { describe, expect, it, vi } from 'vitest'
import { ToastProvider } from '../app/toast'
import { PlanReview } from '../features/inbox/PlanReview'
import { ResultsTable } from '../features/campaign/ResultsTable'
import { SuggestionNote } from '../features/inbox/ReviewControls'

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

  it('labels a prediction as a prior, shows the structure it used, and says why one did not run', () => {
    const p = {
      n_hypotheses: 2, warnings: [],
      families: [{ id: 'f', claim: 'c', mechanism: 'm', refuted_if: 'r', variants: [
        { ...variant('a', 'aspirin'), predictions: [{ tool: 'admet-physchem', version: '1', status: 'ok', input: 'CC(=O)Oc1ccccc1C(=O)O', values: { mw_da: 180.16, lipinski_pass: true }, units: { mw_da: 'Da' }, uncertainty: 'none', in_domain: true, calibrated: false, evidence: false }] },
        { ...variant('b', 'mystery'), predictions: [{ tool: 'admet-physchem', version: '1', status: 'invalid_input', reason: 'RDKit cannot parse this SMILES', values: {}, units: {}, calibrated: false, evidence: false }] },
      ] }],
    } as never
    wrap(<PlanReview item={{ id: 'x', title: 't', ts: 1, plan: p }} />)
    expect(screen.getByText('Prediction, a prior and not evidence')).toBeInTheDocument()
    expect(screen.getByText('CC(=O)Oc1ccccc1C(=O)O')).toBeInTheDocument()
    expect(screen.getByText(/mw_da 180.16 Da, lipinski_pass yes/)).toBeInTheDocument()
    expect(screen.getByText(/no uncertainty reported/)).toBeInTheDocument()
    expect(screen.getByText('Prediction not run')).toBeInTheDocument()
    expect(screen.getByText(/RDKit cannot parse this SMILES/)).toBeInTheDocument()
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

describe('model suggestion on an abstained result', () => {
  const sug = (over: object) => ({ action: 'follow_up', ok: true, status: 'ok', reasons: [], replicates: 6, replicates_requested: 6, concentrations: ['0.5uM', '5uM', '20uM', '80uM'], why: 'bracket the gate', ran: false, ...over }) as never

  it('is labelled as a suggestion, shows the follow-up it proposes, and says when it ran', () => {
    render(<SuggestionNote s={sug({ ran: true })} />)
    const note = screen.getByRole('note')
    expect(note).toHaveTextContent('Model suggestion, not a decision')
    expect(note).toHaveTextContent('follow up, 6 replicates, doses 0.5 to 80 µM')
    expect(note).toHaveTextContent('A follow-up run was made from it')
    expect(note).toHaveTextContent('“bracket the gate”')
  })

  it('says so when the lab cannot take the replicate request, and when a suggestion failed its checks', () => {
    const { rerender } = render(<SuggestionNote s={sug({ replicates: null, reasons: ['this lab does not take a replicate request'] })} />)
    expect(screen.getByRole('note')).toHaveTextContent('6 replicates requested')
    expect(screen.getByRole('note')).toHaveTextContent('does not take a replicate request')
    rerender(<SuggestionNote s={sug({ ok: false, status: 'does_not_bracket_gate', reasons: ['the ladder does not bracket the gate'] })} />)
    expect(screen.getByRole('note')).toHaveTextContent('did not pass the checks: the ladder does not bracket the gate')
  })

  it('leaves accept and drop as suggestions with no run claim', () => {
    render(<SuggestionNote s={sug({ action: 'accept', concentrations: null, replicates: null, replicates_requested: null })} />)
    expect(screen.getByRole('note')).toHaveTextContent('Suggests accept as inconclusive.')
    expect(screen.getByRole('note')).not.toHaveTextContent('follow-up run was made')
  })
})
