import { useCallback, useEffect, useRef, useState } from 'react'
import { Loader2, Lock, ShieldCheck, XCircle } from 'lucide-react'
import { fmtTime, getJSON, sendJSON, type Action, type CaseResult, type HoldPlan } from '../lib/api'
import { currencyNote, fmtMoney } from '../lib/money'
import { ACTION_PLAIN, shortAcct } from '../lib/plain'
import { useNow, timeLeft, untilLabel } from '../lib/time'

const MIN_REASON = 5
const NAME_KEY = 'flowtrace.analyst'

function readName(): string {
  try { return localStorage.getItem(NAME_KEY) ?? '' } catch { return '' }
}

/** The agent's proposed holds and case decision for one case. Nothing happens until a person approves.
 *  Mount it with key={caseId}: each case starts from a fresh panel. */
export default function HoldPanel({ caseId, result, refreshKey, onPlan }: {
  caseId: string
  result: CaseResult
  refreshKey: number
  onPlan: (plan: HoldPlan | null, actions: Action[]) => void
}) {
  const [plan, setPlan] = useState<HoldPlan | null>(null)
  const [actions, setActions] = useState<Action[]>([])
  const [loaded, setLoaded] = useState(false)
  const [planning, setPlanning] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [name, setName] = useState(readName)
  const planKey = useRef(crypto.randomUUID())

  const load = useCallback(() => {
    getJSON<{ plan: HoldPlan | null; actions: Action[] }>(`/api/cases/${caseId}/plan`)
      .then((r) => { setPlan(r.plan); setActions(r.actions); setLoaded(true); onPlan(r.plan, r.actions) })
      .catch((e) => { setError(String(e)); setLoaded(true) })
  }, [caseId, onPlan])

  useEffect(() => { load() }, [load, refreshKey])

  async function planNow() {
    setPlanning(true); setError(null)
    try {
      const r = await sendJSON<{ plan: HoldPlan; actions: Action[] }>(`/api/cases/${caseId}/plan`, undefined, planKey.current)
      setPlan(r.plan); onPlan(r.plan, r.actions); load()
    } catch (e) { setError((e as Error).message) } finally { setPlanning(false) }
  }

  const saveName = (v: string) => { setName(v); try { localStorage.setItem(NAME_KEY, v) } catch { /* private window */ } }
  const holds = actions.filter((a) => a.type === 'HOLD' || a.type === 'RELEASE')
    .sort((x, y) => (y.amount_blocked ?? 0) - (x.amount_blocked ?? 0))
  const decisions = actions.filter((a) => a.type !== 'HOLD' && a.type !== 'RELEASE')
  const evidence = Object.fromEntries(result.evidence.map((e) => [e.id, e.claim]))
  const payments = Object.fromEntries((plan?.network.edges ?? []).map((e) => [e.tx_id, e]))

  return (
    <section aria-labelledby="hold-title" className="flex flex-col gap-3 rounded-lg p-3"
      style={{ border: '1px solid color-mix(in srgb, var(--s4) 45%, var(--border))' }}>
      <h3 id="hold-title" className="flex items-center gap-2 text-sm font-semibold">
        <Lock size={15} style={{ color: 'var(--s4)' }} aria-hidden /> Confirm hold
      </h3>

      {!loaded && <p className="text-xs flex items-center gap-2" style={{ color: 'var(--ink-2)' }}><Loader2 size={12} className="animate-spin" /> Loading the plan…</p>}

      {loaded && !plan && (
        <div className="flex flex-col gap-2">
          <p className="text-sm" style={{ color: 'var(--ink-2)' }}>No hold plan yet for this case.</p>
          <button onClick={planNow} disabled={planning} className="self-start px-3 py-2 rounded-lg text-sm font-medium disabled:opacity-60"
            style={{ background: 'var(--s4)', color: '#1a1300' }}>
            {planning ? 'Planning…' : 'Plan holds'}
          </button>
        </div>
      )}

      {plan && <Effect plan={plan} open={holds.filter((a) => a.type === 'HOLD' && (a.status === 'PROPOSED' || a.status === 'APPROVED'))} />}

      {holds.length > 0 && (
        <ul className="flex flex-col gap-2.5">
          {holds.map((a) => <ActionRow key={a.id} action={a} evidence={evidence} payments={payments} name={name} onChange={load} />)}
        </ul>
      )}

      {decisions.length > 0 && (
        <div className="flex flex-col gap-2">
          <h4 className="text-xs font-semibold" style={{ color: 'var(--ink-2)' }}>Case decision</h4>
          <ul className="flex flex-col gap-2.5">
            {decisions.map((a) => <ActionRow key={a.id} action={a} evidence={evidence} payments={payments} name={name} onChange={load} />)}
          </ul>
        </div>
      )}

      {actions.some((a) => a.status === 'PROPOSED') && (
        <label className="flex items-center gap-2 text-xs" style={{ color: 'var(--ink-2)' }}>
          Deciding as
          <input value={name} onChange={(e) => saveName(e.target.value)} placeholder="your name"
            className="flex-1 min-w-0 rounded-md px-2 py-1 text-xs"
            style={{ background: 'var(--surface-2)', border: '1px solid var(--border)', color: 'var(--ink)' }} />
        </label>
      )}
      {error && <p className="text-xs" style={{ color: 'var(--critical)' }}>{error}</p>}
    </section>
  )
}

