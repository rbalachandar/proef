import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import { ContextStrip, Ticks } from '../ui/ContextStrip'
import { PageHeader } from '../ui/PageHeader'
import { StateChip, StateMark } from '../ui/Chip'
import { Tabs } from '../ui/Tabs'
import { NodeMark } from '../features/program/TreesCard'

/** The shape of a mark is the first signal of a state: colour alone must never carry a verdict. */
const shape = (container: HTMLElement) => {
  const svg = container.querySelector('svg')!
  return [...svg.children].map((c) => c.tagName).join(',')
}

describe('state marks', () => {
  it('give each verdict its own shape, not only its own colour', () => {
    const shapes = {
      validated: shape(render(<StateMark state="validated" />).container),
      refuted: shape(render(<StateMark state="refuted" />).container),
      abstained: shape(render(<StateMark state="abstained" />).container),
    }
    expect(shapes.validated).toBe('circle')
    expect(shapes.refuted).toBe('rect')
    expect(shapes.abstained).toBe('path')
    expect(new Set(Object.values(shapes)).size).toBe(3)
  })

  it('always carry the state as a word, too', () => {
    render(<><StateChip state="abstained" /><StateChip state="refuted" /></>)
    expect(screen.getByText('Abstained')).toBeInTheDocument()
    expect(screen.getByText('Refuted')).toBeInTheDocument()
  })

  it('label every tree status for assistive technology', () => {
    for (const [status, label] of [['achieved', 'achieved'], ['partial', 'partly met'], ['failed', 'not met'], ['inconclusive', 'undecided'], ['in lab', 'in the lab'], ['open', 'not started']]) {
      const { unmount } = render(<NodeMark status={status} />)
      expect(screen.getByRole('img', { name: label })).toBeInTheDocument()
      unmount()
    }
  })
})

describe('context strip', () => {
  it('shows each fact with its label and sub-line', () => {
    render(<ContextStrip cells={[{ k: 'Gate', v: 'IC50 ≤ 20', s: 'fixed before data' }, { k: 'Runs', v: '6 of 12' }]} />)
    expect(screen.getByText('Gate')).toBeInTheDocument()
    expect(screen.getByText('IC50 ≤ 20')).toBeInTheDocument()
    expect(screen.getByText('fixed before data')).toBeInTheDocument()
    expect(screen.getByText('6 of 12')).toBeInTheDocument()
  })

  it('draws used-of-total ticks, never more filled than used', () => {
    const { container } = render(<Ticks used={6} total={12} />)
    const ticks = [...container.querySelectorAll('i')]
    expect(ticks).toHaveLength(12)
    expect(ticks.filter((t) => t.className.includes('bg-ink'))).toHaveLength(6)
  })
})

describe('tabs', () => {
  it('mark the active tab and report a change', async () => {
    const onChange = vi.fn()
    render(<Tabs tabs={[{ id: 'a', label: 'overview' }, { id: 'b', label: 'rounds' }]} value="a" onChange={onChange} />)
    expect(screen.getByRole('tab', { name: 'overview' })).toHaveAttribute('aria-selected', 'true')
    expect(screen.getByRole('tab', { name: 'rounds' })).toHaveAttribute('aria-selected', 'false')
    await userEvent.click(screen.getByRole('tab', { name: 'rounds' }))
    expect(onChange).toHaveBeenCalledWith('b')
  })
})

describe('page header', () => {
  it('renders a title, a sentence and a strip, and omits the section label when there is none', () => {
    render(<PageHeader title="Programs" sub="Each program has a gate." cells={[{ k: 'Programs', v: '2' }]} />)
    expect(screen.getByRole('heading', { name: 'Programs' })).toBeInTheDocument()
    expect(screen.getByText('Each program has a gate.')).toBeInTheDocument()
    expect(document.querySelector('header > .label')).toBeNull()
  })
})
