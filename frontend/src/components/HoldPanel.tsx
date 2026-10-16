import { useCallback, useEffect, useRef, useState } from 'react'
import { Loader2, Lock, ShieldCheck, XCircle } from 'lucide-react'
import { approverToken, fmtTime, getJSON, sendJSON, type Action, type CaseResult, type HoldPlan, type OpenEffect } from '../lib/api'
import { currencyNote, fmtMoney } from '../lib/money'
import { ACTION_PLAIN, friendlyError } from '../lib/plain'
import { useName } from '../lib/names'
import { useNow, timeLeft, untilLabel } from '../lib/time'

const MIN_REASON = 5
const NAME_KEY = 'flowtrace.analyst'

function readName(): string {
  try { return localStorage.getItem(NAME_KEY) ?? '' } catch { return '' }
}

const ORIGIN: Record<Action['proposed_by'], string> = {
  agent: 'Proposed by the FlowTrace agent',
  mcp: 'Proposed by an outside AI client (MCP)',
}

/** The agent's proposed holds and case decision for one case. Nothing happens until a person approves.
 *  Mount it with key={caseId}: each case starts from a fresh panel. */
export default function HoldPanel({ caseId, result, refreshKey, onPlan, briefs = {} }: {
  caseId: string
  result: CaseResult
  refreshKey: number
  onPlan: (plan: HoldPlan | null, actions: Action[]) => void
  /** evidence id -> short reason line, from the case overview */
  briefs?: Record<string, string>
}) {
  const [plan, setPlan] = useState<HoldPlan | null>(null)
  const [actions, setActions] = useState<Action[]>([])
  const [effect, setEffect] = useState<OpenEffect | null>(null)
  const [loaded, setLoaded] = useState(false)
  const [planning, setPlanning] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [name, setName] = useState(readName)
  const planKey = useRef(crypto.randomUUID())
  // A reply that lands after the analyst opened another case must not paint that case's map.
  const alive = useRef(true)
  useEffect(() => { alive.current = true; return () => { alive.current = false } }, [])

  const load = useCallback(() => {
    getJSON<{ plan: HoldPlan | null; actions: Action[]; open_effect: OpenEffect | null }>(`/api/cases/${caseId}/plan`)
      .then((r) => {
        if (!alive.current) return
        setPlan(r.plan); setActions(r.actions); setEffect(r.open_effect); setLoaded(true); onPlan(r.plan, r.actions)
      })
      .catch((e) => { if (alive.current) { setError(friendlyError(e)); setLoaded(true) } })
  }, [caseId, onPlan])

  useEffect(() => { load() }, [load, refreshKey])

  async function planNow() {
    setPlanning(true); setError(null)
    try {
      const r = await sendJSON<{ plan: HoldPlan; actions: Action[] }>(`/api/cases/${caseId}/plan`, undefined, { key: planKey.current })
      if (!alive.current) return
      setPlan(r.plan); onPlan(r.plan, r.actions); load()
    } catch (e) { setError(friendlyError(e)) } finally { setPlanning(false) }
  }

  const saveName = (v: string) => { setName(v); try { localStorage.setItem(NAME_KEY, v) } catch { /* private window */ } }
  const holds = actions.filter((a) => a.type === 'HOLD' || a.type === 'RELEASE')
    .sort((x, y) => (y.hold_amount ?? 0) - (x.hold_amount ?? 0))
  const decisions = actions.filter((a) => a.type !== 'HOLD' && a.type !== 'RELEASE')
  const evidence = Object.fromEntries(result.evidence.map((e) => [e.id, briefs[e.id] ?? e.claim]))
  const payments = Object.fromEntries((plan?.network.edges ?? []).map((e) => [e.tx_id, e]))
  const deciding = actions.some((a) => a.status === 'PROPOSED')
  // Every open proposal starts selected; the analyst unticks what they disagree with.
  const [unpicked, setUnpicked] = useState<Set<string>>(new Set())
  const picked = new Set(actions.filter((a) => a.status === 'PROPOSED' && !unpicked.has(a.id)).map((a) => a.id))
  const toggle = (id: string) => setUnpicked((u) => { const n = new Set(u); if (n.has(id)) n.delete(id); else n.add(id); return n })

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

      {plan && <Effect plan={plan} effect={effect}
        open={holds.filter((a) => a.type === 'HOLD' && (a.status === 'PROPOSED' || a.status === 'APPROVED')).length} />}

      {holds.length > 0 && (
        <div className="flex flex-col gap-1.5">
          <h4 className="text-xs font-semibold" style={{ color: 'var(--ink-2)' }}>Holds</h4>
          <ul className="flex flex-col gap-1.5">
            {holds.map((a) => <ActionRow key={a.id} action={a} evidence={evidence} payments={payments}
              picked={picked.has(a.id)} onPick={() => toggle(a.id)} />)}
          </ul>
        </div>
      )}

      {decisions.length > 0 && (
        <div className="flex flex-col gap-1.5">
          <h4 className="text-xs font-semibold" style={{ color: 'var(--ink-2)' }}>Case decision</h4>
          <ul className="flex flex-col gap-1.5">
            {decisions.map((a) => <ActionRow key={a.id} action={a} evidence={evidence} payments={payments}
              picked={picked.has(a.id)} onPick={() => toggle(a.id)} />)}
          </ul>
        </div>
      )}

      {deciding && (
        <DecisionBar selected={actions.filter((a) => a.status === 'PROPOSED' && picked.has(a.id))}
          name={name} onName={saveName} onDone={load} />
      )}
      {error && <p className="text-xs" style={{ color: 'var(--critical)' }}>{error}</p>}
    </section>
  )
}

