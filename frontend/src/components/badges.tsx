import { AlertOctagon, CheckCircle2, Eye } from 'lucide-react'

/** Disposition badge: status colour always paired with an icon and a label. */
export function ActionBadge({ action, large = false }: { action: string; large?: boolean }) {
  const map: Record<string, { color: string; label: string; Icon: typeof Eye }> = {
    ESCALATE: { color: 'var(--critical)', label: 'Escalate', Icon: AlertOctagon },
    REVIEW: { color: 'var(--warning)', label: 'Review', Icon: Eye },
    CLOSE: { color: 'var(--good)', label: 'Close', Icon: CheckCircle2 },
  }
  const m = map[action] ?? map.REVIEW
  return (
    <span
      className={`inline-flex items-center justify-center gap-1.5 rounded-full font-medium whitespace-nowrap ${large ? 'px-3 py-1 text-sm min-w-[112px]' : 'px-2 py-0.5 text-xs min-w-[88px]'}`}
      style={{ border: `1px solid ${m.color}`, color: 'var(--ink)' }}
    >
      <m.Icon size={large ? 16 : 12} style={{ color: m.color }} aria-hidden />
      {m.label}
    </span>
  )
}

export function KindTag({ kind }: { kind: string }) {
  const m: Record<string, [string, string]> = {
    support: ['var(--critical)', 'Red flag'],
    counter: ['var(--good)', 'Legitimate'],
    context: ['var(--muted)', 'Context'],
  }
  const [c, l] = m[kind] ?? m.context
  return (
    <span className="inline-flex items-center gap-1 text-[10px] uppercase tracking-wide" style={{ color: 'var(--ink-2)' }}>
      <span className="w-1.5 h-1.5 rounded-full" style={{ background: c }} aria-hidden />
      {l}
    </span>
  )
}
