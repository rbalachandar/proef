import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import { ToastProvider } from '../app/toast'
import { PauseControl, PausedBanner } from '../features/pause/PauseControl'

const wrap = (ui: React.ReactElement) => render(<ToastProvider onNavigate={() => {}}>{ui}</ToastProvider>)

describe('pause control', () => {
  it('asks for an optional reason, then pauses with it', async () => {
    const onSet = vi.fn().mockResolvedValue(undefined)
    wrap(<PauseControl scope="program" paused={false} onSet={onSet} />)
    await userEvent.click(screen.getByRole('button', { name: /^pause$/i }))
    expect(screen.getByText(/running plates finish/i)).toBeInTheDocument()
    await userEvent.type(screen.getByPlaceholderText(/reason/i), 'plan review')
    await userEvent.click(screen.getByRole('button', { name: 'Pause program' }))
    expect(onSet).toHaveBeenCalledWith(true, 'plan review')
  })

  it('resumes in one click when paused', async () => {
    const onSet = vi.fn().mockResolvedValue(undefined)
    wrap(<PauseControl scope="campaign" paused onSet={onSet} />)
    await userEvent.click(screen.getByRole('button', { name: /resume/i }))
    expect(onSet).toHaveBeenCalledWith(false, '')
  })
})

describe('paused banner', () => {
  it('says who paused it, why, and what that means', () => {
    render(<PausedBanner pause={{ scope: 'campaign:p1', kind: 'campaign', by: 'ana', reason: 'check the reagent' }} />)
    expect(screen.getByRole('status')).toHaveTextContent('ana paused it: check the reagent')
    expect(screen.getByRole('status')).toHaveTextContent('Running plates finish')
  })

  it('tells a campaign held by its program apart from one paused on its own', () => {
    render(<PausedBanner via="program" pause={{ scope: 'program:egfr', kind: 'program', by: 'bo', reason: '' }} />)
    expect(screen.getByRole('status')).toHaveTextContent('Held by its program.')
  })
})