/** "Holding 2 accounts freezes X now": only the case money that is really in the held accounts. Money on the
 *  case's routes is an assumption and is said apart, in words, never added to the frozen amount. */
function Effect({ plan, effect, open }: { plan: HoldPlan; effect: OpenEffect | null; open: number }) {
  const m = (v: number) => fmtMoney(v, plan.currency, plan.rate)
  const frozen = Math.min(effect?.frozen ?? 0, plan.in_motion)
  const notHeld = Math.max(plan.in_motion - frozen, 0)
  const total = plan.in_motion + plan.already_out
  let line: string
  if (open > 0) {
    line = `${open} hold${open === 1 ? '' : 's'} would freeze ${m(frozen)} of case money.`
  } else if (plan.holds.length > 0) {
    line = `Nothing is frozen yet. ${m(plan.in_motion)} of case money can still be stopped.`
  } else if (plan.in_motion <= 0) {
    line = 'The case money has already moved on, so there is nothing to hold.'
  } else {
    line = `${m(plan.in_motion)} of case money is still moving, but none of it can be held here.`
  }
  const seg = [
    { v: frozen, c: 'var(--s4)', label: 'Frozen now' },
    { v: notHeld, c: 'var(--axis)', label: 'Still moving, not held' },
    { v: plan.already_out, c: 'var(--s8)', label: 'Already gone (cash, crypto, shops)' },
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
      <p className="text-xs" style={{ color: 'var(--muted)' }}>
        Only the case money is frozen, never the whole account.
        {plan.skipped_hubs.length > 0 && ` ${plan.skipped_hubs.length} busy account${plan.skipped_hubs.length === 1 ? ' is' : 's are'} never held (shops, processors).`}
        {' '}{currencyNote()}
      </p>
    </div>
  )
}

