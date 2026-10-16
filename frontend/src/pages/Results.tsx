import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import { getJSON, type Action, type AuditEntry } from '../lib/api'
import { fmtMoney } from '../lib/money'
import { ACTION_PLAIN, shortAcct } from '../lib/plain'
import { timeLeft, untilLabel, useNow } from '../lib/time'

const EVENT_PLAIN: Record<AuditEntry['event'], string> = {
  PROPOSED: 'Proposed',
  APPROVED: 'Approved',
  REJECTED: 'Rejected',
  AUTO_RELEASED: 'Released: time up',
  RELEASED: 'Released early',
  VERDICT: 'Verdict recorded',
  CASE_REUPLOADED: 'Withdrawn: file uploaded again',
  SUPERSEDED: 'Withdrawn: another decision was approved',
  WITHDRAWN: 'Withdrawn: the case was closed',
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
  const now = useNow(1000)

  const load = useCallback(() => {
    Promise.all([getJSON<Action[]>('/api/actions'), getJSON<AuditEntry[]>('/api/audit?limit=200')])
      .then(([a, l]) => { setActions(a); setAudit(l); setError(null) })
      .catch((e) => setError(String(e)))
  }, [])
  useEffect(() => { load(); const t = setInterval(load, 15000); return () => clearInterval(t) }, [load])

  const active = useMemo(() => (actions ?? []).filter((a) => a.type === 'HOLD' && a.status === 'APPROVED')
    .sort((a, b) => (a.expires_ts ?? 0) - (b.expires_ts ?? 0)), [actions])
  const today = active.filter((a) => a.decided_at && sameDay(a.decided_at, now))
  const waiting = (actions ?? []).filter((a) => a.status === 'PROPOSED')

  // Reload as soon as a hold's time is up, so it shows as released without waiting for the next poll.
  const reloaded = useRef<string | null>(null)
  const due = active.find((a) => a.expires_ts && a.expires_ts <= now)
  useEffect(() => { if (due && reloaded.current !== due.id) { reloaded.current = due.id; load() } }, [due, load])

  return (
    <div className="mx-auto max-w-5xl flex flex-col gap-6 px-1 py-3">
      <header className="flex flex-col gap-5">
        <div>
          <h1 className="text-xl font-semibold">Holds &amp; audit</h1>
          <p className="text-sm" style={{ color: 'var(--ink-2)' }}>What people decided on the agent's proposals, and the money held because of it.</p>
        </div>
        <div className="flex flex-wrap gap-x-14 gap-y-4">
        <Figure label="Frozen today" value={today.length ? total(today) : '0'} note={`${today.length} hold${today.length === 1 ? '' : 's'} approved today${mixed(today) ? ', in US dollars' : ''}`} accent />
        <Figure label="Holds in place" value={String(active.length)} note={active.length ? `${total(active)} frozen` : 'none right now'} />
        <Figure label="Waiting for a decision" value={String(waiting.length)} note="proposals nobody has decided yet" />
        </div>
      </header>
      {error && <p className="text-sm" style={{ color: 'var(--critical)' }}>{error}</p>}

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
                  <th className="pr-4 font-medium text-right">Frozen</th><th className="pr-4 font-medium">Approved by</th>
                  <th className="pr-4 font-medium">Ends</th><th className="font-medium text-right">Time left</th>
                </tr>
              </thead>
              <tbody>
                {active.map((a) => (
                  <tr key={a.id} style={{ borderTop: '1px solid var(--border)' }}>
                    <td className="py-2 pr-4">{a.account ? shortAcct(a.account) : ''}</td>
                    <td className="pr-4"><Link to={caseLink(a.case_id)} className="underline underline-offset-2">{a.case_id}</Link></td>
                    <td className="pr-4 text-right font-semibold" style={{ color: 'var(--s4)' }}>{fmtMoney(a.hold_amount ?? 0, a.currency, a.rate)}</td>
                    <td className="pr-4">{a.decided_by}</td>
                    <td className="pr-4" style={{ color: 'var(--ink-2)' }}>{a.expires_ts ? untilLabel(a.expires_ts) : ''}</td>
                    <td className="text-right font-semibold">{a.expires_ts ? timeLeft(a.expires_ts, now) : ''}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>

      {waiting.length > 0 && (
        <section className="card p-4 flex flex-col gap-3">
          <h2 className="text-sm font-semibold">Waiting for a decision</h2>
          <ul className="flex flex-col">
            {waiting.map((a) => (
              <li key={a.id} className="flex items-baseline gap-4 py-2 text-sm" style={{ borderTop: '1px solid var(--border)' }}>
                <Link to={caseLink(a.case_id)} className="underline underline-offset-2 tabular">{a.case_id}</Link>
                <span className="flex-1">{ACTION_PLAIN[a.type].title}{a.account ? ` account ${shortAcct(a.account)}` : ''}
                  {a.proposed_by === 'mcp' && <span className="text-xs" style={{ color: 'var(--muted)' }}> (proposed through MCP)</span>}</span>
                {a.type === 'HOLD' && <span className="tabular" style={{ color: 'var(--ink-2)' }}>{fmtMoney(a.hold_amount ?? 0, a.currency, a.rate)}</span>}
              </li>
            ))}
          </ul>
        </section>
      )}

      <section className="card p-4 flex flex-col gap-3">
        <div>
          <h2 className="text-sm font-semibold">Audit log</h2>
          <p className="text-xs" style={{ color: 'var(--muted)' }}>Every proposal and decision, newest first. Entries are never changed or deleted.</p>
        </div>
        {audit.length === 0 ? (
          <p className="text-sm" style={{ color: 'var(--ink-2)' }}>Nothing yet.</p>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="text-left text-xs" style={{ color: 'var(--muted)' }}>
                  <th className="py-1.5 pr-4 font-medium">When</th><th className="pr-4 font-medium">Who</th>
                  <th className="pr-4 font-medium">What</th><th className="pr-4 font-medium">Case</th>
                  <th className="pr-4 font-medium">Account</th><th className="font-medium">Reason</th>
                </tr>
              </thead>
              <tbody>
                {audit.map((e) => (
                  <tr key={e.id} className="align-top [&>td]:py-2" style={{ borderTop: '1px solid var(--border)' }}>
                    <td className="pr-4 whitespace-nowrap tabular" style={{ color: 'var(--ink-2)' }}>{untilLabel(e.ts)}</td>
                    <td className="pr-4 whitespace-nowrap">{e.actor === 'agent' ? 'Agent' : e.actor === 'mcp' ? 'MCP client' : e.actor === 'system' ? 'System' : e.actor}</td>
                    <td className="pr-4 whitespace-nowrap">{EVENT_PLAIN[e.event] ?? e.event}</td>
                    <td className="pr-4 whitespace-nowrap tabular"><Link to={caseLink(e.case_id)} className="underline underline-offset-2">{e.case_id}</Link></td>
                    <td className="pr-4 tabular">{e.account ? shortAcct(e.account) : ''}</td>
                    <td className="max-w-md" style={{ color: 'var(--ink-2)' }}>{e.reason}</td>
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

function Figure({ label, value, note, accent = false }: { label: string; value: string; note: string; accent?: boolean }) {
  return (
    <div className="flex flex-col">
      <span className="text-xs" style={{ color: 'var(--ink-2)' }}>{label}</span>
      <span className="text-3xl font-semibold tabular leading-tight" style={{ color: accent ? 'var(--s4)' : 'var(--ink)' }}>{value}</span>
      <span className="text-xs" style={{ color: 'var(--muted)' }}>{note}</span>
    </div>
  )
}
