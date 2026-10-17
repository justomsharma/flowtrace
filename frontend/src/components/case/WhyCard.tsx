import { AlertTriangle, CircleCheck, Clock, Eye, Fingerprint, Loader2, OctagonAlert, Play, type LucideIcon } from 'lucide-react'
import type { CaseOverview } from '../../lib/api'

const VERDICT: Record<CaseOverview['status']['state'], { label: string; Icon: LucideIcon; ink: string; tint: string; note: string }> = {
  not_started: { label: 'Not checked yet', Icon: Clock, ink: 'var(--ink-2)', tint: 'var(--surface-2)', note: 'Investigate to get a verdict' },
  queued: { label: 'Starting', Icon: Clock, ink: 'var(--brand)', tint: 'var(--brand-tint)', note: 'Starts in a moment' },
  investigating: { label: 'Investigating', Icon: Loader2, ink: 'var(--brand)', tint: 'var(--brand-tint)', note: 'Following the money now' },
  failed: { label: 'Check failed', Icon: AlertTriangle, ink: 'var(--critical)', tint: 'var(--surface-2)', note: 'Try running it again' },
  escalate: { label: 'Escalate', Icon: OctagonAlert, ink: 'var(--critical)', tint: 'var(--flag-tint)', note: 'Send to a senior analyst' },
  review: { label: 'Review', Icon: Eye, ink: '#94620a', tint: 'var(--hold-tint)', note: 'Mixed signs: a person decides' },
  close: { label: 'Close', Icon: CircleCheck, ink: 'var(--good)', tint: 'var(--good-tint)', note: 'Looks like normal activity' },
}

const cap = (s: string) => s.charAt(0).toUpperCase() + s.slice(1)

function Cell({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex flex-col gap-1 min-w-0">
      <span className="text-xs font-medium uppercase tracking-wide" style={{ color: 'var(--muted)' }}>{label}</span>
      {children}
    </div>
  )
}

/** The verdict, how sure it is, and one line on what it rests on. */
export default function WhyCard({ o, onInvestigate }: { o: CaseOverview; onInvestigate: () => void }) {
  const s = o.status
  const v = VERDICT[s.state]
  const strong = o.findings.filter((f) => f.tag === 'Strong sign').length
  const weak = o.findings.length - strong
  const normal = o.explanations.length
  // Levels come from the server, at the thresholds in config.yaml (disposition.escalate_at / close_below).
  const level = s.risk_level
  const color = level === 'High' ? 'var(--critical)' : level === 'Medium' ? 'var(--warning)' : level === 'Low' ? 'var(--good)' : 'var(--axis)'
  const basis = o.summary
    ? `Based on ${strong} strong sign${strong === 1 ? '' : 's'}, ${weak} weaker sign${weak === 1 ? '' : 's'} and ${normal} normal explanation${normal === 1 ? '' : 's'}.`
    : null

  return (
    <section aria-label="Verdict" className="card p-5 grid gap-5 sm:grid-cols-2 lg:grid-cols-[minmax(0,2fr)_minmax(0,0.8fr)_minmax(0,0.8fr)_minmax(0,1.2fr)] items-start">
      <div className="flex items-center gap-3 min-w-0">
        <span className="w-12 h-12 rounded-full grid place-items-center shrink-0" style={{ background: v.tint }}>
          <v.Icon size={24} className={s.state === 'investigating' ? 'animate-spin' : ''} style={{ color: v.ink }} aria-hidden />
        </span>
        <div className="min-w-0">
          <div className="text-xl font-semibold leading-tight" style={{ color: v.ink }}>{v.label}</div>
          <div className="text-sm" style={{ color: 'var(--ink-2)' }}>{o.verdict_basis ?? basis ?? v.note}</div>
          {(s.state === 'not_started' || s.state === 'failed') && (
            <button onClick={onInvestigate} className="mt-2 inline-flex items-center gap-2 px-3.5 py-2 rounded-lg text-sm font-semibold"
              style={{ background: 'var(--brand)', color: 'var(--brand-ink)' }}>
              <Play size={14} aria-hidden /> {s.state === 'failed' ? 'Run again' : 'Investigate'}
            </button>
          )}
        </div>
      </div>

      <Cell label="Risk">
        <span className="text-lg font-semibold">{level ?? '–'}</span>
        <div className="h-2 rounded-full overflow-hidden" style={{ background: 'var(--surface-2)' }} aria-hidden>
          <div className="h-full rounded-full" style={{ width: `${Math.max(4, (s.risk ?? 0) * 100)}%`, background: color }} />
        </div>
      </Cell>

      <Cell label="Confidence">
        <span className="text-lg font-semibold">{s.confidence ?? '–'}</span>
        <span className="text-xs" style={{ color: 'var(--muted)' }}>How far the case is from a decision line (a score, not a probability)</span>
      </Cell>

      <Cell label="Possible crime type (indicative, not a finding)">
        {o.crime_guess.length ? (
          <span className="flex flex-wrap gap-1.5">
            {o.crime_guess.slice(0, 3).map((c) => (
              <span key={c} className="inline-flex items-center gap-1 px-2 py-0.5 rounded-md text-sm" style={{ background: 'var(--surface-2)' }}>
                <Fingerprint size={13} style={{ color: 'var(--ink-2)' }} aria-hidden />{cap(c)}
              </span>
            ))}
          </span>
        ) : (
          <>
            <span className="text-base font-semibold">Not determined</span>
            <span className="text-xs" style={{ color: 'var(--muted)' }}>
              {!o.summary ? 'Shown after the investigation.' : s.state === 'escalate'
                ? 'The pattern does not point to one crime.' : 'Shown for escalated cases only.'}
            </span>
          </>
        )}
      </Cell>
    </section>
  )
}