function ActionRow({ action: a, evidence, payments, picked, onPick }: {
  action: Action
  evidence: Record<string, string>
  payments: Record<string, HoldPlan['network']['edges'][number]>
  picked: boolean
  onPick: () => void
}) {
  const now = useNow(a.status === 'APPROVED' ? 1000 : 0)
  const m = (v = 0) => fmtMoney(v, a.currency, a.rate)
  const acctName = useName()
  const isHold = a.type === 'HOLD'
  const proposed = a.status === 'PROPOSED'
  const firstSentence = (t: string) => t.split(/(?<=\.)\s/)[0]
  const claims = (a.evidence_ids ?? []).map((id) => evidence[id]).filter(Boolean)
  const into = (a.tx_ids ?? []).map((t) => payments[t]).filter(Boolean)
  const title = isHold ? `Freeze ${m(a.hold_amount)} in ${a.account ? acctName(a.account) : 'the account'}`
    : `${ACTION_PLAIN[a.type].title}${a.account ? ` ${acctName(a.account)}` : ''}`

  return (
    <li className="rounded-lg px-3 py-2.5 flex items-start gap-2.5" style={{ background: 'var(--surface-2)' }}>
      {proposed ? (
        <input type="checkbox" checked={picked} onChange={onPick} aria-label={`Select: ${title}`}
          className="mt-0.5 w-4 h-4 shrink-0 accent-[var(--brand)]" />
      ) : <span className="w-4 shrink-0" aria-hidden />}
      <div className="min-w-0 flex-1 flex flex-col gap-1">
        <div className="text-sm font-semibold">{title}</div>
        <dl className="grid grid-cols-[96px_1fr] gap-x-2 gap-y-0.5 text-xs leading-snug">
          {a.why && (<><dt className="font-semibold" style={{ color: 'var(--muted)' }}>{isHold ? 'Basis for hold' : 'Basis'}</dt><dd>{firstSentence(a.why)}</dd></>)}
          {claims.length > 0 && (<><dt className="font-semibold" style={{ color: 'var(--muted)' }}>Indicators</dt>
            <dd><ul className="flex flex-col gap-0.5">{claims.slice(0, 2).map((c, i) => <li key={i}>{c}</li>)}</ul></dd></>)}
          {into.length > 0 && (<><dt className="font-semibold" style={{ color: 'var(--muted)' }}>Funds received</dt>
            <dd className="tabular">
              {into.slice(0, 2).map((e) => `${m(e.amount)} from ${acctName(e.src)}, ${fmtTime(e.ts)}`).join('; ')}
              {into.length > 2 && <span style={{ color: 'var(--muted)' }}> and {into.length - 2} more</span>}
            </dd></>)}
        </dl>
        {!proposed && <Status a={a} now={now} />}
      </div>
    </li>
  )
}

