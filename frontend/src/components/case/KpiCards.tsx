import { AlertTriangle, ArrowLeftRight, Coins, Network, type LucideIcon } from 'lucide-react'
import type { CaseOverview } from '../../lib/api'
import { fmtMoney } from '../../lib/money'

function Kpi({ label, value, note, Icon, ink }: { label: string; value: string | number; note: string; Icon: LucideIcon; ink: string }) {
  return (
    <div className="card p-4 flex items-center gap-3.5 min-w-0">
      <span className="w-10 h-10 rounded-full grid place-items-center shrink-0" style={{ background: 'var(--surface-2)' }}>
        <Icon size={20} style={{ color: ink }} aria-hidden />
      </span>
      <div className="min-w-0">
        <div className="text-[24px] leading-tight font-semibold tabular truncate">{value}</div>
        <div className="text-sm" style={{ color: 'var(--ink-2)' }}>{label} <span style={{ color: 'var(--muted)' }}>· {note}</span></div>
      </div>
    </div>
  )
}

/** Four plain numbers about the case. The verdict lives in the Why card. */
export default function KpiCards({ o }: { o: CaseOverview }) {
  const k = o.kpis
  return (
    <div className="grid gap-4 grid-cols-2 min-[1180px]:grid-cols-4">
      <Kpi label="Amount" note="flagged money" Icon={Coins} ink="var(--critical)" value={fmtMoney(k.amount)} />
      <Kpi label="Accounts" note={k.traced_accounts > 0 ? `${k.traced_accounts} found by tracing` : 'in the case'}
        Icon={Network} ink="var(--brand)" value={k.connected_accounts} />
      <Kpi label="Payments" note="flagged" Icon={ArrowLeftRight} ink="var(--s7)" value={k.payments} />
      <Kpi label="Warning signs" note={k.suspicious_patterns === null ? 'after investigating' : 'found'}
        Icon={AlertTriangle} ink="#94620a" value={k.suspicious_patterns ?? '–'} />
    </div>
  )
}
