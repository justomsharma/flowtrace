import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { BarChart3, ChevronRight, Download, GitBranch, Lock, Maximize2, PieChart, ReceiptText, ShieldCheck } from 'lucide-react'
import HoldPanel from '../components/HoldPanel'
import FlowGraph, { FlowLegend } from '../components/case/FlowGraph'
import KpiCards from '../components/case/KpiCards'
import { DistributionBars, TimelineChart } from '../components/case/Charts'
import { Panel, Pill } from '../components/case/Panel'
import { PaymentSequence, PaymentsTable } from '../components/case/Payments'
import { InvestigationProgress, Stepper } from '../components/case/Progress'
import { AiSummary } from '../components/case/Summary'
import WhyCard from '../components/case/WhyCard'
import KeyFindings from '../components/case/KeyFindings'
import { NamesProvider } from '../lib/names'
import { getJSON, type Action, type CaseOverview as Overview, type HoldPlan } from '../lib/api'
import { useBatch } from '../lib/batch'
import { fmtMoney } from '../lib/money'
import { caseNo, caseSentence, friendlyError, shortText, trickName } from '../lib/plain'
import { caseSteps } from '../lib/stepper'
import { useInvestigation } from '../lib/useInvestigation'

const PRIORITY = {
  High: { bg: 'var(--flag-tint)', fg: 'var(--critical)' },
  Medium: { bg: 'var(--hold-tint)', fg: '#94620a' },
  Low: { bg: 'var(--surface-2)', fg: 'var(--ink-2)' },
}

export default function CaseOverview() {
  const { batch, batchId, caseId, ready, query } = useBatch()
  const navigate = useNavigate()

  // No case chosen: open the top case of the current batch.
  useEffect(() => {
    if (!caseId && batch?.cases.length) navigate({ pathname: '/case', search: query(batch.cases[0].case_id) }, { replace: true })
  }, [caseId, batch, navigate, query])

  if (!caseId) {
    if (!ready || batch?.cases.length) return null
    return (
      <div className="px-6 py-10 max-w-xl">
        <h1 className="text-2xl font-semibold">No case open</h1>
        <p className="text-sm mt-2" style={{ color: 'var(--ink-2)' }}>
          {batchId ? 'This upload has no cases.' : 'Nothing uploaded yet.'}{' '}
          <Link to="/today" className="font-medium underline" style={{ color: 'var(--brand)' }}>Go to Today's work</Link> to upload payments.
        </p>
      </div>
    )
  }
  return <CasePage key={caseId} caseId={caseId} />
}

