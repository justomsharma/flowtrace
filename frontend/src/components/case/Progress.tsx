import { AlertTriangle, Check, ListChecks, Loader2, Minus, ShieldCheck, X } from 'lucide-react'
import type { CaseResult, StepInfo } from '../../lib/api'
import { STEP_PLAIN, shortText } from '../../lib/plain'
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

interface Row { step: number; tool: string; reason: string; done: boolean; found: string[]; kinds?: string[] }

const fromResult = (steps: StepInfo[], result: CaseResult | null): Row[] =>
  steps.map((s) => {
    const ev = (result?.evidence ?? []).filter((e) => e.step === s.step && e.kind !== 'context')
    return { step: s.step, tool: s.tool, reason: s.reason, done: true, found: ev.map((e) => e.claim), kinds: ev.map((e) => e.kind) }
  })

/** Standard names for the checks, as they would appear in an audit file. */
const CHECK_NAME: Record<string, string> = {
  account_profile: 'Account profile',
  counterparty_history: 'Counterparty history',
  recurring_flows: 'Recurring payments',
  trace_funds: 'Fund tracing',
  shared_counterparties: 'Common beneficiary',
  find_cycles: 'Circular flows',
  owner_network: 'Related accounts (same owner)',
  case_memory: 'Prior cases',
}

const OUTCOME = {
  indicator: { label: 'Indicator found', bg: 'var(--flag-tint)', fg: 'var(--critical)', Icon: AlertTriangle },
  explained: { label: 'Explanation found', bg: 'var(--good-tint)', fg: 'var(--good)', Icon: ShieldCheck },
  clear: { label: 'No issue found', bg: 'var(--surface-2)', fg: 'var(--ink-2)', Icon: Minus },
}

/** The checks the investigator performed and what each one found: the case's audit trail. */
export function InvestigationProgress({ live, liveSteps, stored, result, error, writing, briefs = {} }: {
  live: boolean; liveSteps: LiveStep[]; stored: StepInfo[]; result: CaseResult | null; error: string | null; writing: boolean
  /** claim -> short line, from the case overview */
  briefs?: Record<string, string>
}) {
  const rows: Row[] = live || !stored.length ? liveSteps : fromResult(stored, result)
  const done = rows.filter((r) => r.done).length
  return (
    <Panel title="Checks performed" icon={ListChecks} sub="What the investigator checked, and what each check found"
      right={rows.length > 0 && live ? <span className="text-sm tabular" style={{ color: 'var(--ink-2)' }}>{done} of {rows.length}</span> : undefined}>
      {rows.length === 0 && (
        <p className="text-sm flex items-center gap-2" style={{ color: 'var(--ink-2)' }}>
          {live ? <><Loader2 size={14} className="animate-spin" aria-hidden /> Starting the investigation…</> : 'Not investigated yet.'}
        </p>
      )}
      <ol className="flex flex-col" aria-live="polite">
        {rows.map((r, i) => {
          const kinds = r.kinds ?? []
          const o = kinds.includes('support') ? OUTCOME.indicator : kinds.includes('counter') ? OUTCOME.explained : OUTCOME.clear
          const first = r.found[0]
          return (
            <li key={r.step} className="grid grid-cols-[minmax(0,1fr)_auto] gap-x-3 gap-y-0.5 py-2.5 items-start" style={{ borderTop: i ? '1px solid var(--border)' : undefined }}>
              <span className="text-sm font-medium flex items-center gap-2">
                {!r.done && <Loader2 size={14} className="animate-spin" style={{ color: 'var(--brand)' }} aria-hidden />}
                {CHECK_NAME[r.tool] ?? STEP_PLAIN[r.tool]?.label ?? r.tool}
              </span>
              {r.done ? (
                <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-md text-xs font-medium whitespace-nowrap" style={{ background: o.bg, color: o.fg }}>
                  <o.Icon size={12} aria-hidden />{o.label}
                </span>
              ) : <span className="text-xs" style={{ color: 'var(--muted)' }}>Running</span>}
              {r.done && first && (
                <span className="col-span-2 text-xs" style={{ color: 'var(--ink-2)' }}>
                  {briefs[first] ?? shortText(first)}{r.found.length > 1 ? ` (+${r.found.length - 1} more)` : ''}
                </span>
              )}
            </li>
          )
        })}
        {writing && (
          <li className="flex gap-2 py-2.5 text-sm" style={{ borderTop: '1px solid var(--border)', color: 'var(--ink-2)' }}>
            <Loader2 size={16} className="animate-spin shrink-0" style={{ color: 'var(--brand)' }} aria-hidden /> Writing the report…
          </li>
        )}
      </ol>
      {error && <p className="text-sm" style={{ color: 'var(--critical)' }}>{error}</p>}
    </Panel>
  )
}
