import { fireEvent, render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { describe, expect, it, vi } from 'vitest'
import { ToastProvider } from '../app/toast'
import { LaunchCampaignDialog } from '../features/launch/LaunchCampaignDialog'

const launch = vi.fn()
vi.mock('../api/hooks', () => ({
  useLaunchCampaign: () => ({ mutate: launch, isPending: false, error: null }),
  useSystemStatus: () => ({ data: { model: { configured: true, model: 'm' } } }),
  useProgram: () => ({ data: { limits: { max_rounds: 3 } } }),
}))

const open = (onSplit?: (o: string) => void) => render(
  <MemoryRouter><ToastProvider onNavigate={() => {}}>
    <LaunchCampaignDialog open onOpenChange={() => {}} slug="p" onSplit={onSplit} />
  </ToastProvider></MemoryRouter>)

describe('launch dialog: split first', () => {
  it('hands the objective to the split flow instead of launching one campaign', () => {
    const onSplit = vi.fn()
    open(onSplit)
    fireEvent.click(screen.getByRole('radio', { name: /split into sub-problems first/i }))
    fireEvent.change(screen.getByRole('textbox'), { target: { value: ' a potent, soluble inhibitor ' } })
    fireEvent.click(screen.getByRole('button', { name: /review the split/i }))
    expect(onSplit).toHaveBeenCalledWith('a potent, soluble inhibitor')
    expect(launch).not.toHaveBeenCalled()
  })

  it('does not offer the split where the caller cannot run one', () => {
    open()
    expect(screen.queryByRole('radio', { name: /split into sub-problems first/i })).toBeNull()
  })
})
