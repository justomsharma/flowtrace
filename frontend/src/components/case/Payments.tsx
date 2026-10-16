import { useState } from 'react'
import { ArrowRight } from 'lucide-react'
import { fmtUtc, type Payment } from '../../lib/api'
import { fmtMoney, fmtOriginal, USD_EQ } from '../../lib/money'
import { fullId, useName } from '../../lib/names'
import { StatusPill } from './Panel'

const gap = (ms: number) => {
  const min = Math.round(ms / 60000)
  if (min < 1) return 'moments later'
  if (min < 60) return `${min} min later`
  const h = Math.floor(min / 60)
  return h < 48 ? `${h}h ${min % 60}m later` : `${Math.round(h / 24)} days later`
}

/** The payment in its own currency ("EUR 1,050"), or the currency's name when the amount is unknown. */
const paidIn = (p: Payment) => fmtOriginal(p.amount_orig, p.cur) || p.cur || ''

/** The flow as a sequence: every payment in time order, with the wait before it. */
export function PaymentSequence({ payments, currency, rate }: { payments: Payment[]; currency: string; rate: number }) {
  const name = useName()
  const [all, setAll] = useState(false)
  const ordered = [...payments].sort((a, b) => a.ts - b.ts || a.tx_id.localeCompare(b.tx_id))
  const shown = all ? ordered : ordered.slice(0, 12)
  return (
    <div className="flex flex-col gap-2">
      <ol className="flex flex-col">
        {shown.map((p, i) => (
          <li key={p.tx_id} className="grid grid-cols-[150px_1fr_auto_auto] items-center gap-3 py-2 text-sm tabular" style={{ borderTop: i ? '1px solid var(--border)' : undefined }}>
            <span className="flex flex-col">
              <span>{fmtUtc(p.ts)}</span>
              {i > 0 && <span className="text-xs" style={{ color: 'var(--muted)' }}>{gap(p.ts - shown[i - 1].ts)}</span>}
            </span>
            <span className="flex items-center gap-2 font-medium">{name(p.src)} <ArrowRight size={14} style={{ color: 'var(--muted)' }} aria-label="to" /> {name(p.dst)}</span>
            <span className="flex flex-col text-right">
              <span className="font-semibold">{fmtMoney(p.amount, currency, rate)}</span>
              <span className="text-xs" style={{ color: 'var(--muted)' }}>{paidIn(p)}</span>
            </span>
            <StatusPill status={p.status} />
          </li>
        ))}
      </ol>
      {ordered.length > 12 && (
        <button onClick={() => setAll((v) => !v)} className="self-start text-sm font-medium" style={{ color: 'var(--brand)' }}>
          {all ? 'Show the first 12' : `Show all ${ordered.length} payments`}
        </button>
      )}
    </div>
  )
}

/** `fill`: every payment, scrolling inside the panel when the column has less room than the table needs. */
export function PaymentsTable({ payments, currency, rate, fill = false }: { payments: Payment[]; currency: string; rate: number; fill?: boolean }) {
  const name = useName()
  const [all, setAll] = useState(false)
  const shown = all || fill ? payments : payments.slice(0, 8)
  return (
    <div className={`flex flex-col gap-3 ${fill ? 'flex-1 min-h-0' : ''}`}>
      <div className={fill ? 'flex-1 min-h-0 overflow-auto' : 'overflow-x-auto -mx-1'}>
        <table className="w-full text-sm tabular whitespace-nowrap">
          <thead>
            <tr className="text-left text-xs sticky top-0 z-10" style={{ color: 'var(--muted)', background: 'var(--surface-2)' }}>
              <th className="py-2 px-3 font-medium rounded-l-lg">Time (UTC)</th>
              <th className="px-3 font-medium">From</th>
              <th className="px-3 font-medium">To</th>
              <th className="px-3 font-medium text-right">Amount ({USD_EQ})</th>
              <th className="px-3 font-medium">Paid in</th>
              <th className="px-3 font-medium">Type</th>
              <th className="px-3 font-medium rounded-r-lg">Status</th>
            </tr>
          </thead>
          <tbody>
            {shown.map((p) => (
              <tr key={p.tx_id} style={{ borderBottom: '1px solid var(--border)' }}>
                <td className="py-2.5 px-3">{fmtUtc(p.ts).replace(' UTC', '')}</td>
                <td className="px-3 font-medium" title={fullId(p.src)}>{name(p.src)}</td>
                <td className="px-3 font-medium" title={fullId(p.dst)}>{name(p.dst)}</td>
                <td className="px-3 text-right">{fmtMoney(p.amount, currency, rate)}</td>
                <td className="px-3" style={{ color: 'var(--ink-2)' }}>{paidIn(p)}</td>
                <td className="px-3" style={{ color: 'var(--ink-2)' }}>{p.fmt ?? ''}</td>
                <td className="px-3"><StatusPill status={p.status} /></td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-xs" style={{ color: 'var(--muted)' }}>
        <span>Followed: money the investigation traced onward. Amounts are the {USD_EQ}; "Paid in" is the payment's own currency.</span>
        {!fill && payments.length > 8 && (
          <button onClick={() => setAll((v) => !v)} className="ml-auto text-sm font-medium" style={{ color: 'var(--brand)' }}>
            {all ? 'Show the latest 8' : `Show all ${payments.length} payments`}
          </button>
        )}
      </div>
    </div>
  )
}
