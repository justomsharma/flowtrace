import { useState } from 'react'
import { ChevronRight, HelpCircle, Lightbulb, ShieldCheck } from 'lucide-react'
import type { CaseOverview, Finding } from '../../lib/api'
import { evidenceInfo } from '../../lib/evidence'
import { ConfidenceBadge, Panel } from './Panel'

/** Confidence comes from the measured strength of the sign on the dataset, not from a hand-set weight. */
function level(f: Finding): 'High' | 'Medium' | 'Low' {
  if (f.tag === 'Strong sign' || f.tag === 'Strong sign of normal') return 'High'
  if (f.tag === 'Moderate sign') return 'Medium'
  if (f.tag === 'Common in normal accounts') return 'Low'
  return f.confidence
}

function FindingRow({ f }: { f: Finding }) {
  const [open, setOpen] = useState(false)
  const { title, Icon } = evidenceInfo(f.code)
  const conf = level(f)
  return (
    <li className="rounded-xl" style={{ background: 'var(--surface-2)' }}>
      <button onClick={() => setOpen((v) => !v)} aria-expanded={open}
        className="w-full text-left p-3 flex gap-3 items-start">
        <span className="w-9 h-9 rounded-full grid place-items-center shrink-0" style={{ background: 'var(--flag-tint)' }}>
          <Icon size={17} style={{ color: 'var(--critical)' }} aria-hidden />
        </span>
        <span className="min-w-0 flex-1">
          <span className="flex items-start justify-between gap-2">
            <span className="text-sm font-semibold">{title}</span>
            <ConfidenceBadge level={conf} />
          </span>
          <span className="block text-sm mt-0.5 leading-snug" style={{ color: 'var(--ink-2)' }}>{f.brief}</span>
        </span>
        <ChevronRight size={16} className="shrink-0 mt-2 transition-transform" style={{ color: 'var(--muted)', transform: open ? 'rotate(90deg)' : undefined }} aria-hidden />
      </button>
      {open && (
        <dl className="px-3 pb-3 pl-[60px] flex flex-col gap-1.5 text-sm">
          {f.why && (<><dt className="text-xs font-semibold" style={{ color: 'var(--muted)' }}>Why it matters</dt><dd>{f.why}</dd></>)}
          {f.check && (<><dt className="text-xs font-semibold" style={{ color: 'var(--muted)' }}>Verification step</dt><dd>{f.check}</dd></>)}
        </dl>
      )}
    </li>
  )
}

/** Red flags, strongest measured first, then what could make the case normal. */
export default function KeyFindings({ o }: { o: CaseOverview }) {
  const investigated = !!o.summary
  const rank = { High: 0, Medium: 1, Low: 2 }
  const findings = [...o.findings].sort((a, b) => rank[level(a)] - rank[level(b)])
  const checks = [...new Set(findings.map((f) => f.check).filter(Boolean))].slice(0, 3)
  return (
    <Panel title="Key findings" icon={Lightbulb} tint="var(--hold-tint)" ink="#94620a"
      right={investigated && findings.length > 0 ? (
        <span className="text-sm font-medium px-2.5 py-1 rounded-full tabular" style={{ background: 'var(--brand-tint)', color: 'var(--brand)' }}>
          {findings.length} found
        </span>
      ) : undefined}>
      {!investigated ? (
        o.alert_reasons.length ? (
          <ul className="flex flex-col gap-2">
            {o.alert_reasons.map((r, i) => (
              <li key={i} className="rounded-xl p-3 text-sm" style={{ background: 'var(--surface-2)' }}>{r}</li>
            ))}
            <li className="text-xs" style={{ color: 'var(--muted)' }}>Alert triggers. Run the investigation to test each one.</li>
          </ul>
        ) : <p className="text-sm" style={{ color: 'var(--ink-2)' }}>Investigate to see the findings.</p>
      ) : findings.length === 0 ? (
        <p className="text-sm" style={{ color: 'var(--ink-2)' }}>No indicator identified in the transaction history.</p>
      ) : (
        <ul className="flex flex-col gap-2.5">
          {findings.map((f) => <FindingRow key={f.id} f={f} />)}
        </ul>
      )}

      {investigated && (
        <div className="rounded-xl p-3.5 flex gap-3" style={{ background: 'var(--brand-tint)' }}>
          <HelpCircle size={22} className="shrink-0" style={{ color: 'var(--brand)' }} aria-hidden />
          <div className="min-w-0 flex flex-col gap-2">
            <h3 className="text-sm font-semibold">Legitimate explanations</h3>
            {o.explanations.length > 0 ? (
              <ul className="flex flex-col gap-1.5">
                {o.explanations.slice(0, 3).map((e) => (
                  <li key={e.id} className="flex gap-2 text-sm leading-snug">
                    <ShieldCheck size={15} className="shrink-0 mt-0.5" style={{ color: 'var(--good)' }} aria-hidden />
                    <span>{e.brief}{e.tag === 'Strong sign of normal' && <span style={{ color: 'var(--muted)' }}> · {e.why}</span>}</span>
                  </li>
                ))}
              </ul>
            ) : (
              <p className="text-sm" style={{ color: 'var(--ink-2)' }}>None identified in the transaction history.</p>
            )}
            {checks.length > 0 && (
              <div>
                <div className="text-xs font-semibold mb-1" style={{ color: 'var(--muted)' }}>Verification steps</div>
                <ul className="list-disc pl-5 flex flex-col gap-1 text-sm" style={{ color: 'var(--ink-2)' }}>
                  {checks.map((c, i) => <li key={i}>{c}</li>)}
                </ul>
              </div>
            )}
          </div>
        </div>
      )}
    </Panel>
  )
}
