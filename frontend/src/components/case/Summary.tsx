import { Bot, Gauge } from 'lucide-react'
import type { CaseOverview } from '../../lib/api'
import { Panel, Pill } from './Panel'

export function AiSummary({ summary, live }: { summary: CaseOverview['summary']; live: boolean }) {
  return (
    <Panel title="AI investigator summary" icon={Bot}
      right={summary ? (summary.by_ai
        ? <Pill bg="var(--good-tint)" fg="var(--good)">AI</Pill>
        : <Pill bg="var(--surface-2)" fg="var(--ink-2)">Rules</Pill>) : undefined}>
      {summary ? (
        <div className="flex flex-col gap-1.5">
          <p className="text-[15px] font-semibold leading-snug">{summary.headline}</p>
          <p className="text-sm leading-relaxed" style={{ color: 'var(--ink-2)' }}>{summary.text}</p>
          {!summary.by_ai && (
            <p className="text-xs" style={{ color: 'var(--muted)' }}>Written by the rule-based fallback because no AI model answered.</p>
          )}
        </div>
      ) : (
        <p className="text-sm" style={{ color: 'var(--ink-2)' }}>
          {live ? 'The agent writes its summary when the investigation ends.' : 'No summary yet: the case has not been investigated.'}
        </p>
      )}
    </Panel>
  )
}

const IND: Record<CaseOverview['indicators'][number]['key'], { label: string; color: string; what: string }> = {
  model: { label: 'Model score', color: 'var(--critical)', what: 'Chance of laundering from the triage model' },
  red_flags: { label: 'Red-flag strength', color: 'var(--serious)', what: 'Combined weight of the red flags' },
  shape: { label: 'Laundering shape', color: 'var(--warning)', what: 'How clearly the flow matches a known shape' },
  legitimacy: { label: 'Legitimate explanations', color: 'var(--good)', what: 'Lowers the risk' },
}

/** The disposition's inputs, as computed in code: risk = 0.45 model + 0.35 red flags + 0.20 shape − 0.40 legitimacy. */
export function RiskIndicators({ indicators, risk }: { indicators: CaseOverview['indicators']; risk: number | null }) {
  if (!indicators.length) return null
  return (
    <Panel title="Top risk indicators" icon={Gauge} tint="var(--flag-tint)" ink="var(--critical)"
      sub={risk !== null ? <>They add up to a risk of <span className="font-semibold tabular" style={{ color: 'var(--ink)' }}>{risk.toFixed(2)}</span> out of 1.</> : undefined}>
      <ul className="flex flex-col gap-3">
        {indicators.map((i) => {
          const d = IND[i.key]
          const pct = Math.round(i.value * 100)
          return (
            <li key={i.key} className="flex flex-col gap-1">
              <div className="flex items-baseline justify-between gap-3 text-sm">
                <span className="font-medium">{d.label}</span>
                <span className="tabular font-semibold">{pct}%</span>
              </div>
              <div className="h-2 rounded-full overflow-hidden" style={{ background: 'var(--surface-2)' }}
                role="meter" aria-valuenow={pct} aria-valuemin={0} aria-valuemax={100} aria-label={d.label}>
                <div className="h-full rounded-full" style={{ width: `${pct}%`, background: d.color }} />
              </div>
              <span className="text-xs tabular" style={{ color: 'var(--muted)' }}>
                {d.what}: {i.contribution >= 0 ? 'adds' : 'takes off'} {Math.abs(i.contribution).toFixed(2)}
              </span>
            </li>
          )
        })}
      </ul>
    </Panel>
  )
}