/** One name, one reason, one click for every ticked proposal. Each decision is still logged on its own. */
function DecisionBar({ selected, name, onName, onDone }: {
  selected: Action[]
  name: string
  onName: (v: string) => void
  onDone: () => void
}) {
  const [reason, setReason] = useState('')
  const [editing, setEditing] = useState(!name.trim())
  const [busy, setBusy] = useState<'approve' | 'reject' | null>(null)
  const [error, setError] = useState<string | null>(null)
  const keys = useRef(new Map<string, string>())  // one key per decision: a double click returns the first answer
  const key = (id: string) => {
    if (!keys.current.has(id)) keys.current.set(id, crypto.randomUUID())
    return keys.current.get(id)!
  }
  const n = selected.length
  const ready = n > 0 && reason.trim().length >= MIN_REASON && !!name.trim() && !!approverToken

  async function decide(kind: 'approve' | 'reject') {
    setBusy(kind); setError(null)
    try {
      for (const a of selected) {
        await sendJSON(`/api/actions/${a.id}/${kind}`, { reason: reason.trim(), by: name.trim() },
          { key: `${key(a.id)}:${kind}`, approverToken })
      }
      setReason('')
    } catch (e) { setError(friendlyError(e)) } finally { setBusy(null); onDone() }
  }

  const hint = !approverToken ? 'Approvals are off: set VITE_APPROVER_TOKEN in frontend/.env.development.local.'
    : n === 0 ? 'Tick at least one proposal.'
    : !name.trim() ? 'Enter your name and a reason.'
    : reason.trim().length < MIN_REASON ? `Enter a reason (at least ${MIN_REASON} characters).` : ''

  return (
    <div className="flex flex-col gap-2 rounded-lg p-3" style={{ background: 'var(--surface-2)' }}>
      <div className="grid gap-2 md:grid-cols-[200px_minmax(0,1fr)_auto] md:items-center">
        {editing ? (
          <input value={name} onChange={(e) => onName(e.target.value)} onBlur={() => { if (name.trim()) setEditing(false) }}
            placeholder="Your name (asked once)" aria-label="Your name"
            className="rounded-md px-2 py-1.5 text-sm"
            style={{ background: 'var(--surface)', border: `1px solid ${name.trim() ? 'var(--border)' : 'var(--s4)'}`, color: 'var(--ink)' }} />
        ) : (
          <span className="text-xs" style={{ color: 'var(--ink-2)' }}>
            Deciding as <b style={{ color: 'var(--ink)' }}>{name}</b>{' '}
            <button onClick={() => setEditing(true)} className="underline underline-offset-2">change</button>
          </span>
        )}
        <label className="sr-only" htmlFor="decision-reason">Reason for your decision</label>
        <input id="decision-reason" value={reason} onChange={(e) => setReason(e.target.value)}
          placeholder="Reason for your decision (kept in the audit log)"
          className="rounded-md px-2 py-1.5 text-sm min-w-0"
          style={{ background: 'var(--surface)', border: '1px solid var(--border)', color: 'var(--ink)' }} />
        <div className="flex items-center gap-2">
          <button onClick={() => decide('approve')} disabled={!!busy || !ready}
            className="px-3 py-1.5 rounded-md text-sm font-medium flex items-center gap-1.5 whitespace-nowrap disabled:opacity-50"
            style={{ background: 'var(--good)', color: '#fff' }}>
            {busy === 'approve' ? <Loader2 size={14} className="animate-spin" /> : <ShieldCheck size={14} aria-hidden />}
            {n === 1 ? ACTION_PLAIN[selected[0].type].approve : `Approve ${n} selected`}
          </button>
          <button onClick={() => decide('reject')} disabled={!!busy || !ready}
            className="px-3 py-1.5 rounded-md text-sm flex items-center gap-1.5 whitespace-nowrap disabled:opacity-50"
            style={{ border: '1px solid var(--border)', color: 'var(--ink)', background: 'var(--surface)' }}>
            {busy === 'reject' ? <Loader2 size={14} className="animate-spin" /> : <XCircle size={14} aria-hidden />}
            {n === 1 ? 'Reject' : `Reject ${n} selected`}
          </button>
        </div>
      </div>
      {hint && <span className="text-xs" style={{ color: 'var(--muted)' }}>{hint}</span>}
      {error && <p className="text-xs" style={{ color: 'var(--critical)' }}>{error}</p>}
    </div>
  )
}

function Status({ a, now }: { a: Action; now: number }) {
  const muted = { color: 'var(--ink-2)' }
  const origin = ORIGIN[a.proposed_by] ?? ORIGIN.agent
  if (a.status === 'PROPOSED') return <p className="text-xs" style={muted}>{origin}.</p>
  if (a.status === 'APPROVED') {
    return (
      <div className="flex flex-col gap-0.5 text-xs">
        <span style={muted}>{origin}.</span>
        <span style={{ color: 'var(--good)' }}>Approved by {a.decided_by}: {a.reason}</span>
        {a.type === 'HOLD' && a.expires_ts && (
          <span className="tabular" style={muted}>
            Held until {untilLabel(a.expires_ts)}. <span className="font-semibold" style={{ color: 'var(--ink)' }}>{timeLeft(a.expires_ts, now)}</span>
          </span>
        )}
      </div>
    )
  }
  if (a.status === 'REJECTED') return <p className="text-xs" style={muted}>{origin}. Rejected by {a.decided_by}: {a.reason}</p>
  return <p className="text-xs" style={muted}>{a.released_at ? `Released ${untilLabel(a.released_at)}.` : 'Withdrawn.'}</p>
}
