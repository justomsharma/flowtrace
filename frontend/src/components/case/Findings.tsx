import { HelpCircle, Lightbulb } from 'lucide-react'
import type { Finding } from '../../lib/api'
import { evidenceInfo } from '../../lib/evidence'
import { ConfidenceBadge, Panel } from './Panel'

/** The red flags (support evidence), strongest first, and the legitimate explanations (counter evidence). */
export default function Findings({ findings, explanations, investigated, currency }: { findings: Finding[]; explanations: Finding[]; investigated: boolean; currency: string }) {
  return (
    <Panel title="Key findings" icon={Lightbulb} tint="var(--hold-tint)" ink="#94620a"
      right={investigated && findings.length > 0 ? (
        <span className="text-sm font-medium px-2.5 py-1 rounded-full tabular" style={{ background: 'var(--brand-tint)', color: 'var(--brand)' }}>
          {findings.length} red flag{findings.length === 1 ? '' : 's'}
        </span>
      ) : undefined}>
      {!investigated ? (
        <p className="text-sm" style={{ color: 'var(--ink-2)' }}>Findings appear once the agent has investigated the case.</p>
      ) : findings.length === 0 ? (
        <p className="text-sm" style={{ color: 'var(--ink-2)' }}>No red flag was found in the graph.</p>
      ) : (
        <ul className="flex flex-col gap-2.5">
          {findings.map((f) => {
            const { title, Icon } = evidenceInfo(f.code)
            return (
              <li key={f.id} className="rounded-xl p-3 flex gap-3" style={{ background: 'var(--surface-2)' }}>
                <span className="w-9 h-9 rounded-full grid place-items-center shrink-0" style={{ background: 'var(--flag-tint)' }}>
                  <Icon size={17} style={{ color: 'var(--critical)' }} aria-hidden />
                </span>
                <div className="min-w-0 flex-1">
                  <div className="flex items-start justify-between gap-2">
                    <h3 className="text-sm font-semibold">{title}</h3>
                    <ConfidenceBadge level={f.confidence} />
                  </div>
                  <p className="text-sm mt-0.5 leading-snug" style={{ color: 'var(--ink-2)' }}>{f.claim}</p>
                  {f.escalator && <p className="text-xs mt-1" style={{ color: 'var(--critical)' }}>Strong signal: it halves the weight of legitimate explanations.</p>}
                </div>
              </li>
            )
          })}
        </ul>
      )}

      {investigated && (
        <div className="rounded-xl p-3.5 flex gap-3" style={{ background: 'var(--brand-tint)' }}>
          <HelpCircle size={22} className="shrink-0" style={{ color: 'var(--brand)' }} aria-hidden />
          <div className="min-w-0">
            <h3 className="text-sm font-semibold">Possible explanation</h3>
            {explanations.length === 0 ? (
              <p className="text-sm mt-0.5" style={{ color: 'var(--ink-2)' }}>
                The graph shows no legitimate explanation: no known counterparties, regular payments or normal activity for these accounts.
                Business context (invoices, KYC) is not in the data.
              </p>
            ) : (
              <ul className="flex flex-col gap-1.5 mt-1">
                {explanations.map((e) => (
                  <li key={e.id} className="text-sm leading-snug">
                    <span className="font-medium">{evidenceInfo(e.code).title}.</span>{' '}
                    <span style={{ color: 'var(--ink-2)' }}>{e.claim}</span>{' '}
                    <span className="text-xs whitespace-nowrap" style={{ color: 'var(--muted)' }}>({e.confidence.toLowerCase()} weight)</span>
                  </li>
                ))}
              </ul>
            )}
          </div>
        </div>
      )}
      {investigated && currency !== 'US Dollar' && (findings.length > 0 || explanations.length > 0) && (
        <p className="text-xs -mt-1" style={{ color: 'var(--muted)' }}>
          The investigation states amounts in US dollars ($); the rest of this page shows them in {currency}.
        </p>
      )}
    </Panel>
  )
}