/** "Holding 2 accounts blocks X of Y still in motion", with the money split into blocked, loose and gone.
 *  Counts the holds still proposed or approved, so a rejection shows straight away. */
function Effect({ plan, open }: { plan: HoldPlan; open: Action[] }) {
  const m = (v: number) => fmtMoney(v, plan.currency, plan.rate)
  const blocked = Math.min(open.reduce((s, a) => s + (a.amount_blocked ?? 0), 0), plan.in_motion)
  const loose = Math.max(plan.in_motion - blocked, 0)
  const total = plan.in_motion + plan.already_out
  const n = open.length
  let line: string
  if (n > 0) {
    line = `Holding ${n} account${n === 1 ? '' : 's'} blocks ${m(blocked)} of ${m(plan.in_motion)} still in motion.`
  } else if (plan.holds.length > 0) {
    line = `No hold is open, so ${m(plan.in_motion)} is still free to move.`
  } else if (plan.in_motion <= 0) {
    line = 'No case money is still sitting in the trail, so there is nothing to hold.'
  } else {
    line = `${m(plan.in_motion)} is still in motion, but no account here can be held.`
  }
  const seg = [
    { v: blocked, c: 'var(--s4)', label: 'Blocked by these holds' },
    { v: loose, c: 'var(--axis)', label: 'Still loose' },
    { v: plan.already_out, c: 'var(--s8)', label: 'Already gone' },
  ]
  return (
    <div className="flex flex-col gap-2">
      <p className="text-[15px] leading-snug font-medium tabular">{line}</p>
      {total > 0 && (
        <>
          <div className="flex h-2.5 w-full overflow-hidden rounded-full" style={{ background: 'var(--surface-2)' }}
            role="img" aria-label={seg.map((s) => `${s.label}: ${m(s.v)}`).join(', ')}>
            {seg.map((s) => s.v > 0 && <span key={s.label} style={{ width: `${(100 * s.v) / total}%`, background: s.c }} />)}
          </div>
          <ul className="flex flex-wrap gap-x-4 gap-y-1 text-xs" style={{ color: 'var(--ink-2)' }}>
            {seg.map((s) => (
              <li key={s.label} className="flex items-center gap-1.5 tabular">
                <span className="w-2 h-2 rounded-full" style={{ background: s.c }} aria-hidden />
                {s.label} {m(s.v)}
              </li>
            ))}
          </ul>
        </>
      )}
      {plan.already_out > 0 && (
        <p className="text-xs" style={{ color: 'var(--muted)' }}>
          Already gone means it left in cash, crypto or another currency, or reached a merchant. A hold cannot bring it back.
        </p>
      )}
      {n !== plan.holds.length && plan.holds.length > 0 && (
        <p className="text-xs" style={{ color: 'var(--muted)' }}>
          The agent proposed {plan.holds.length} hold{plan.holds.length === 1 ? '' : 's'} blocking {m(plan.blocked)}.
        </p>
      )}
      {plan.note && <p className="text-xs" style={{ color: 'var(--ink-2)' }}>{plan.note}</p>}
      {plan.skipped_hubs.length > 0 && (
        <p className="text-xs" style={{ color: 'var(--muted)' }}>
          Not held: {plan.skipped_hubs.length} busy account{plan.skipped_hubs.length === 1 ? '' : 's'} (merchant or processor). FlowTrace never holds those.
        </p>
      )}
      <p className="text-xs" style={{ color: 'var(--muted)' }}>{currencyNote(plan.currency)}</p>
    </div>
  )
}

