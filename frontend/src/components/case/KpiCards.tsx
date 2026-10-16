import type { ReactNode } from 'react'
import { AlertTriangle, CircleCheck, Clock, Coins, Loader2, Network, OctagonAlert, Eye, type LucideIcon } from 'lucide-react'
import type { CaseOverview } from '../../lib/api'
import { fmtMoney } from '../../lib/money'

const plural = (n: number, w: string) => `${n} ${w}${n === 1 ? '' : 's'}`

const STATUS: Record<CaseOverview['status']['state'], { label: string; Icon: LucideIcon; tint: string; ink: string }> = {
  not_started: { label: 'Not started', Icon: Clock, tint: 'var(--surface-2)', ink: 'var(--ink-2)' },
  queued: { label: 'Queued', Icon: Clock, tint: 'var(--brand-tint)', ink: 'var(--brand)' },
  investigating: { label: 'Investigating', Icon: Loader2, tint: 'var(--brand-tint)', ink: 'var(--brand)' },
  failed: { label: 'Failed', Icon: AlertTriangle, tint: 'var(--surface-2)', ink: 'var(--critical)' },
  escalate: { label: 'Escalate', Icon: OctagonAlert, tint: 'var(--flag-tint)', ink: 'var(--critical)' },
  review: { label: 'Review', Icon: Eye, tint: 'var(--hold-tint)', ink: '#94620a' },
  close: { label: 'Close', Icon: CircleCheck, tint: 'var(--good-tint)', ink: 'var(--good)' },
}

function Kpi({ label, value, note, Icon, tint, ink, spin = false }: {
  label: string; value: ReactNode; note: ReactNode; Icon: LucideIcon; tint: string; ink: string; spin?: boolean
}) {
  return (
    <div className="card p-4 flex items-start gap-3.5 min-w-0" style={{ background: `color-mix(in srgb, ${tint} 55%, var(--surface))` }}>
      <span className="w-11 h-11 rounded-full grid place-items-center shrink-0" style={{ background: tint }}>
        <Icon size={22} className={spin ? 'animate-spin' : ''} style={{ color: ink }} aria-hidden />
      </span>
      <div className="min-w-0">
        <div className="text-sm font-medium" style={{ color: ink === 'var(--ink-2)' ? 'var(--ink)' : ink }}>{label}</div>
        <div className="text-[26px] leading-tight font-semibold tabular truncate">{value}</div>
        <div className="text-sm" style={{ color: 'var(--ink-2)' }}>{note}</div>
      </div>
    </div>
  )
}

export default function KpiCards({ o, onInvestigate }: { o: CaseOverview; onInvestigate: () => void }) {
  const k = o.kpis
  const s = o.status
  const st = STATUS[s.state]
  const statusNote: ReactNode =
    s.state === 'not_started' || s.state === 'failed' ? (
      <button onClick={onInvestigate} className="font-medium underline underline-offset-2" style={{ color: 'var(--brand)' }}>
        {s.state === 'failed' ? 'Run the investigation again' : 'Investigate this case'}
      </button>
    )
    : s.state === 'queued' ? 'Starts in a moment'
    : s.state === 'investigating' ? 'The agent is following the money'
    : `Risk ${s.risk?.toFixed(2)}, ${s.confidence?.toLowerCase()} confidence`
  return (
    <div className="grid gap-4 grid-cols-2 min-[1180px]:grid-cols-4">
      <Kpi label="Amount involved" Icon={Coins} tint="var(--flag-tint)" ink="var(--critical)"
        value={fmtMoney(k.amount, o.currency, o.rate)}
        note={`across ${plural(k.accounts, 'account')} · ${plural(k.payments, 'payment')}`} />
      <Kpi label="Connected accounts" Icon={Network} tint="var(--brand-tint)" ink="var(--brand)" value={k.connected_accounts}
        note={k.traced_accounts > 0 ? `${k.accounts} in the case, ${k.traced_accounts} found by following the money` : 'All in the case'} />
      <Kpi label="Suspicious patterns" Icon={AlertTriangle} tint="var(--hold-tint)" ink="#94620a"
        value={k.suspicious_patterns ?? '–'}
        note={k.suspicious_patterns === null ? 'Not investigated yet' : 'Red flags found in the graph'} />
      <Kpi label="Investigation status" Icon={st.Icon} spin={s.state === 'investigating'} tint={st.tint} ink={st.ink}
        value={st.label} note={statusNote} />
    </div>
  )
}
