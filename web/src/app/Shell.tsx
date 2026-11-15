import { NavLink, Outlet, useLocation } from 'react-router-dom'
import { Moon, ShieldAlert, Sun } from 'lucide-react'
import { useSystemStatus } from '../api/hooks'
import { Tip } from '../ui/Tooltip'
import { cn } from '../ui/cn'
import { CommandPalette } from './CommandPalette'
import { HaltControl } from './HaltDialog'
import { NAV } from './nav'
import { useTheme } from './theme'

/** Global, true on every page: the ledger's state, the stop switch, search. Anything about one campaign or program
 *  lives in that page's own context strip, never here. */
function TopBar() {
  const { data } = useSystemStatus()
  const { theme, toggle } = useTheme()
  const chain = data?.chain, anchor = data?.anchor
  return (
    <header className="flex h-[42px] shrink-0 items-stretch border-b border-line bg-surface">
      <div className="flex w-14 shrink-0 items-center gap-2 border-r border-line px-3.5 lg:w-[168px]">
        <img src="/proef-mark-light.png" alt="" width={24} height={24} className="dark:hidden" />
        <img src="/proef-mark.png" alt="" width={24} height={24} className="hidden dark:block" />
        <span className="hidden text-[19px] font-bold tracking-[-.03em] lg:inline">proef<span aria-hidden className="text-[#ff8a1f]">.</span></span>
      </div>
      <div className="flex items-center pl-[18px]"><CommandPalette dark={theme === 'dark'} onToggleTheme={toggle} /></div>
      <div className="ml-auto flex items-stretch font-mono text-[11.5px] text-muted">
        {chain && (
          <Tip label={chain.verified
            ? `Every one of ${chain.records.toLocaleString()} records was re-hashed and linked to the one before just now. ${anchor ? 'An anchor exists.' : 'No anchor yet: see Evidence.'}`
            : `${chain.problems.length} record(s) fail verification: the ledger was altered.`}>
            <span className={cn('flex items-center gap-2 border-l border-line px-[18px]', !chain.verified && 'text-bad')}>
              <span aria-hidden className={cn('h-[7px] w-[7px] rounded-full', chain.verified ? 'bg-accent shadow-[0_0_0_3px_var(--accent-soft)]' : 'bg-bad')} />
              <b className={cn('font-medium', chain.verified && 'text-ink')}>{chain.verified ? 'ledger verified' : 'ledger integrity failed'}</b>
            </span>
          </Tip>
        )}
        {anchor && anchor.published === false && (
          <Tip label="An anchor was created but never published. It protects nothing until the root is published outside this system’s control. See Evidence.">
            <span className="flex items-center border-l border-line px-[18px] text-warn">anchor unpublished</span>
          </Tip>
        )}
        <HaltControl />
        <button onClick={toggle} aria-label="Toggle theme" className="flex items-center border-l border-line px-4 hover:bg-sunken hover:text-ink">
          {theme === 'dark' ? <Sun size={14} /> : <Moon size={14} />}
        </button>
      </div>
    </header>
  )
}

function Rail() {
  const { data } = useSystemStatus()
  const { pathname } = useLocation()
  return (
    <aside className="w-14 shrink-0 border-r border-line-strong bg-[var(--rail)] py-3.5 lg:w-[168px]">
      <nav aria-label="Sections" className="flex flex-col">
        {NAV.map(({ to, label, icon: Icon, ...rest }) => (
          <NavLink key={to} to={to} end={'end' in rest}
            className={({ isActive: active }) => cn(
              'flex items-center justify-between border-l-2 border-transparent px-4 py-[7px] font-mono text-[12.5px] lowercase text-muted hover:text-ink',
              (active || (to === '/' && pathname.startsWith('/programs'))) && 'border-ink bg-ink font-semibold text-canvas hover:text-canvas dark:bg-sunken dark:text-ink dark:hover:text-ink')}>
            <Icon size={15} aria-hidden className="lg:hidden" />
            <span className="hidden lg:inline">{label}</span>
            {label === 'Decisions' && !!data?.pending_approvals && (
              <span className="num hidden bg-ink px-1.5 text-[11px] font-bold text-canvas lg:inline [.bg-ink_&]:bg-canvas [.bg-ink_&]:text-ink">{data.pending_approvals}</span>
            )}
          </NavLink>
        ))}
      </nav>
    </aside>
  )
}

export default function Shell() {
  const { data } = useSystemStatus()
  return (
    <div className="flex h-full flex-col bg-canvas text-ink">
      <TopBar />
      {data?.halted && (
        <div role="alert" className="flex items-center gap-2 bg-bad-soft px-4 py-2 text-sm font-medium text-bad">
          <ShieldAlert size={16} /> All agents are halted{data.halt_reason ? `: ${data.halt_reason}` : ''}. Nothing new will be submitted to the lab until a human resumes.
        </div>
      )}
      <div className="flex min-h-0 flex-1">
        <Rail />
        <main className="min-h-0 min-w-0 flex-1 overflow-auto"><Outlet /></main>
      </div>
    </div>
  )
}