function ActionRow({ action: a, evidence, payments, name, onChange }: {
  action: Action
  evidence: Record<string, string>
  payments: Record<string, HoldPlan['network']['edges'][number]>
  name: string
  onChange: () => void
}) {
  const [reason, setReason] = useState('')
  const [busy, setBusy] = useState<'approve' | 'reject' | null>(null)
  const [error, setError] = useState<string | null>(null)
  const key = useRef(crypto.randomUUID())  // one per decision: a double click returns the first answer
  const now = useNow(a.status === 'APPROVED' ? 1000 : 0)
  const m = (v = 0) => fmtMoney(v, a.currency, a.rate)
  const isHold = a.type === 'HOLD'
  const viaRoute = Math.max((a.amount_blocked ?? 0) - (a.frozen_now ?? 0), 0)
  const claims = (a.evidence_ids ?? []).map((id) => evidence[id]).filter(Boolean)
  const into = (a.tx_ids ?? []).map((t) => payments[t]).filter(Boolean)

  async function decide(kind: 'approve' | 'reject') {
    setBusy(kind); setError(null)
    try {
      await sendJSON(`/api/actions/${a.id}/${kind}`, { reason: reason.trim(), by: name.trim() || 'analyst' }, `${key.current}:${kind}`)
      onChange()
    } catch (e) { setError((e as Error).message); onChange() } finally { setBusy(null) }
  }

  return (
    <li className="rounded-lg p-3 flex flex-col gap-2" style={{ background: 'var(--surface-2)' }}>
      <div className="flex items-baseline justify-between gap-3">
        <span className="text-sm font-medium">
          {ACTION_PLAIN[a.type].title}{a.account ? ` account ${shortAcct(a.account)}` : ''}
        </span>
        {isHold && <span className="text-base font-semibold tabular" style={{ color: 'var(--s4)' }}>{m(a.amount_blocked)}</span>}
      </div>
      {isHold && (
        <p className="text-xs tabular" style={{ color: 'var(--ink-2)' }}>
          {(a.frozen_now ?? 0) > 0 && <>{m(a.frozen_now)} is in the account now. </>}
          {viaRoute > 0 && <>{m(viaRoute)} more would reach it on the routes this case used.</>}
        </p>
      )}
      {a.why && <p className="text-sm leading-snug">{a.why}</p>}
      {claims.length > 0 && (
        <ul className="flex flex-col gap-1 pl-3" style={{ borderLeft: '2px solid var(--critical)' }}>
          {claims.slice(0, 3).map((c, i) => <li key={i} className="text-xs" style={{ color: 'var(--ink-2)' }}>{c}</li>)}
        </ul>
      )}
      {into.length > 0 && (
        <ul className="flex flex-col gap-0.5 text-xs tabular" style={{ color: 'var(--ink-2)' }} aria-label="Case payments into this account">
          {into.slice(0, 3).map((e) => (
            <li key={e.tx_id}>Received {m(e.amount)} from account {shortAcct(e.src)} on {fmtTime(e.ts)}</li>
          ))}
          {into.length > 3 && <li>and {into.length - 3} more payments</li>}
        </ul>
      )}

      <Status a={a} now={now} />

      {a.status === 'PROPOSED' && (
        <div className="flex flex-col gap-2">
          <label className="sr-only" htmlFor={`reason-${a.id}`}>Reason for your decision</label>
          <textarea id={`reason-${a.id}`} value={reason} onChange={(e) => setReason(e.target.value)} rows={2}
            placeholder="Reason for your decision (kept in the audit log)"
            className="w-full rounded-md px-2 py-1.5 text-sm resize-y"
            style={{ background: 'var(--surface)', border: '1px solid var(--border)', color: 'var(--ink)' }} />
          <div className="flex flex-wrap items-center gap-2">
            <button onClick={() => decide('approve')} disabled={!!busy || reason.trim().length < MIN_REASON}
              className="px-3 py-1.5 rounded-md text-sm font-medium flex items-center gap-1.5 whitespace-nowrap disabled:opacity-50"
              style={{ background: 'var(--good)', color: '#fff' }}>
              {busy === 'approve' ? <Loader2 size={14} className="animate-spin" /> : <ShieldCheck size={14} aria-hidden />}
              {ACTION_PLAIN[a.type].approve}
            </button>
            <button onClick={() => decide('reject')} disabled={!!busy || reason.trim().length < MIN_REASON}
              className="px-3 py-1.5 rounded-md text-sm flex items-center gap-1.5 disabled:opacity-50"
              style={{ border: '1px solid var(--border)', color: 'var(--ink)' }}>
              {busy === 'reject' ? <Loader2 size={14} className="animate-spin" /> : <XCircle size={14} aria-hidden />}
              Reject
            </button>
          </div>
          {reason.trim().length < MIN_REASON && (
            <span className="text-xs" style={{ color: 'var(--muted)' }}>Write a reason to approve or reject.</span>
          )}
          {error && <p className="text-xs" style={{ color: 'var(--critical)' }}>{error}</p>}
        </div>
      )}
    </li>
  )
}

function Status({ a, now }: { a: Action; now: number }) {
  const muted = { color: 'var(--ink-2)' }
  if (a.status === 'PROPOSED') {
    return a.type === 'HOLD'
      ? <p className="text-xs" style={muted}>Proposed by the agent. Ends {a.expiry_days} business days after approval.</p>
      : <p className="text-xs" style={muted}>Proposed by the agent.</p>
  }
  if (a.status === 'APPROVED') {
    return (
      <div className="flex flex-col gap-0.5 text-xs">
        <span style={{ color: 'var(--good)' }}>Approved by {a.decided_by}: {a.reason}</span>
        {a.type === 'HOLD' && a.expires_ts && (
          <span className="tabular" style={muted}>
            Held until {untilLabel(a.expires_ts)}. <span className="font-semibold" style={{ color: 'var(--ink)' }}>{timeLeft(a.expires_ts, now)}</span>
          </span>
        )}
      </div>
    )
  }
  if (a.status === 'REJECTED') return <p className="text-xs" style={muted}>Rejected by {a.decided_by}: {a.reason}</p>
  return <p className="text-xs" style={muted}>{a.released_at ? `Released ${untilLabel(a.released_at)}.` : 'Withdrawn.'}</p>
}
