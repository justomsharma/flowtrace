import { useEffect, useState } from 'react'
import { Link, NavLink, useLocation } from 'react-router-dom'
import { FileText, LayoutDashboard, ListChecks, Lock, Orbit, Waypoints } from 'lucide-react'
import { getJSON, type CaseBrief, type Health } from '../lib/api'
import { signOut, useApprover } from '../lib/approver'
import ApproverSignIn from './ApproverSignIn'
import { useBatch } from '../lib/batch'
import { inToday, caseNo } from '../lib/plain'

const NAV = [
  { to: '/', label: 'Live Network', Icon: Orbit, keepCase: true },
  { to: '/today', label: "Today's work", Icon: ListChecks, keepCase: false },
  { to: '/case', label: 'Case overview', Icon: LayoutDashboard, keepCase: true },
  { to: '/results', label: 'Holds & audit', Icon: Lock, keepCase: false },
  { to: '/reports', label: 'Reports', Icon: FileText, keepCase: true },
]

/** Disposition colour of a case in the queue; status colours always come with a text label for screen readers. */
function dot(c: CaseBrief): { color: string; label: string; pulse?: boolean } {
  if (c.result) {
    const a = c.result.action
    return a === 'ESCALATE' ? { color: 'var(--critical)', label: 'Escalate' }
      : a === 'CLOSE' ? { color: 'var(--good)', label: 'Close' } : { color: 'var(--warning)', label: 'Review' }
  }
  if (c.job) return { color: 'var(--brand)', label: c.job.status === 'running' ? 'Investigating' : 'Queued', pulse: c.job.status === 'running' }
  if (c.failed) return { color: 'var(--muted)', label: 'Failed' }
  return { color: 'var(--axis)', label: 'Not investigated' }
}


export default function Sidebar() {
  const { batch, caseId, query } = useBatch()
  const onCase = useLocation().pathname === '/case'
  const top = (batch?.cases ?? []).filter((c, i) => inToday(c, i, batch?.capacity)).slice(0, 8)

  return (
    <aside className="w-[232px] shrink-0 h-screen sticky top-0 flex flex-col gap-6 px-3 py-5 overflow-y-auto"
      style={{ background: 'var(--surface)', borderRight: '1px solid var(--border)' }}>
      <div className="flex items-center gap-2.5 px-2">
        <span className="w-9 h-9 rounded-lg grid place-items-center" style={{ background: 'var(--brand)' }}>
          <Waypoints size={20} color="#fff" aria-hidden />
        </span>
        <span className="flex flex-col leading-tight">
          <span className="text-[17px] font-semibold">FlowTrace</span>
          <span className="text-xs" style={{ color: 'var(--muted)' }}>AML investigation agent</span>
        </span>
      </div>

      <nav aria-label="Main" className="flex flex-col gap-0.5">
        {NAV.map(({ to, label, Icon, keepCase }) => (
          <NavLink key={to} to={{ pathname: to, search: query(keepCase ? undefined : null) }} end
            className="flex items-center gap-3 px-3 py-2 rounded-lg text-sm font-medium"
            style={({ isActive }) => ({ background: isActive ? 'var(--brand-tint)' : 'transparent', color: isActive ? 'var(--brand)' : 'var(--ink-2)' })}>
            <Icon size={18} aria-hidden />{label}
          </NavLink>
        ))}
      </nav>

      {top.length > 0 && (
        <div className="flex flex-col gap-1">
          <h2 className="px-3 text-xs font-medium" style={{ color: 'var(--muted)' }}>Cases</h2>
          <ul className="flex flex-col gap-0.5">
            {top.map((c) => {
              const d = dot(c)
              const active = onCase && c.case_id === caseId
              return (
                <li key={c.case_id}>
                  <Link to={{ pathname: '/case', search: query(c.case_id) }} aria-current={active ? 'page' : undefined}
                    className="flex items-center gap-2.5 px-3 py-1.5 rounded-lg text-sm tabular"
                    style={{ background: active ? 'var(--surface-2)' : 'transparent', color: active ? 'var(--ink)' : 'var(--ink-2)', fontWeight: active ? 600 : 400 }}>
                    <span className={`w-2 h-2 rounded-full ${d.pulse ? 'animate-pulse' : ''}`} style={{ background: d.color }} aria-hidden />
                    Case {caseNo(c.case_id)}
                    <span className="sr-only">: {d.label}</span>
                    <span className="ml-auto text-xs" style={{ color: 'var(--muted)' }} aria-hidden>{d.label === 'Not investigated' ? '' : d.label}</span>
                  </Link>
                </li>
              )
            })}
          </ul>
        </div>
      )}

      <SystemStatus />
    </aside>
  )
}

function SystemStatus() {
  const approver = useApprover()
  const [signingIn, setSigningIn] = useState(false)
  const [h, setH] = useState<Health | null>(null)
  const [at, setAt] = useState<number | null>(null)
  useEffect(() => {
    const check = () => getJSON<Health>('/api/health').then(setH)
      .catch(() => setH({ api: false, graph: false, llm: false })).finally(() => setAt(Date.now()))
    check()
    const t = setInterval(check, 30000)
    return () => clearInterval(t)
  }, [])
  if (!h) return null
  const online = h.api && h.graph
  const row = (ok: boolean, label: string) => (
    <li className="flex items-center gap-2">
      <span className="w-1.5 h-1.5 rounded-full" style={{ background: ok ? 'var(--good)' : 'var(--critical)' }} aria-hidden />
      {label}
    </li>
  )
  return (
    <section aria-label="System status" className="mt-auto rounded-xl p-3 text-xs flex flex-col gap-2" style={{ background: 'var(--surface-2)' }}>
      <div className="flex items-center gap-2 text-sm font-semibold">
        <span className="w-2.5 h-2.5 rounded-full" style={{ background: online ? 'var(--good)' : 'var(--critical)' }} aria-hidden />
        {online ? 'System online' : h.api ? 'Graph database offline' : 'Server offline'}
      </div>
      <ul className="flex flex-col gap-1" style={{ color: 'var(--ink-2)' }}>
        {row(h.graph, h.graph ? 'Graph database (FalkorDB)' : 'Graph database not reachable')}
        {row(h.llm, h.llm ? `AI investigator${h.model ? ` (${h.model})` : ''}`
          : h.llm_budget?.over ? "Today's AI budget is used: rule-based investigator" : 'AI off: rule-based investigator')}
      </ul>
      {h.demo && <p style={{ color: 'var(--ink-2)' }}>Public demo: the bundled sample files only.</p>}
      {approver ? (
        <p style={{ color: 'var(--ink-2)' }}>
          Signed in as <b style={{ color: 'var(--ink)' }}>demo approver</b>{' '}
          <button onClick={signOut} className="underline underline-offset-2">sign out</button>
        </p>
      ) : signingIn ? <ApproverSignIn compact /> : (
        <p style={{ color: 'var(--ink-2)' }}>
          Viewing only{' '}
          <button onClick={() => setSigningIn(true)} className="underline underline-offset-2">sign in as approver</button>
        </p>
      )}
      {at && <p style={{ color: 'var(--muted)' }}>Checked {new Date(at).toLocaleTimeString('en-GB', { hour: '2-digit', minute: '2-digit' })}</p>}
    </section>
  )
}
