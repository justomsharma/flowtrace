import { Bar, BarChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'
import type { CaseOverview } from '../../lib/api'
import { fmtMoney } from '../../lib/money'
import { useName } from '../../lib/names'
import { TipBox, axisProps } from '../ui'

const FLAG = 'var(--critical)'
const OTHER = '#a9b5c6'

const binLabel = (t: number, bucket: 'hour' | 'day', multiDay = false) =>
  multiDay && bucket === 'hour'
    ? new Date(t).toLocaleString('en-GB', { day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit', timeZone: 'UTC' })
    : new Date(t).toLocaleString('en-GB', bucket === 'hour'
    ? { hour: '2-digit', minute: '2-digit', timeZone: 'UTC' } : { day: 'numeric', month: 'short', timeZone: 'UTC' })

export function TimelineChart({ o }: { o: CaseOverview }) {
  const m = (v: number) => fmtMoney(v, o.currency, 1)  // values below are already in the data's currency
  const { bucket, bins } = o.timeline
  const multiDay = bins.length > 0 && new Date(bins[0].t).getUTCDate() !== new Date(bins[bins.length - 1].t).getUTCDate()
  const data = bins.map((b) => ({ ...b, flagged: b.flagged * o.rate, other: b.other * o.rate, label: binLabel(b.t, bucket, multiDay) }))
  if (!data.length) return <p className="text-sm" style={{ color: 'var(--ink-2)' }}>No payments to show.</p>
  // One huge payment can flatten every other bar to nothing. When amounts span more than 50x, use a log
  // scale with the two series side by side (log cannot stack from zero), and say so under the chart.
  const vals = data.flatMap((d) => [d.flagged, d.other]).filter((v) => v > 0)
  const log = vals.length > 1 && Math.max(...vals) / Math.min(...vals) > 50
  const plot = log ? data.map((d) => ({ ...d, flagged: d.flagged || null, other: d.other || null })) : data
  const day = new Date(bins[0].t).toLocaleDateString('en-GB', { day: 'numeric', month: 'short', year: 'numeric', timeZone: 'UTC' })
  return (
    <div className="flex flex-col gap-2 flex-1">
      <ul className="flex gap-4 text-xs self-end" style={{ color: 'var(--ink-2)' }} aria-label="Legend">
        <li className="flex items-center gap-1.5"><span className="w-2.5 h-2.5 rounded-sm" style={{ background: FLAG }} aria-hidden />Suspicious</li>
        <li className="flex items-center gap-1.5"><span className="w-2.5 h-2.5 rounded-sm" style={{ background: OTHER }} aria-hidden />Not suspicious</li>
      </ul>
      <div className="relative flex-1 min-h-[200px]" role="img" aria-label={`Payment amounts per ${bucket}, flagged apart from the rest`}>
        <div className="absolute inset-0">
        <ResponsiveContainer width="100%" height="100%">
          <BarChart data={plot} margin={{ top: 4, right: 4, bottom: 0, left: 0 }} barCategoryGap="18%" maxBarSize={36}>
            <CartesianGrid vertical={false} stroke="var(--grid)" />
            <XAxis dataKey="label" {...axisProps} interval="preserveStartEnd" minTickGap={18} />
            <YAxis {...axisProps} axisLine={false} width={64} scale={log ? 'log' : 'auto'}
              domain={log ? [(min: number) => Math.max(1, min / 2), 'auto'] : [0, 'auto']} allowDataOverflow={log}
              tickFormatter={(v: number) => v >= 1e6 ? `$${+(v / 1e6).toFixed(1)}M` : v >= 1e3 ? `$${+(v / 1e3).toFixed(0)}K` : `$${v}`} />
            <Tooltip cursor={{ fill: 'var(--surface-2)' }} content={({ active, payload }) => {
              if (!active || !payload?.length) return null
              const b = payload[0].payload as (typeof data)[number]
              if (!b.n) return null  // an empty hour has nothing to explain
              return <TipBox title={`${b.label} UTC${bucket === 'hour' && !multiDay ? `, ${day}` : ''}`} rows={[
                ...(b.flagged > 0 ? [{ label: 'Suspicious', value: m(b.flagged), color: FLAG }] : []),
                ...(b.other > 0 ? [{ label: 'Not suspicious', value: m(b.other), color: OTHER }] : []),
                { label: 'Payments', value: String(b.n) },
              ]} />
            }} />
            <Bar dataKey="flagged" stackId={log ? undefined : 'a'} fill={FLAG} radius={log ? [3, 3, 0, 0] : 0} stroke="var(--surface)" strokeWidth={1} isAnimationActive={false} />
            <Bar dataKey="other" stackId={log ? undefined : 'a'} fill={OTHER} radius={[log ? 3 : 4, log ? 3 : 4, 0, 0]} stroke="var(--surface)" strokeWidth={1} isAnimationActive={false} />
          </BarChart>
        </ResponsiveContainer>
        </div>
      </div>
      <p className="text-xs" style={{ color: 'var(--muted)' }}>Per {bucket}, UTC{bucket === 'hour' && !multiDay ? `, ${day}` : ''}.{log && ' Log scale, so small payments stay visible next to large ones.'}</p>
    </div>
  )
}

/** Who received the money: a ranked list with bars. Reads correctly even when one account takes nearly all of it. */
export function DistributionBars({ o }: { o: CaseOverview }) {
  const nm = useName()
  const rows = o.distribution.map((d) => ({ ...d, name: d.account ? nm(d.account) : `${d.accounts} other${d.accounts === 1 ? '' : 's'}` }))
  const total = rows.reduce((s, r) => s + r.amount, 0)
  if (!rows.length) return <p className="text-sm" style={{ color: 'var(--ink-2)' }}>No payments to show.</p>
  const max = Math.max(...rows.map((r) => r.amount))
  return (
    <div className="flex flex-col gap-3">
      <div className="text-sm" style={{ color: 'var(--ink-2)' }}>
        <span className="text-lg font-semibold tabular" style={{ color: 'var(--ink)' }}>{fmtMoney(total)}</span> received in total
      </div>
      <ul className="flex flex-col gap-2.5">
        {rows.map((r) => (
          <li key={r.account ?? 'other'} className="flex flex-col gap-1" title={`${r.name}: ${fmtMoney(r.amount)} (${(r.share * 100).toFixed(1)}%)`}>
            <div className="flex items-baseline gap-2 text-sm tabular">
              <span className="font-medium whitespace-nowrap">{r.name}</span>
              <span className="ml-auto font-semibold">{fmtMoney(r.amount)}</span>
              <span className="w-10 text-right text-xs shrink-0" style={{ color: 'var(--muted)' }}>{(r.share * 100).toFixed(r.share < 0.1 ? 1 : 0)}%</span>
            </div>
            <div className="h-2 rounded-full overflow-hidden" style={{ background: 'var(--surface-2)' }} aria-hidden>
              <div className="h-full rounded-full" style={{ width: `${Math.max(1.5, (100 * r.amount) / max)}%`, background: r.account ? 'var(--s1)' : OTHER }} />
            </div>
          </li>
        ))}
      </ul>
    </div>
  )
}
