import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import { AlertTriangle, CircleCheck, MinusCircle, OctagonAlert, type LucideIcon } from 'lucide-react'
import { getJSON, type Action, type AuditEntry, type AutoHoldPolicy } from '../lib/api'
import { holdPromise, isAutoHold } from '../lib/holds'
import { useBatch } from '../lib/batch'
import { currencyNote, fmtMoney } from '../lib/money'
import { ACTION_PLAIN, caseNo } from '../lib/plain'
import { fullId } from '../lib/names'
import { timeLeft, untilLabel, useNow } from '../lib/time'

const EVENT_PLAIN: Record<AuditEntry['event'], string> = {
  PROPOSED: 'Proposed',
  PROVISIONAL: 'Held provisionally (auto-hold)',
  APPROVED: 'Approved',
  REJECTED: 'Rejected',
  AUTO_RELEASED: 'Released: time up',
  RELEASED: 'Released early',
  VERDICT: 'Verdict recorded',
  CASE_REUPLOADED: 'Withdrawn: file uploaded again',
  SUPERSEDED: 'Withdrawn: another decision was approved',
  WITHDRAWN: 'Withdrawn: the case was closed',
}

/** Every audit entry falls in one of four groups, so an analyst sees at a glance what needs them. */
type Group = 'review' | 'alert' | 'cleared' | 'withdrawn'
const GROUP: Record<Group, { label: string; Icon: LucideIcon; fg: string; bg: string }> = {
  review: { label: 'Needs review', Icon: AlertTriangle, fg: '#94620a', bg: 'var(--hold-tint)' },
  alert: { label: 'Escalated / Blocked', Icon: OctagonAlert, fg: 'var(--critical)', bg: 'var(--flag-tint)' },
  cleared: { label: 'Cleared', Icon: CircleCheck, fg: 'var(--good)', bg: 'var(--good-tint)' },
  withdrawn: { label: 'Withdrawn', Icon: MinusCircle, fg: 'var(--ink-2)', bg: 'var(--surface-2)' },
}
const BLOCKING = new Set(['HOLD', 'ESCALATE', 'FILE_STR'])

function group(e: AuditEntry, type?: string): Group {
  switch (e.event) {
    case 'PROPOSED':
    case 'PROVISIONAL': return 'review'                    // a person still has to approve, confirm or reject it
    case 'APPROVED': return type && BLOCKING.has(type) ? 'alert' : 'cleared'
    case 'VERDICT': return e.to_status === 'ESCALATE' ? 'alert' : e.to_status === 'CLOSE' ? 'cleared' : 'review'
    case 'AUTO_RELEASED':
    case 'RELEASED': return 'cleared'
    default: return 'withdrawn'                            // rejected, superseded, re-uploaded, withdrawn
  }
}

/** "Hold proposed", "Escalation approved", "Hold released: time up". */
function what(e: AuditEntry, type?: string): string {
  const t = type ? ACTION_PLAIN[type as keyof typeof ACTION_PLAIN]?.title ?? type : ''
  switch (e.event) {
    case 'PROPOSED': return `${t} proposed`
    case 'APPROVED': return `${t} approved`
    case 'REJECTED': return `${t} rejected`
    default: return EVENT_PLAIN[e.event] ?? e.event
  }
}

function StatusPill({ g }: { g: Group }) {
  const s = GROUP[g]
  return (
    <span className="inline-flex items-center gap-1.5 px-2 py-0.5 rounded-md text-xs font-medium whitespace-nowrap" style={{ background: s.bg, color: s.fg }}>
      <s.Icon size={13} aria-hidden />{s.label}
    </span>
  )
}


const caseLink = (caseId: string) => {
  const i = caseId.lastIndexOf('-C')
  return i > 0 ? `/case?batch=${caseId.slice(0, i)}&case=${caseId}` : '/'
}

const sameDay = (a: number, b: number) => new Date(a).toDateString() === new Date(b).toDateString()

const mixed = (holds: Action[]) => new Set(holds.map((h) => h.currency ?? 'US Dollar')).size > 1

