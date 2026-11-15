import { FileSpreadsheet, FlaskConical, Inbox, LayoutDashboard, ShieldCheck, Cpu } from 'lucide-react'

export const NAV = [
  { to: '/', label: 'Programs', icon: LayoutDashboard, end: true },
  { to: '/inbox', label: 'Decisions', icon: Inbox },
  { to: '/campaigns', label: 'Campaigns', icon: FlaskConical },
  { to: '/analyze', label: 'Analyze', icon: FileSpreadsheet },
  { to: '/evidence', label: 'Evidence', icon: ShieldCheck },
  { to: '/lab', label: 'Lab', icon: Cpu },
] as const
