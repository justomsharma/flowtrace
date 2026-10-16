import { ListChecks } from 'lucide-react'
import type { CaseOverview } from '../../lib/api'
import { Panel, Pill } from './Panel'

/** The investigator's summary: a few short points, never a paragraph. */
export function AiSummary({ summary, live }: { summary: CaseOverview['summary']; live: boolean }) {
  return (
    <Panel title="Summary" icon={ListChecks}
      right={summary ? (summary.by_ai
        ? <Pill bg="var(--good-tint)" fg="var(--good)">AI</Pill>
        : <Pill bg="var(--surface-2)" fg="var(--ink-2)">Rules</Pill>) : undefined}>
      {summary ? (
        <ul className="flex flex-col gap-2">
          {summary.points.map((p, i) => (
            <li key={i} className="flex gap-2 text-sm leading-snug">
              <span className="w-1.5 h-1.5 rounded-full mt-[7px] shrink-0" style={{ background: 'var(--brand)' }} aria-hidden />
              <span>{p}</span>
            </li>
          ))}
        </ul>
      ) : (
        <p className="text-sm" style={{ color: 'var(--ink-2)' }}>
          {live ? 'Written when the investigation ends.' : 'Not investigated yet.'}
        </p>
      )}
    </Panel>
  )
}
