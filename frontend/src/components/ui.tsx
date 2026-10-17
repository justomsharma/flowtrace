import type { ReactNode } from 'react'

export function ChartCard({
  title,
  subtitle,
  children,
  right,
  className = '',
}: {
  title: string
  subtitle?: ReactNode
  children: ReactNode
  right?: ReactNode
  className?: string
}) {
  return (
    <section className={`card p-4 flex flex-col gap-3 min-w-0 ${className}`}>
      <header className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <h2 className="text-sm font-semibold" style={{ color: 'var(--ink)' }}>
            {title}
          </h2>
          {subtitle && (
            <p className="text-xs mt-0.5" style={{ color: 'var(--ink-2)' }}>
              {subtitle}
            </p>
          )}
        </div>
        {right}
      </header>
      {children}
    </section>
  )
}

export function StatTile({
  label,
  value,
  note,
  accent,
}: {
  label: string
  value: string
  note?: ReactNode
  accent?: string
}) {
  return (
    <div className="card p-4 min-w-0">
      <div className="text-xs uppercase tracking-wide" style={{ color: 'var(--muted)' }}>
        {label}
      </div>
      <div className="text-2xl font-semibold mt-1" style={{ color: 'var(--ink)' }}>
        {accent && (
          <span
            className="inline-block w-2 h-2 rounded-full mr-2 align-middle"
            style={{ background: accent }}
            aria-hidden
          />
        )}
        {value}
      </div>
      {note && (
        <div className="text-xs mt-1" style={{ color: 'var(--ink-2)' }}>
          {note}
        </div>
      )}
    </div>
  )
}

interface TipRow {
  label: string
  value: string
  color?: string
}

/** Recharts custom tooltip body: text in ink tokens, colour only on the swatch. */
export function TipBox({ title, rows }: { title: string; rows: TipRow[] }) {
  return (
    <div
      className="rounded-lg px-3 py-2 text-xs shadow-lg"
      style={{ background: 'var(--surface-2)', border: '1px solid var(--border)', color: 'var(--ink)' }}
    >
      <div className="font-semibold mb-1">{title}</div>
      {rows.map((r) => (
        <div key={r.label} className="flex items-center gap-2 tabular">
          {r.color && <span className="w-2 h-2 rounded-sm" style={{ background: r.color }} />}
          <span style={{ color: 'var(--ink-2)' }}>{r.label}</span>
          <span className="ml-auto pl-3 font-medium">{r.value}</span>
        </div>
      ))}
    </div>
  )
}

export function Pill({ ok, label }: { ok: boolean; label: string }) {
  return (
    <span
      className="inline-flex items-center gap-1.5 text-xs px-2 py-1 rounded-full"
      style={{ border: '1px solid var(--border)', color: 'var(--ink-2)' }}
    >
      <span
        className="w-1.5 h-1.5 rounded-full"
        style={{ background: ok ? 'var(--good)' : 'var(--critical)' }}
        aria-hidden
      />
      {label}
      <span className="sr-only">{ok ? 'online' : 'offline'}</span>
    </span>
  )
}

export const axisProps = {
  stroke: 'var(--axis)',
  tick: { fill: 'var(--muted)', fontSize: 11 },
  tickLine: false,
}

/** Small (i) with a plain-language explanation on hover or focus. */
export function InfoTip({ text }: { text: string }) {
  return (
    <span className="relative inline-flex group align-middle ml-1" tabIndex={0} aria-label={text}>
      <span className="w-4 h-4 rounded-full text-[10px] inline-flex items-center justify-center cursor-help"
        style={{ border: '1px solid var(--axis)', color: 'var(--muted)' }}>i</span>
      <span role="tooltip"
        className="pointer-events-none absolute z-20 left-1/2 -translate-x-1/2 bottom-6 w-64 rounded-lg px-3 py-2 text-xs opacity-0 group-hover:opacity-100 group-focus:opacity-100 transition-opacity shadow-lg"
        style={{ background: 'var(--surface-2)', border: '1px solid var(--border)', color: 'var(--ink)' }}>
        {text}
      </span>
    </span>
  )
}

/** Big plain number with a sentence underneath. */
export function BigStat({ value, label, meaning }: { value: string; label: string; meaning: string }) {
  return (
    <div className="flex flex-col gap-1 min-w-0">
      <div className="text-3xl md:text-4xl font-semibold tabular" style={{ color: 'var(--ink)' }}>{value}</div>
      <div className="text-sm font-medium" style={{ color: 'var(--ink)' }}>{label}</div>
      <div className="text-xs" style={{ color: 'var(--ink-2)' }}>{meaning}</div>
    </div>
  )
}
