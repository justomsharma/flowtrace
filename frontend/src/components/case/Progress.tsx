import { Check, ListChecks, Loader2, X } from 'lucide-react'
import type { CaseResult, StepInfo } from '../../lib/api'
import { STEP_PLAIN } from '../../lib/plain'
import type { CaseStep } from '../../lib/stepper'
import type { LiveStep } from '../../lib/useInvestigation'
import { Panel } from './Panel'

const MARK = {
  done: { bg: 'var(--good)', fg: '#fff', Icon: Check },
  active: { bg: 'var(--brand)', fg: '#fff', Icon: Loader2 },
  failed: { bg: 'var(--critical)', fg: '#fff', Icon: X },
  todo: { bg: 'var(--surface-2)', fg: 'var(--muted)', Icon: null },
}

/** Alert received → Initial analysis → Graph investigation → Report → Analyst review. */
export function Stepper({ steps }: { steps: CaseStep[] }) {
  return (
    <ol className="card px-5 py-4 grid gap-4 grid-cols-2 sm:grid-cols-3 xl:grid-cols-5" aria-label="Case progress">
      {steps.map((s, i) => {
        const m = MARK[s.state]
        return (
          <li key={s.key} className="flex items-center gap-3 min-w-0" aria-current={s.state === 'active' ? 'step' : undefined}>
            <span className="w-9 h-9 rounded-full grid place-items-center shrink-0 text-sm font-semibold tabular"
              style={{ background: m.bg, color: m.fg, border: s.state === 'todo' ? '1.5px solid var(--axis)' : undefined }}>
              {m.Icon ? <m.Icon size={17} className={s.state === 'active' ? 'animate-spin' : ''} aria-hidden /> : i + 1}
            </span>
            <span className="flex flex-col min-w-0">
              <span className="text-sm font-semibold truncate">{s.label}</span>
              <span className="text-xs truncate" style={{ color: s.state === 'active' ? 'var(--brand)' : s.state === 'failed' ? 'var(--critical)' : 'var(--muted)' }}>
                {s.note}<span className="sr-only">, {s.state}</span>
              </span>
            </span>
          </li>
        )
      })}
    </ol>
  )
}

interface Row { step: number; tool: string; reason: string; done: boolean; found: string[] }

const fromResult = (steps: StepInfo[], result: CaseResult | null): Row[] =>
  steps.map((s) => ({
    step: s.step, tool: s.tool, reason: s.reason, done: true,
    found: (result?.evidence ?? []).filter((e) => e.step === s.step && e.kind !== 'context').map((e) => e.claim),
  }))

/** The agent's own steps: streamed live while it works, from the stored result afterwards. */
export function InvestigationProgress({ live, liveSteps, stored, result, error, writing }: {
  live: boolean; liveSteps: LiveStep[]; stored: StepInfo[]; result: CaseResult | null; error: string | null; writing: boolean
}) {
  const rows: Row[] = live || liveSteps.length ? liveSteps : fromResult(stored, result)
  const done = rows.filter((r) => r.done).length
  return (
    <Panel title="Investigation progress" icon={ListChecks}
      right={rows.length > 0 ? <span className="text-sm tabular" style={{ color: 'var(--ink-2)' }}>{done} of {rows.length} steps done</span> : undefined}>
      {rows.length > 0 && (
        <div className="h-2 rounded-full overflow-hidden -mt-1" style={{ background: 'var(--surface-2)' }} aria-hidden>
          <div className="h-full rounded-full transition-[width] duration-500" style={{ width: `${(100 * done) / rows.length}%`, background: 'var(--brand)' }} />
        </div>
      )}
      {rows.length === 0 && (
        <p className="text-sm flex items-center gap-2" style={{ color: 'var(--ink-2)' }}>
          {live ? <><Loader2 size={14} className="animate-spin" aria-hidden /> Starting the investigation…</> : 'The agent has not investigated this case yet.'}
        </p>
      )}
      <ol className="flex flex-col" aria-live="polite">
        {rows.map((r, i) => {
          const plain = STEP_PLAIN[r.tool]
          return (
            <li key={r.step} className="flex gap-3 py-2.5" style={{ borderTop: i ? '1px solid var(--border)' : undefined }}>
              {r.done ? <span className="w-6 h-6 rounded-full grid place-items-center shrink-0" style={{ background: 'var(--good)' }}><Check size={14} color="#fff" aria-hidden /></span>
                : <Loader2 size={24} className="animate-spin shrink-0" style={{ color: 'var(--brand)' }} aria-hidden />}
              <div className="min-w-0">
                <div className="text-sm font-medium">{plain?.label ?? r.tool}</div>
                <div className="text-xs" style={{ color: 'var(--muted)' }}>
                  {r.done ? (r.found.length ? `Found ${r.found.length} thing${r.found.length === 1 ? '' : 's'}` : 'Nothing new') : plain?.doing ?? 'Working'}
                </div>
                {r.reason && <p className="text-xs mt-0.5 italic" style={{ color: 'var(--ink-2)' }}>Why: {r.reason}</p>}
                {r.found.slice(0, 2).map((f, j) => <p key={j} className="text-xs mt-0.5" style={{ color: 'var(--ink-2)' }}>{f}</p>)}
              </div>
            </li>
          )
        })}
        {writing && (
          <li className="flex gap-3 py-2.5 text-sm" style={{ borderTop: '1px solid var(--border)', color: 'var(--ink-2)' }}>
            <Loader2 size={20} className="animate-spin shrink-0" style={{ color: 'var(--brand)' }} aria-hidden /> Writing the report…
          </li>
        )}
      </ol>
      {error && <p className="text-sm" style={{ color: 'var(--critical)' }}>{error}</p>}
    </Panel>
  )
}
