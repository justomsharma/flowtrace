import type { ReactNode } from 'react'
import type { LucideIcon } from 'lucide-react'

/** A titled section of the case page. The icon tile carries the section's colour; the body stays neutral. */
export function Panel({ title, sub, icon: Icon, tint = 'var(--brand-tint)', ink = 'var(--brand)', right, children, className = '', id, labelId }: {
  title: string
  sub?: ReactNode
  icon: LucideIcon
  tint?: string
  ink?: string
  right?: ReactNode
  children: ReactNode
  className?: string
  id?: string
  labelId?: string
}) {
  const hid = labelId ?? `${title.toLowerCase().replace(/[^a-z]+/g, '-')}-title`
  return (
    <section id={id} aria-labelledby={hid} className={`card p-5 flex flex-col gap-4 min-w-0 ${className}`}>
      <header className="flex flex-wrap items-start gap-3">
        <span className="w-9 h-9 rounded-lg grid place-items-center shrink-0" style={{ background: tint }}>
          <Icon size={18} style={{ color: ink }} aria-hidden />
        </span>
        <div className="min-w-[180px] flex-1">
          <h2 id={hid} className="text-[17px] font-semibold leading-tight">{title}</h2>
          {sub && <p className="text-sm mt-0.5" style={{ color: 'var(--ink-2)' }}>{sub}</p>}
        </div>
        {right}
      </header>
      {children}
    </section>
  )
}

const CONF: Record<string, { bg: string; fg: string }> = {
  High: { bg: 'var(--flag-tint)', fg: 'var(--critical)' },
  Medium: { bg: 'var(--hold-tint)', fg: '#94620a' },
  Low: { bg: 'var(--surface-2)', fg: 'var(--ink-2)' },
}

export function ConfidenceBadge({ level, suffix = 'confidence' }: { level: 'High' | 'Medium' | 'Low'; suffix?: string }) {
  const c = CONF[level]
  return (
    <span className="inline-flex items-center px-2 py-0.5 rounded-md text-xs font-medium whitespace-nowrap" style={{ background: c.bg, color: c.fg }}>
      {level} {suffix}
    </span>
  )
}

export function Pill({ children, bg, fg }: { children: ReactNode; bg: string; fg: string }) {
  return <span className="inline-flex items-center px-2 py-0.5 rounded-md text-xs font-medium whitespace-nowrap" style={{ background: bg, color: fg }}>{children}</span>
}

const STATUS_PILL = {
  flagged: { label: 'Suspicious', bg: 'var(--flag-tint)', fg: 'var(--critical)' },
  trail: { label: 'Followed', bg: 'var(--brand-tint)', fg: 'var(--brand)' },
  normal: { label: 'Normal', bg: 'var(--good-tint)', fg: 'var(--good)' },
}

export function StatusPill({ status }: { status: keyof typeof STATUS_PILL }) {
  const s = STATUS_PILL[status]
  return <Pill bg={s.bg} fg={s.fg}>{s.label}</Pill>
}