/** Sum of holds in their own currency when they share one; otherwise in US dollars. */
function total(holds: Action[]): string {
  const usd = holds.reduce((s, h) => s + (h.hold_amount ?? 0), 0)
  return mixed(holds) ? fmtMoney(usd) : fmtMoney(usd, holds[0]?.currency, holds[0]?.rate)
}

export default function Results() {
  const [actions, setActions] = useState<Action[] | null>(null)
  const [audit, setAudit] = useState<AuditEntry[]>([])
  const [error, setError] = useState<string | null>(null)
  const [filter, setFilter] = useState<Group | 'all'>('all')
  const [policy, setPolicy] = useState<AutoHoldPolicy | null>(null)
  useEffect(() => { getJSON<{ auto_hold?: AutoHoldPolicy }>('/api/health').then((h) => setPolicy(h.auto_hold ?? null)).catch(() => undefined) }, [])
  const now = useNow(1000)

  const load = useCallback(() => {
    Promise.all([getJSON<Action[]>('/api/actions'), getJSON<AuditEntry[]>('/api/audit?limit=200')])
      .then(([a, l]) => { setActions(a); setAudit(l); setError(null) })
      .catch((e) => setError(String(e)))
  }, [])
  useEffect(() => { load(); const t = setInterval(load, 15000); return () => clearInterval(t) }, [load])

  // Holds in place: approved by a person, or provisional (an auto-hold waiting for a person).
  const ends = (a: Action) => (a.status === 'PROVISIONAL' ? a.provisional_until : a.expires_ts) ?? 0
  const active = useMemo(() => (actions ?? []).filter((a) => a.type === 'HOLD' && (a.status === 'APPROVED' || a.status === 'PROVISIONAL'))
    .sort((a, b) => ends(a) - ends(b)), [actions])
  const today = active.filter((a) => { const t = a.decided_at ?? a.held_at; return !!t && sameDay(t, now) })
  const waiting = (actions ?? []).filter((a) => a.status === 'PROPOSED' || a.status === 'PROVISIONAL')
  const { batch } = useBatch()
  const inBatch = (a: Action) => !!batch && a.case_id.startsWith(`${batch.batch_id}-`)
  const mine = (actions ?? []).filter(inBatch)
  const investigated = (batch?.cases ?? []).filter((x) => x.result).length
  const verdicts = (batch?.cases ?? []).reduce((c, x) => {
    const k = x.result?.action ?? 'PENDING'
    return { ...c, [k]: (c[k] ?? 0) + 1 }
  }, {} as Record<string, number>)
  const proposedHolds = mine.filter((a) => a.type === 'HOLD' && a.status === 'PROPOSED')
  const decided = mine.filter((a) => a.status === 'APPROVED' || a.status === 'REJECTED').length
  const autoHeld = mine.filter((a) => a.type === 'HOLD' && isAutoHold(a)).length
  const waitingHere = new Set(mine.filter((a) => a.status === 'PROPOSED' || a.status === 'PROVISIONAL').map((a) => a.case_id)).size
  // One row per case in "Needs review": a case usually carries several proposals (an STR plus holds).
  const byCase = useMemo(() => {
    const m = new Map<string, Action[]>()
    for (const a of waiting) m.set(a.case_id, [...(m.get(a.case_id) ?? []), a])
    return [...m.entries()]
  }, [waiting])
  const typeOf = useMemo(() => Object.fromEntries((actions ?? []).map((a) => [a.id, a.type])), [actions])
  const rows = useMemo(() => audit.map((e) => ({ e, g: group(e, typeOf[e.action_id]) })), [audit, typeOf])
  const counts = useMemo(() => rows.reduce((c, r) => ({ ...c, [r.g]: (c[r.g] ?? 0) + 1 }), {} as Record<Group, number>), [rows])
  const shown = filter === 'all' ? rows : rows.filter((r) => r.g === filter)

  // Reload as soon as a hold's time is up, so it shows as released without waiting for the next poll.
  const reloaded = useRef<string | null>(null)
  const due = active.find((a) => ends(a) && ends(a) <= now)
  useEffect(() => { if (due && reloaded.current !== due.id) { reloaded.current = due.id; load() } }, [due, load])

  return (
    <div className="px-6 py-5 flex flex-col gap-6">
      <header className="flex flex-col gap-5">
        <div>
          <h1 className="text-xl font-semibold">Holds &amp; audit</h1>
          <p className="text-sm" style={{ color: 'var(--ink-2)' }}>What people decided on the agent's proposals, and the money held because of it. {currencyNote()}</p>
        </div>
        <div className="flex flex-wrap gap-x-14 gap-y-4">
        <Figure label="Frozen today" value={today.length ? total(today) : '$0'}
          note={today.length ? `${today.length} hold${today.length === 1 ? '' : 's'} placed today`
            : waiting.some((a) => a.type === 'HOLD') ? 'holds are proposed, none approved yet' : 'no hold approved today'} accent />
        <Figure label="Holds in place" value={String(active.length)} note={active.length
          ? `${total(active)} frozen${active.some((a) => a.status === 'PROVISIONAL') ? `, ${active.filter((a) => a.status === 'PROVISIONAL').length} provisional (auto-hold)` : ''}`
          : 'none right now'} />
        <Figure label="Needs review" value={String(byCase.length)} note={`case${byCase.length === 1 ? '' : 's'} across all uploads, ${waiting.length} proposal${waiting.length === 1 ? '' : 's'} in total`} />
        </div>
      </header>
      {error && <p className="text-sm" style={{ color: 'var(--critical)' }}>{error}</p>}

      {batch && (
        <section className="card p-4 flex flex-col gap-4" aria-labelledby="summary-title">
          <div>
            <h2 id="summary-title" className="text-sm font-semibold">Summary of the latest upload</h2>
            <p className="text-xs" style={{ color: 'var(--muted)' }}>{batch.filename}: from every payment checked to what an analyst still has to decide.</p>
          </div>
          <ol className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
            <Step n={batch.rows} label="Payments checked" note={`${(batch.rows - batch.flagged_tx).toLocaleString('en-US')} passed as not suspicious`} />
            <Step n={batch.flagged_tx} label="Payments flagged" note={`grouped into ${batch.cases.length} case${batch.cases.length === 1 ? '' : 's'}`} />
            <Step n={investigated} label="Cases investigated by the agent"
              note={[['ESCALATE', 'escalate'], ['REVIEW', 'review'], ['CLOSE', 'close'], ['PENDING', 'not yet investigated']]
                .filter(([k]) => verdicts[k]).map(([k, w]) => `${verdicts[k]} ${w}`).join(' · ') || 'none yet'} />
            <Step n={waitingHere} label="Cases waiting for an analyst" note={`${decided} decision${decided === 1 ? '' : 's'} made so far${autoHeld ? `, ${autoHeld} hold${autoHeld === 1 ? '' : 's'} placed by auto-hold` : ''}`} warn />
          </ol>
          <p className="text-sm" style={{ color: 'var(--ink-2)' }}>
            {active.length > 0
              ? <>Frozen now: <b style={{ color: 'var(--ink)' }}>{total(active)}</b> in {active.length} hold{active.length === 1 ? '' : 's'} in place.</>
              : proposedHolds.length > 0
                ? <>Nothing is frozen yet: <b style={{ color: 'var(--ink)' }}>{fmtMoney(proposedHolds.reduce((t, a) => t + (a.hold_amount ?? 0), 0))}</b> across {proposedHolds.length} proposed hold{proposedHolds.length === 1 ? '' : 's'} waits for an analyst to approve.</>
                : 'Nothing is frozen: no hold has been proposed or approved.'}
            {' '}{holdPromise(policy)}
          </p>
        </section>
      )}

      <section className="card p-4 flex flex-col gap-3">
        <h2 className="text-sm font-semibold">Holds in place</h2>
        {actions && active.length === 0 && (
          <p className="text-sm" style={{ color: 'var(--ink-2)' }}>No holds yet. Open a case and approve a proposed hold to start one.</p>
        )}
        {active.length > 0 && (
          <div className="overflow-x-auto">
            <table className="w-full text-sm tabular whitespace-nowrap">
              <thead>
                <tr className="text-left text-xs" style={{ color: 'var(--muted)' }}>
                  <th className="py-1.5 pr-4 font-medium">Account</th><th className="pr-4 font-medium">Case</th>
                  <th className="pr-4 font-medium text-right">Frozen</th><th className="pr-4 font-medium">Held by</th>
                  <th className="pr-4 font-medium">Ends</th><th className="font-medium text-right">Time left</th>
                </tr>
              </thead>
              <tbody>
                {active.map((a) => (
                  <tr key={a.id} style={{ borderTop: '1px solid var(--border)' }}>
                    <td className="py-2 pr-4 font-mono text-xs">{a.account ? fullId(a.account) : ''}</td>
                    <td className="pr-4"><Link to={caseLink(a.case_id)} className="underline underline-offset-2">Case {caseNo(a.case_id)}</Link></td>
                    <td className="pr-4 text-right font-semibold" style={{ color: 'var(--s4)' }}>{fmtMoney(a.hold_amount ?? 0, a.currency, a.rate)}</td>
                    <td className="pr-4">{a.status === 'PROVISIONAL' ? 'Agent (auto-hold), waiting for a person' : a.decided_by}</td>
                    <td className="pr-4" style={{ color: 'var(--ink-2)' }}>{ends(a) ? untilLabel(ends(a)) : ''}</td>
                    <td className="text-right font-semibold">{ends(a) ? timeLeft(ends(a), now) : ''}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>

      {byCase.length > 0 && (
        <section className="card p-4 flex flex-col gap-3">
          <div>
            <h2 className="text-sm font-semibold flex items-center gap-2"><AlertTriangle size={15} style={{ color: '#94620a' }} aria-hidden />Needs review: {byCase.length} case{byCase.length === 1 ? '' : 's'}</h2>
            <p className="text-xs" style={{ color: 'var(--muted)' }}>
              Every proposal, including closing a low-risk case, waits for an analyst to approve or reject it. {holdPromise(policy)}
            </p>
          </div>
          <ul className="flex flex-col">
            {byCase.map(([cid, list]) => {
              const blocking = list.some((a) => BLOCKING.has(a.type))
              const G = blocking ? GROUP.alert : GROUP.cleared
              const holds = list.filter((a) => a.type === 'HOLD')
              const other = list.filter((a) => a.type !== 'HOLD')
              return (
                <li key={cid} className="grid grid-cols-[72px_20px_1fr_auto] items-baseline gap-3 py-2 text-sm" style={{ borderTop: '1px solid var(--border)' }}>
                  <Link to={caseLink(cid)} className="underline underline-offset-2 tabular">Case {caseNo(cid)}</Link>
                  <G.Icon size={15} className="self-center" style={{ color: G.fg }} aria-label={blocking ? 'Escalation or block proposed' : 'Clearance proposed'} />
                  <span>
                    {other.map((a) => ACTION_PLAIN[a.type].title).join(', ')}
                    {holds.length > 0 && `${other.length ? ' + ' : ''}${holds.length} hold${holds.length === 1 ? '' : 's'}`}
                    {!blocking && <span className="text-xs" style={{ color: 'var(--muted)' }}> (agent found it low risk; confirm or reopen)</span>}
                  </span>
                  <span className="tabular" style={{ color: 'var(--ink-2)' }}>{holds.length ? fmtMoney(holds.reduce((t, a) => t + (a.hold_amount ?? 0), 0)) : ''}</span>
                </li>
              )
            })}
          </ul>
        </section>
      )}

      <section className="card p-4 flex flex-col gap-3">
        <div className="flex flex-wrap items-end justify-between gap-3">
          <div>
            <h2 className="text-sm font-semibold">Audit log</h2>
            <p className="text-xs" style={{ color: 'var(--muted)' }}>Every proposal and decision, newest first. Entries are never changed or deleted.</p>
          </div>
          <div role="group" aria-label="Filter the audit log" className="flex flex-wrap gap-1.5">
            {(['all', 'review', 'alert', 'cleared', 'withdrawn'] as const).map((k) => {
              const n = k === 'all' ? rows.length : counts[k] ?? 0
              const on = filter === k
              return (
                <button key={k} onClick={() => setFilter(k)} aria-pressed={on}
                  className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full text-xs font-medium"
                  style={{ border: '1px solid var(--border)', background: on ? 'var(--ink)' : 'transparent', color: on ? 'var(--page)' : 'var(--ink-2)' }}>
                  {k !== 'all' && (() => { const G = GROUP[k]; return <G.Icon size={12} style={{ color: on ? 'inherit' : G.fg }} aria-hidden /> })()}
                  {k === 'all' ? 'All' : GROUP[k].label} <span className="tabular">{n}</span>
                </button>
              )
            })}
          </div>
        </div>
        {shown.length === 0 ? (
          <p className="text-sm" style={{ color: 'var(--ink-2)' }}>{audit.length ? 'No entries in this group.' : 'Nothing yet.'}</p>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm min-w-[1100px]">
              <thead>
                <tr className="text-left text-xs" style={{ color: 'var(--muted)' }}>
                  <th className="py-1.5 pr-4 font-medium">Status</th>
                  <th className="pr-4 font-medium">When</th><th className="pr-4 font-medium">Who</th>
                  <th className="pr-4 font-medium">What</th><th className="pr-4 font-medium">Case</th>
                  <th className="pr-4 font-medium">Account</th><th className="font-medium">Reason</th>
                </tr>
              </thead>
              <tbody>
                {shown.map(({ e, g }) => (
                  <tr key={e.id} className="align-top [&>td]:py-2" style={{ borderTop: '1px solid var(--border)' }}>
                    <td className="pr-4"><StatusPill g={g} /></td>
                    <td className="pr-4 whitespace-nowrap tabular" style={{ color: 'var(--ink-2)' }}>{untilLabel(e.ts)}</td>
                    <td className="pr-4 whitespace-nowrap">{e.actor === 'agent' ? 'Agent' : e.actor === 'mcp' ? 'MCP client' : e.actor === 'system' ? 'System' : e.actor.includes('auto-hold') ? 'Agent (auto-hold)' : e.actor}</td>
                    <td className="pr-4 whitespace-nowrap">{what(e, typeOf[e.action_id])}</td>
                    <td className="pr-4 whitespace-nowrap tabular"><Link to={caseLink(e.case_id)} className="underline underline-offset-2">Case {caseNo(e.case_id)}</Link></td>
                    <td className="pr-4 whitespace-nowrap font-mono text-xs">{e.account ? fullId(e.account) : ''}</td>
                    <td className="min-w-[260px]" style={{ color: 'var(--ink-2)' }}>{e.reason}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </div>
  )
}

function Step({ n, label, note, warn = false }: { n: number; label: string; note: string; warn?: boolean }) {
  return (
    <li className="rounded-lg p-3 flex flex-col" style={{ background: warn ? 'var(--hold-tint)' : 'var(--surface-2)' }}>
      <span className="text-2xl font-semibold tabular leading-tight">{n.toLocaleString('en-US')}</span>
      <span className="text-sm font-medium">{label}</span>
      <span className="text-xs" style={{ color: 'var(--ink-2)' }}>{note}</span>
    </li>
  )
}

function Figure({ label, value, note, accent = false }: { label: string; value: string; note: string; accent?: boolean }) {
  return (
    <div className="flex flex-col">
      <span className="text-xs" style={{ color: 'var(--ink-2)' }}>{label}</span>
      <span className="text-3xl font-semibold tabular leading-tight" style={{ color: accent ? 'var(--s4)' : 'var(--ink)' }}>{value}</span>
      <span className="text-xs" style={{ color: 'var(--muted)' }}>{note}</span>
    </div>
  )
}
