import { useCallback, useEffect, useMemo, useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { BarChart3, ChevronRight, Download, GitBranch, Lock, Maximize2, PieChart, ReceiptText, ShieldCheck } from 'lucide-react'
import HoldPanel from '../components/HoldPanel'
import Findings from '../components/case/Findings'
import FlowGraph, { FlowLegend } from '../components/case/FlowGraph'
import KpiCards from '../components/case/KpiCards'
import { DistributionDonut, TimelineChart } from '../components/case/Charts'
import { Panel, Pill } from '../components/case/Panel'
import { PaymentSequence, PaymentsTable } from '../components/case/Payments'
import { InvestigationProgress, Stepper } from '../components/case/Progress'
import { AiSummary, RiskIndicators } from '../components/case/Summary'
import { getJSON, type Action, type CaseOverview as Overview, type HoldPlan } from '../lib/api'
import { useBatch } from '../lib/batch'
import { fmtMoney } from '../lib/money'
import { TRICKS, caseSentence, trickName } from '../lib/plain'
import { caseSteps } from '../lib/stepper'
import { useInvestigation } from '../lib/useInvestigation'

const caseNo = (id: string) => id.slice(id.lastIndexOf('-') + 1)
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
          {batchId ? 'This upload has no cases.' : 'Nothing has been uploaded yet.'}{' '}
          <Link to="/" className="font-medium underline" style={{ color: 'var(--brand)' }}>Go to Today's work</Link> to upload a day of payments.
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
  const [view, setView] = useState<'graph' | 'timeline'>('graph')
  const [hold, setHold] = useState<{ plan: HoldPlan | null; actions: Action[] }>({ plan: null, actions: [] })
  const [planVersion, setPlanVersion] = useState(0)

  const [loadError, setLoadError] = useState<string | null>(null)
  const load = useCallback(() => {
    getJSON<Overview>(`/api/cases/${caseId}/overview`).then((x) => { setO(x); setMissing(false); setLoadError(null) })
      .catch((e) => { if (String(e).includes(': 404')) setMissing(true); else setLoadError(String(e)) })
  }, [caseId])
  useEffect(() => { load() }, [load])

  const inv = useInvestigation(caseId, o?.status.job?.id ?? null)

  // Keep the page current while its investigation is queued or running; the stream ending reloads it at once.
  const state = o?.status.state
  // Until the first load succeeds it retries too.
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
          Its file was probably uploaded again, which renumbers the cases. <Link to={{ pathname: '/', search: query(null) }} className="font-medium underline" style={{ color: 'var(--brand)' }}>Open Today's work</Link> to pick a case.
        </p>
      </div>
    )
  }
  if (!o) {
    return (
      <div className="px-6 py-10 text-sm" style={{ color: loadError ? 'var(--critical)' : 'var(--ink-2)' }} aria-live="polite">
        {loadError ? `Could not load the case (${loadError}). Trying again…` : 'Loading the case…'}
      </div>
    )
  }

  const m = (v: number) => fmtMoney(v, o.currency, o.rate)
  const investigated = !!o.summary
  const proposed = hold.actions.filter((a) => a.type === 'HOLD' && a.status === 'PROPOSED')
  const proposedSum = proposed.reduce((s, a) => s + (a.hold_amount ?? 0), 0)
  const p = PRIORITY[o.priority]
  const writing = inv.live && inv.steps.length > 0 && inv.steps.every((s) => s.done)

  return (
    <div className="px-6 py-5 flex flex-col gap-5 max-w-[1560px]">
      <nav aria-label="Breadcrumb" className="flex items-center gap-1.5 text-sm" style={{ color: 'var(--muted)' }}>
        <Link to={{ pathname: '/', search: query(null) }} className="hover:underline">Today's work</Link>
        <ChevronRight size={14} aria-hidden />
        <span className="tabular" style={{ color: 'var(--ink-2)' }} aria-current="page">{o.case_id}</span>
      </nav>

      <header className="flex flex-wrap items-start justify-between gap-4 -mt-2">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-3">
            <h1 className="text-[28px] font-semibold leading-tight">
              <span className="tabular" style={{ color: 'var(--muted)' }}>{caseNo(o.case_id)}</span>{' '}
              {trickName(o.typology ?? undefined)}
            </h1>
            <Pill bg={p.bg} fg={p.fg}>{o.priority} priority</Pill>
            {o.tier === 'lower' && <Pill bg="var(--surface-2)" fg="var(--ink-2)">Beyond today's capacity</Pill>}
          </div>
          <p className="text-[15px] mt-1" style={{ color: 'var(--ink-2)' }}>
            {caseSentence(o.typology, m(o.kpis.amount), o.kpis.accounts)}. {o.typology && TRICKS[o.typology] ? TRICKS[o.typology].short : ''}
          </p>
        </div>
        {investigated ? (
          <a href={`/api/cases/${o.case_id}/report.md`} download
            className="inline-flex items-center gap-2 px-4 py-2.5 rounded-lg text-sm font-semibold shadow-sm"
            style={{ background: 'var(--brand)', color: 'var(--brand-ink)' }}>
            <Download size={16} aria-hidden /> Generate report
          </a>
        ) : (
          <span className="flex flex-col items-end gap-1">
            <button disabled aria-describedby="report-why" className="inline-flex items-center gap-2 px-4 py-2.5 rounded-lg text-sm font-semibold opacity-50 cursor-not-allowed"
              style={{ background: 'var(--brand)', color: 'var(--brand-ink)' }}>
              <Download size={16} aria-hidden /> Generate report
            </button>
            <span id="report-why" className="text-xs" style={{ color: 'var(--muted)' }}>Available once the case is investigated</span>
          </span>
        )}
      </header>

      {proposed.length > 0 && (
        <a href="#next-step" className="rounded-xl px-4 py-3 flex flex-wrap items-center gap-3 text-sm"
          style={{ background: 'var(--hold-tint)', border: '1px solid color-mix(in srgb, var(--hold) 45%, transparent)' }}>
          <Lock size={18} style={{ color: 'var(--hold)' }} aria-hidden />
          <span>
            <span className="font-semibold">The agent proposes {proposed.length} hold{proposed.length === 1 ? '' : 's'} freezing {fmtMoney(proposedSum, proposed[0].currency, proposed[0].rate)}.</span>{' '}
            Nothing is frozen until a person approves.
          </span>
          <span className="ml-auto font-semibold" style={{ color: '#94620a' }}>Review the holds</span>
        </a>
      )}

      <KpiCards o={o} onInvestigate={investigate} />

      <div className="grid gap-5 min-[1180px]:grid-cols-[minmax(0,1fr)_400px] items-start">
        <div className="flex flex-col gap-5 min-w-0">
          <Panel title="Transaction flow" icon={GitBranch}
            sub={view === 'graph' ? 'How the money moved between accounts, left to right' : 'Every payment in the flow, in time order'}
            right={
              <div className="flex items-center gap-2">
                <div role="group" aria-label="View" className="flex rounded-lg p-0.5" style={{ background: 'var(--surface-2)' }}>
                  {(['graph', 'timeline'] as const).map((v) => (
                    <button key={v} onClick={() => setView(v)} aria-pressed={view === v}
                      className="px-3 py-1.5 rounded-md text-sm font-medium"
                      style={view === v ? { background: 'var(--brand)', color: 'var(--brand-ink)' } : { color: 'var(--ink-2)' }}>
                      {v === 'graph' ? 'Graph view' : 'Timeline view'}
                    </button>
                  ))}
                </div>
                <Link to={{ pathname: '/network', search: query(o.case_id) }}
                  className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-sm font-medium"
                  style={{ border: '1px solid var(--border)', color: 'var(--ink)' }}>
                  <Maximize2 size={14} aria-hidden /> View full graph
                </Link>
              </div>
            }>
            {view === 'graph' ? (
              <>
                <FlowGraph nodes={o.flow.nodes} edges={o.flow.edges} currency={o.currency} rate={o.rate} />
                <FlowLegend />
                {o.flow.hidden_accounts > 0 && (
                  <p className="text-xs" style={{ color: 'var(--muted)' }}>
                    {o.flow.hidden_accounts} more account{o.flow.hidden_accounts === 1 ? '' : 's'} with smaller payments are on the Live Network.
                  </p>
                )}
              </>
            ) : <PaymentSequence payments={o.transactions} currency={o.currency} rate={o.rate} />}
          </Panel>

          <div className="grid gap-5 lg:grid-cols-[minmax(0,1.35fr)_minmax(0,1fr)]">
            <Panel title="Transaction timeline" icon={BarChart3} sub="When the money moved">
              <TimelineChart o={o} />
            </Panel>
            <Panel title="Amount distribution" icon={PieChart} sub="Where the money landed, by receiving account">
              <DistributionDonut o={o} />
            </Panel>
          </div>

          <Panel title="Recent transactions" icon={ReceiptText} sub="Newest first: the case's payments and the money followed from them">
            <PaymentsTable payments={o.transactions} currency={o.currency} rate={o.rate} />
          </Panel>

          <RiskIndicators indicators={o.indicators} risk={o.status.risk} />
          <InvestigationProgress live={inv.live} liveSteps={inv.steps} stored={o.steps} result={inv.result} error={inv.error} writing={writing} />
        </div>

        <div className="flex flex-col gap-5 min-w-0">
          <Findings findings={o.findings} explanations={o.explanations} investigated={investigated} currency={o.currency} />

          <section id="next-step" aria-labelledby="next-step-title" className="card p-5 flex flex-col gap-4 scroll-mt-4"
            style={{ borderColor: 'color-mix(in srgb, var(--hold) 55%, var(--border))', boxShadow: 'inset 0 4px 0 var(--hold)' }}>
            <header className="flex items-start gap-3">
              <span className="w-9 h-9 rounded-lg grid place-items-center shrink-0" style={{ background: 'var(--hold-tint)' }}>
                <ShieldCheck size={18} style={{ color: 'var(--hold)' }} aria-hidden />
              </span>
              <div>
                <h2 id="next-step-title" className="text-[17px] font-semibold leading-tight">Recommended next step</h2>
                <p className="text-sm mt-0.5" style={{ color: 'var(--ink-2)' }}>The agent proposes actions. A person approves or rejects each one, with a reason.</p>
              </div>
            </header>
            {inv.result ? (
              <HoldPanel key={inv.result.case_id} caseId={inv.result.case_id} result={inv.result} refreshKey={planVersion} onPlan={onPlan} />
            ) : (
              <p className="text-sm" style={{ color: 'var(--ink-2)' }}>
                {inv.live ? 'When the investigation ends, the agent proposes what to do here.' : 'Investigate the case first: the proposals follow from the evidence.'}
              </p>
            )}
            {o.next_steps.length > 0 && (
              <div>
                <h3 className="text-sm font-semibold mb-1">The report also suggests</h3>
                <ul className="list-disc pl-5 flex flex-col gap-1 text-sm" style={{ color: 'var(--ink-2)' }}>
                  {o.next_steps.map((s, i) => <li key={i}>{s}</li>)}
                </ul>
              </div>
            )}
          </section>

          <AiSummary summary={o.summary} live={inv.live} />
        </div>
      </div>

      <Stepper steps={steps} />
    </div>
  )
}