function CasePage({ caseId }: { caseId: string }) {
  const { query, refresh: refreshBatch } = useBatch()
  const [o, setO] = useState<Overview | null>(null)
  const [missing, setMissing] = useState(false)
  const [view, setView] = useState<'graph' | 'list'>('graph')
  // Two columns end level. The left column takes the right column's height, but never less than the flow
  // graph plus a 260px payments box. The graph takes spare room (centred); the payments table gives room
  // back by scrolling inside. Natural single-column layout below 1180px.
  const [leftH, setLeftH] = useState<number | null>(null)
  // When the graph is short, the charts move up into the left column instead of leaving it empty.
  const [chartsLeft, setChartsLeft] = useState(false)
  const right = useRef<HTMLDivElement | null>(null)
  const graphBox = useRef<HTMLDivElement | null>(null)
  const measure = useCallback(() => {
    const r = right.current, box = graphBox.current, g = box?.firstElementChild as HTMLElement | null
    const panel = box?.closest('section')
    if (!r || !box || !g || !panel || window.innerWidth < 1180) { setLeftH(null); setChartsLeft(false); return }
    const flowNatural = panel.offsetHeight - box.offsetHeight + g.offsetHeight
    const CHARTS = 380
    const up = r.offsetHeight >= flowNatural + 20 + CHARTS + 20 + 260
    setChartsLeft(up)
    setLeftH(Math.max(r.offsetHeight, flowNatural + 20 + 260 + (up ? CHARTS + 20 : 0)))
  }, [])
  const watch = useRef<ResizeObserver | null>(null)
  const observe = useCallback(() => {
    watch.current?.disconnect()
    watch.current = new ResizeObserver(measure)
    if (right.current) watch.current.observe(right.current)
    const g = graphBox.current?.firstElementChild
    if (g) watch.current.observe(g)
    measure()
  }, [measure])
  const rightCol = useCallback((el: HTMLDivElement | null) => { right.current = el; observe() }, [observe])
  const graphCol = useCallback((el: HTMLDivElement | null) => { graphBox.current = el; observe() }, [observe])
  const [hold, setHold] = useState<{ plan: HoldPlan | null; actions: Action[] }>({ plan: null, actions: [] })
  const [planVersion, setPlanVersion] = useState(0)

  const [loadError, setLoadError] = useState<string | null>(null)
  const load = useCallback(() => {
    getJSON<Overview>(`/api/cases/${caseId}/overview`).then((x) => { setO(x); setMissing(false); setLoadError(null) })
      .catch((e) => { if (String(e).includes(': 404')) setMissing(true); else setLoadError(friendlyError(e)) })
  }, [caseId])
  useEffect(() => { load() }, [load])

  const inv = useInvestigation(caseId, o?.status.job?.id ?? null)

  // Keep the page current while its investigation is queued or running; the stream ending reloads it at once.
  // Until the first load succeeds it retries too.
  const state = o?.status.state
  useEffect(() => {
    if (state && !['queued', 'investigating', 'not_started'].includes(state)) return
    const t = setInterval(load, state === 'not_started' ? 10000 : 4000)
    return () => clearInterval(t)
  }, [state, load])
  // The page can learn of a run (or its end) from polling alone: its stream dropped, or it ran from another tab.
  const jobId = o?.status.job?.id
  const finished = !!o?.summary && !o?.status.job
  const { live, join, reload } = inv
  useEffect(() => { if (jobId && !live) join(jobId) }, [jobId, live, join])
  useEffect(() => { if (finished && !live) reload() }, [finished, live, reload])
  useEffect(() => { if (inv.ended) { load(); refreshBatch(); setPlanVersion((v) => v + 1) } }, [inv.ended, load, refreshBatch])

  const onPlan = useCallback((plan: HoldPlan | null, actions: Action[]) => { setHold({ plan, actions }); load() }, [load])
  const investigate = useCallback(() => { inv.investigate().then(load) }, [inv, load])

  const steps = useMemo(() => caseSteps({
    status: inv.live && o?.status.state !== 'queued' ? 'investigating' : o?.status.state ?? 'not_started',
    hasResult: !!inv.result, actions: hold.actions, liveSteps: inv.steps.length,
  }), [o, inv.live, inv.result, inv.steps.length, hold.actions])

  if (missing) {
    return (
      <div className="px-6 py-10 max-w-xl">
        <h1 className="text-2xl font-semibold">Case {caseNo(caseId)} is not available</h1>
        <p className="text-sm mt-2" style={{ color: 'var(--ink-2)' }}>
          The file was probably uploaded again, which renumbers cases.{' '}
          <Link to={{ pathname: '/today', search: query(null) }} className="font-medium underline" style={{ color: 'var(--brand)' }}>Open Today's work</Link> to pick a case.
        </p>
      </div>
    )
  }
  if (!o) {
    return (
      <div className="px-6 py-10 text-sm" style={{ color: loadError ? 'var(--critical)' : 'var(--ink-2)' }} aria-live="polite">
        {loadError ? 'Could not load the case. Trying again…' : 'Loading the case…'}
      </div>
    )
  }

  const investigated = !!o.summary
  const proposed = hold.actions.filter((a) => a.type === 'HOLD' && a.status === 'PROPOSED')
  const proposedSum = proposed.reduce((s, a) => s + (a.hold_amount ?? 0), 0)
  const p = PRIORITY[o.priority]
  const writing = inv.live && inv.steps.length > 0 && inv.steps.every((s) => s.done)

  const charts = (
        <div className="grid gap-5 shrink-0 lg:grid-cols-[minmax(0,1.35fr)_minmax(0,1fr)]">
          <Panel title="When the money moved" icon={BarChart3}>
            <TimelineChart o={o} />
          </Panel>
          <Panel title="Who received it" icon={PieChart}>
            <DistributionBars o={o} />
          </Panel>
        </div>
  )

  return (
    <NamesProvider names={o.names ?? {}}>
    <div className="px-6 py-5 flex flex-col gap-5">
      <nav aria-label="Breadcrumb" className="flex items-center gap-1.5 text-sm" style={{ color: 'var(--muted)' }}>
        <Link to={{ pathname: '/today', search: query(null) }} className="hover:underline">Today's work</Link>
        <ChevronRight size={14} aria-hidden />
        <span style={{ color: 'var(--ink-2)' }} aria-current="page">Case {caseNo(o.case_id)}</span>
      </nav>

      <header className="flex flex-wrap items-start justify-between gap-4 -mt-2">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-3">
            <h1 className="text-[28px] font-semibold leading-tight">
              <span style={{ color: 'var(--muted)' }}>Case {caseNo(o.case_id)} ·</span> {trickName(o.typology ?? undefined)}
            </h1>
            <Pill bg={p.bg} fg={p.fg}>{o.priority} priority</Pill>
            {o.tier === 'lower' && <Pill bg="var(--surface-2)" fg="var(--ink-2)">Not in today's list</Pill>}
          </div>
          <div className="mt-1.5 max-w-4xl flex flex-col gap-0.5 text-[15px]" style={{ color: 'var(--ink-2)' }}>
            {(o.story?.length ? o.story : [caseSentence(o.typology, fmtMoney(o.kpis.amount), o.kpis.accounts)]).map((t, i) => <p key={i}>{t}</p>)}
          </div>
        </div>
        {investigated ? (
          <a href={`/api/cases/${o.case_id}/report.md`} download
            className="inline-flex items-center gap-2 px-4 py-2.5 rounded-lg text-sm font-semibold shadow-sm"
            style={{ background: 'var(--brand)', color: 'var(--brand-ink)' }}>
            <Download size={16} aria-hidden /> Download report
          </a>
        ) : (
          <span className="flex flex-col items-end gap-1">
            <button disabled aria-describedby="report-why" className="inline-flex items-center gap-2 px-4 py-2.5 rounded-lg text-sm font-semibold opacity-50 cursor-not-allowed"
              style={{ background: 'var(--brand)', color: 'var(--brand-ink)' }}>
              <Download size={16} aria-hidden /> Download report
            </button>
            <span id="report-why" className="text-xs" style={{ color: 'var(--muted)' }}>After the investigation</span>
          </span>
        )}
      </header>

      {proposed.length > 0 && (
        <a href="#next-step" className="rounded-xl px-4 py-3 flex flex-wrap items-center gap-3 text-sm"
          style={{ background: 'var(--hold-tint)', border: '1px solid color-mix(in srgb, var(--hold) 45%, transparent)' }}>
          <Lock size={18} style={{ color: 'var(--hold)' }} aria-hidden />
          <span className="font-semibold">
            {proposed.length} hold{proposed.length === 1 ? '' : 's'} proposed: {fmtMoney(proposedSum)}. Nothing is frozen until you approve.
          </span>
          <span className="ml-auto font-semibold" style={{ color: '#94620a' }}>Review</span>
        </a>
      )}

      <WhyCard o={o} onInvestigate={investigate} />
      <KpiCards o={o} />

      <div className="grid gap-5 min-[1180px]:grid-cols-[minmax(0,1fr)_420px] items-start">
        <div className="flex flex-col gap-5 min-w-0" style={leftH ? { height: leftH } : undefined}>
          <Panel title="Money flow" icon={GitBranch} className="grow shrink-0"
            sub={view === 'graph' ? 'Who paid whom, left to right. Point at anything for details.' : 'Every payment, in time order'}
            right={
              <div className="flex items-center gap-2">
                <div role="group" aria-label="View" className="flex rounded-lg p-0.5" style={{ background: 'var(--surface-2)' }}>
                  {(['graph', 'list'] as const).map((v) => (
                    <button key={v} onClick={() => setView(v)} aria-pressed={view === v}
                      className="px-3 py-1.5 rounded-md text-sm font-medium"
                      style={view === v ? { background: 'var(--brand)', color: 'var(--brand-ink)' } : { color: 'var(--ink-2)' }}>
                      {v === 'graph' ? 'Graph' : 'List'}
                    </button>
                  ))}
                </div>
                <Link to={{ pathname: '/', search: query(o.case_id) }}
                  className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-sm font-medium"
                  style={{ border: '1px solid var(--border)', color: 'var(--ink)' }}>
                  <Maximize2 size={14} aria-hidden /> 3D view
                </Link>
              </div>
            }>
            {view === 'graph' ? (
              <>
                <div ref={graphCol} className="flex-1 flex items-center">
                  <FlowGraph nodes={o.flow.nodes} edges={o.flow.edges} currency="US Dollar" rate={1} />
                </div>
                <FlowLegend />
                {o.flow.hidden_accounts > 0 && (
                  <p className="text-xs" style={{ color: 'var(--muted)' }}>
                    {o.flow.hidden_accounts} smaller account{o.flow.hidden_accounts === 1 ? '' : 's'} hidden. See them in the 3D view.
                  </p>
                )}
              </>
            ) : <PaymentSequence payments={o.transactions} currency="US Dollar" rate={1} />}
          </Panel>
          {chartsLeft && charts}
          <Panel title="Payments" icon={ReceiptText} sub={`${o.transactions.length} payments, newest first`} className="min-h-[260px] shrink">
            <PaymentsTable payments={o.transactions} currency="US Dollar" rate={1} fill />
          </Panel>
        </div>
        <div ref={rightCol} className="flex flex-col gap-5 min-w-0">
          <KeyFindings o={o} />
          <AiSummary summary={o.summary} live={inv.live} />
          <InvestigationProgress live={inv.live} liveSteps={inv.steps} stored={o.steps} result={inv.result} error={inv.error} writing={writing}
            briefs={Object.fromEntries([...o.findings, ...o.explanations].map((f) => [f.claim, f.brief]))} />
        </div>
      </div>

      {!chartsLeft && charts}

      <section id="next-step" aria-labelledby="next-step-title" className="card p-5 flex flex-col gap-4 scroll-mt-4"
        style={{ borderColor: 'color-mix(in srgb, var(--hold) 55%, var(--border))', boxShadow: 'inset 0 4px 0 var(--hold)' }}>
        <header className="flex items-start gap-3">
          <span className="w-9 h-9 rounded-lg grid place-items-center shrink-0" style={{ background: 'var(--hold-tint)' }}>
            <ShieldCheck size={18} style={{ color: 'var(--hold)' }} aria-hidden />
          </span>
          <div>
            <h2 id="next-step-title" className="text-[17px] font-semibold leading-tight">Next step</h2>
            <p className="text-sm mt-0.5" style={{ color: 'var(--ink-2)' }}>The agent proposes. You approve or reject.</p>
          </div>
        </header>
        {inv.result ? (
          <HoldPanel key={inv.result.case_id} caseId={inv.result.case_id} result={inv.result} refreshKey={planVersion} onPlan={onPlan}
            briefs={Object.fromEntries([...o.findings, ...o.explanations].map((f) => [f.id, f.brief]))} />
        ) : (
          <p className="text-sm" style={{ color: 'var(--ink-2)' }}>
            {inv.live ? 'Proposals appear when the investigation ends.' : 'Investigate the case first.'}
          </p>
        )}
        {o.next_steps.length > 0 && (
          <div>
            <h3 className="text-sm font-semibold mb-1">Also suggested</h3>
            <ul className="list-disc pl-5 flex flex-col gap-1 text-sm" style={{ color: 'var(--ink-2)' }}>
              {o.next_steps.slice(0, 3).map((s, i) => <li key={i}>{shortText(s)}</li>)}
            </ul>
          </div>
        )}
      </section>

      <Stepper steps={steps} />
    </div>
    </NamesProvider>
  )
}
