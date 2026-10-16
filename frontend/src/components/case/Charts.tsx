import { Bar, BarChart, CartesianGrid, Cell, Pie, PieChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'
import type { CaseOverview } from '../../lib/api'
import { fmtMoney } from '../../lib/money'
import { shortAcct } from '../../lib/plain'
import { TipBox, axisProps } from '../ui'

const FLAG = 'var(--critical)'
const OTHER = '#a9b5c6'
// Categorical slots in fixed order (validated reference palette); "Other" is neutral.
const SLOTS = ['var(--s1)', 'var(--s2)', 'var(--s3)', 'var(--s4)', 'var(--s5)', 'var(--s7)']

const binLabel = (t: number, bucket: 'hour' | 'day') =>
  new Date(t).toLocaleString('en-GB', bucket === 'hour'
    ? { hour: '2-digit', minute: '2-digit', timeZone: 'UTC' } : { day: 'numeric', month: 'short', timeZone: 'UTC' })

export function TimelineChart({ o }: { o: CaseOverview }) {
  const m = (v: number) => fmtMoney(v, o.currency, 1)  // values below are already in the data's currency
  const { bucket, bins } = o.timeline
  const data = bins.map((b) => ({ ...b, flagged: b.flagged * o.rate, other: b.other * o.rate, label: binLabel(b.t, bucket) }))
  if (!data.length) return <p className="text-sm" style={{ color: 'var(--ink-2)' }}>No payments to show.</p>
  const day = new Date(bins[0].t).toLocaleDateString('en-GB', { day: 'numeric', month: 'short', year: 'numeric', timeZone: 'UTC' })
  return (
    <div className="flex flex-col gap-2">
      <ul className="flex gap-4 text-xs self-end" style={{ color: 'var(--ink-2)' }} aria-label="Legend">
        <li className="flex items-center gap-1.5"><span className="w-2.5 h-2.5 rounded-sm" style={{ background: FLAG }} aria-hidden />Flagged</li>
        <li className="flex items-center gap-1.5"><span className="w-2.5 h-2.5 rounded-sm" style={{ background: OTHER }} aria-hidden />Followed or normal</li>
      </ul>
      <div className="h-[200px]" role="img" aria-label={`Payment amounts per ${bucket}, flagged apart from the rest`}>
        <ResponsiveContainer width="100%" height="100%">
          <BarChart data={data} margin={{ top: 4, right: 4, bottom: 0, left: 0 }} barCategoryGap="18%" maxBarSize={36}>
            <CartesianGrid vertical={false} stroke="var(--grid)" />
            <XAxis dataKey="label" {...axisProps} interval="preserveStartEnd" minTickGap={18} />
            <YAxis {...axisProps} axisLine={false} width={64}
              tickFormatter={(v: number) => m(v)} />
            <Tooltip cursor={{ fill: 'var(--surface-2)' }} content={({ active, payload }) => {
              if (!active || !payload?.length) return null
              const b = payload[0].payload as (typeof data)[number]
              return <TipBox title={`${b.label} UTC${bucket === 'hour' ? `, ${day}` : ''}`} rows={[
                { label: 'Flagged', value: m(b.flagged), color: FLAG },
                { label: 'Followed or normal', value: m(b.other), color: OTHER },
                { label: 'Payments', value: String(b.n) },
              ]} />
            }} />
            <Bar dataKey="flagged" stackId="a" fill={FLAG} stroke="var(--surface)" strokeWidth={1} isAnimationActive={false} />
            <Bar dataKey="other" stackId="a" fill={OTHER} radius={[4, 4, 0, 0]} stroke="var(--surface)" strokeWidth={1} isAnimationActive={false} />
          </BarChart>
        </ResponsiveContainer>
      </div>
      <p className="text-xs" style={{ color: 'var(--muted)' }}>Per {bucket} in UTC{bucket === 'hour' ? `, from ${day}` : ''}. Amounts in {o.currency}.</p>
    </div>
  )
}

export function DistributionDonut({ o }: { o: CaseOverview }) {
  const m = (v: number) => fmtMoney(v, o.currency, o.rate)
  const rows = o.distribution.map((d, i) => ({ ...d, name: d.account ? shortAcct(d.account) : `${d.accounts} others`, color: d.account ? SLOTS[i] : OTHER }))
  const total = rows.reduce((s, r) => s + r.amount, 0)
  if (!rows.length) return <p className="text-sm" style={{ color: 'var(--ink-2)' }}>No payments to show.</p>
  return (
    <div className="flex items-center gap-5 flex-wrap">
      <div className="relative w-[156px] h-[156px] shrink-0" role="img" aria-label={`Money received per account, ${m(total)} in total`}>
        <ResponsiveContainer width="100%" height="100%">
          <PieChart>
            <Pie data={rows} dataKey="amount" nameKey="name" innerRadius={52} outerRadius={76} paddingAngle={rows.length > 1 ? 2 : 0}
              stroke="var(--surface)" strokeWidth={2} isAnimationActive={false}>
              {rows.map((r) => <Cell key={r.account ?? 'other'} fill={r.color} />)}
            </Pie>
            <Tooltip content={({ active, payload }) => {
              if (!active || !payload?.length) return null
              const r = payload[0].payload as (typeof rows)[number]
              return <TipBox title={r.account ?? r.name} rows={[{ label: 'Received', value: m(r.amount), color: r.color }, { label: 'Share', value: `${(r.share * 100).toFixed(1)}%` }]} />
            }} />
          </PieChart>
        </ResponsiveContainer>
        <div className="absolute inset-0 grid place-items-center pointer-events-none text-center">
          <div>
            <div className="text-base font-semibold tabular">{fmtMoney(total, o.currency, o.rate)}</div>
            <div className="text-xs" style={{ color: 'var(--muted)' }}>received</div>
          </div>
        </div>
      </div>
      <ul className="flex flex-col gap-1.5 text-sm min-w-[150px] flex-1">
        {rows.map((r) => (
          <li key={r.account ?? 'other'} className="flex items-baseline gap-2 tabular">
            <span className="w-2.5 h-2.5 rounded-full shrink-0 self-center" style={{ background: r.color }} aria-hidden />
            <span className="font-medium">{r.name}</span>
            <span className="ml-auto" style={{ color: 'var(--ink-2)' }}>{m(r.amount)}</span>
            <span className="w-12 text-right text-xs" style={{ color: 'var(--muted)' }}>{(r.share * 100).toFixed(1)}%</span>
          </li>
        ))}
      </ul>
    </div>
  )
}
