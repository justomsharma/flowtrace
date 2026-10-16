import { useEffect, useState } from 'react'
import { AlertTriangle, Loader2, ShieldCheck } from 'lucide-react'
import { getJSON, type CaseOverview } from '../lib/api'
import { evidenceInfo } from '../lib/evidence'

/** The case at a glance beside the 3D map: why the verdict, the findings, and what could make it normal. */
export default function CaseGlance({ caseId, version }: { caseId: string; version: string }) {
  const [o, setO] = useState<CaseOverview | null>(null)
  useEffect(() => {
    let alive = true
    getJSON<CaseOverview>(`/api/cases/${caseId}/overview`).then((x) => { if (alive) setO(x) }).catch(() => undefined)
    return () => { alive = false }
  }, [caseId, version])

  if (!o) return <p className="text-xs flex items-center gap-2" style={{ color: 'var(--ink-2)' }}><Loader2 size={12} className="animate-spin" /> Loading…</p>
  const label = { color: 'var(--muted)' }
  return (
    <div className="flex flex-col gap-3 text-sm">
      {o.verdict_basis && <p className="leading-snug" style={{ color: 'var(--ink-2)' }}>{o.verdict_basis}</p>}

      {o.crime_guess.length > 0 && (
        <div className="flex flex-col gap-1">
          <span className="text-xs font-semibold" style={label}>Likely crime type (indicative)</span>
          <span className="flex flex-wrap gap-1.5">
            {o.crime_guess.slice(0, 3).map((c) => (
              <span key={c} className="px-2 py-0.5 rounded-md text-xs" style={{ background: 'var(--surface-2)' }}>{c.charAt(0).toUpperCase() + c.slice(1)}</span>
            ))}
          </span>
        </div>
      )}

      {o.findings.length > 0 && (
        <div className="flex flex-col gap-1.5">
          <span className="text-xs font-semibold" style={label}>Key findings</span>
          <ul className="flex flex-col gap-1.5">
            {o.findings.slice(0, 4).map((f) => (
              <li key={f.id} className="flex gap-2 leading-snug">
                <AlertTriangle size={14} className="shrink-0 mt-0.5" style={{ color: 'var(--s2)' }} aria-hidden />
                <span><b className="font-medium">{evidenceInfo(f.code).title}:</b> <span style={{ color: 'var(--ink-2)' }}>{f.brief}</span></span>
              </li>
            ))}
          </ul>
        </div>
      )}

      {o.explanations.length > 0 && (
        <div className="flex flex-col gap-1.5">
          <span className="text-xs font-semibold" style={label}>Legitimate explanations</span>
          <ul className="flex flex-col gap-1.5">
            {o.explanations.slice(0, 3).map((e) => (
              <li key={e.id} className="flex gap-2 leading-snug" style={{ color: 'var(--ink-2)' }}>
                <ShieldCheck size={14} className="shrink-0 mt-0.5" style={{ color: 'var(--good)' }} aria-hidden />{e.brief}
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  )
}
