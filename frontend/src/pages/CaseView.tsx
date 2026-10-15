import { useCallback, useEffect, useRef, useState } from 'react'
import { Link, useParams, useSearchParams } from 'react-router-dom'
import {
  Bar, BarChart, CartesianGrid, Cell, Line, LineChart, ResponsiveContainer, Scatter, ScatterChart, Tooltip, XAxis, YAxis, ZAxis,
} from 'recharts'
import { ArrowLeft, Database, Play } from 'lucide-react'
import GraphCanvas from '../components/GraphCanvas'
import { ActionBadge, KindTag } from '../components/badges'
import { ChartCard, StatTile, TipBox, axisProps } from '../components/ui'
import {
  fmtTime, fmtUsd, getJSON, postJSON, streamJob, type CaseInput, type CaseResult, type DispositionInfo,
  type Edge, type EvidenceItem, type GNode, type JobEvent, type ReportItem,
} from '../lib/api'

interface LiveStep {
  step: number
  tool: string
  args: Record<string, unknown>
  reason: string
  hypothesis?: string
  policy?: string
  summary?: Record<string, unknown>
  error?: string | null
  ms?: number
  risk?: number
  evidence: EvidenceItem[]
  queries: { label: string; cypher: string; ms: number; rows: number }[]
}

const TOOL_LABEL: Record<string, string> = {
  account_profile: 'Profile the account',
  counterparty_history: 'Check counterparty history',
  recurring_flows: 'Read behaviour memory',
  trace_funds: 'Trace the money',
  shared_counterparties: 'Find shared counterparties',
  find_cycles: 'Look for loops',
  owner_network: 'Check owner’s other accounts',
  case_memory: 'Check prior cases',
}

/* eslint-disable @typescript-eslint/no-explicit-any */
export default function CaseView() {
  const { caseId = '' } = useParams()
  const [params] = useSearchParams()
  const [input, setInput] = useState<CaseInput | null>(null)
  const [result, setResult] = useState<CaseResult | null>(null)
  const [nodes, setNodes] = useState<GNode[]>([])
  const [edges, setEdges] = useState<Edge[]>([])
  const [steps, setSteps] = useState<LiveStep[]>([])
  const [pending, setPending] = useState<string[]>([])
  const [running, setRunning] = useState(false)
  const [phase, setPhase] = useState<string>('')
  const [error, setError] = useState<string | null>(null)
  const [policy, setPolicy] = useState<'claude' | 'heuristic' | 'fixed'>('claude')
  const [focus, setFocus] = useState<string[]>([])
  const autoRan = useRef(false)

  const showResult = useCallback((r: CaseResult) => {
    setResult(r)
    setNodes(r.graph.nodes)
    setEdges(r.graph.edges)
    setSteps(r.steps.map((s) => ({
      ...s, evidence: r.evidence.filter((e) => e.step === s.step),
      queries: r.queries.filter((q) => q.step === s.step),
    })))
  }, [])

  useEffect(() => {
    getJSON<{ case: CaseInput; result: CaseResult | null }>(`/api/cases/${caseId}`).then(({ case: c, result: r }) => {
      setInput(c)
      if (r) showResult(r)
      else {
        setNodes(c.accounts.map((a) => ({ id: a, role: 'subject' })))
        setEdges(c.edges.map((e) => ({ ...e, role: 'alert' })))
      }
    }).catch((e) => setError(String(e)))
  }, [caseId, showResult])

  const start = useCallback(async () => {
    if (!input) return
    setError(null)
    setResult(null)
    setSteps([])
    setRunning(true)
    setPhase('starting')
    setNodes(input.accounts.map((a) => ({ id: a, role: 'subject' })))
    setEdges(input.edges.map((e) => ({ ...e, role: 'alert' })))
    try {
      const { job_id } = await postJSON<{ job_id: string }>(`/api/cases/${caseId}/investigate?policy=${policy}`)
      streamJob(job_id, (e: JobEvent) => {
        switch (e.type) {
          case 'graph':
            if (e.nodes?.length) setNodes((n) => [...n, ...e.nodes.filter((x: GNode) => !n.some((y) => y.id === x.id))])
            if (e.edges?.length) setEdges((x) => [...x, ...e.edges.filter((y: Edge) => !x.some((z) => z.tx_id === y.tx_id))])
            break
          case 'trace_wave':
            setPhase(`${e.direction} trace · hop ${e.hop} · ${e.edges.length} transfers · ${e.ms} ms`)
            setEdges((x) => [...x, ...e.edges.filter((y: Edge) => !x.some((z) => z.tx_id === y.tx_id)).map((y: Edge) => ({ ...y, role: 'trace' }))])
            setFocus(e.new_accounts ?? [])
            break
          case 'step_start':
            setPhase(`step ${e.step}: ${TOOL_LABEL[e.tool] ?? e.tool}`)
            setSteps((s) => [...s, { step: e.step, tool: e.tool, args: e.args, reason: e.reason, hypothesis: e.hypothesis,
              policy: e.policy, evidence: [], queries: [] }])
            break
          case 'query':
            setPending((p) => [...p.slice(-3), `${e.label} · ${e.ms} ms · ${e.rows} rows`])
            setSteps((s) => s.map((x) => x.step === e.step ? { ...x, queries: [...x.queries, e as any] } : x))
            break
          case 'step_done':
            setSteps((s) => s.map((x) => x.step === e.step ? { ...x, summary: e.summary, error: e.error, ms: e.ms, risk: e.risk,
              evidence: e.evidence } : x))
            break
          case 'decision':
            setPhase(`decision: ${e.action} (risk ${e.risk})`)
            break
          case 'report_start':
            setPhase('writing the analyst report…')
            break
          case 'report':
            showResult(e.case)
            setPhase('done')
            setFocus([])
            break
          case 'error':
            setError(e.error)
            break
        }
      }, () => setRunning(false))
    } catch (err) {
      setError(String(err))
      setRunning(false)
    }
  }, [caseId, input, policy, showResult])

  useEffect(() => {
    if (params.get('run') && input && !result && !autoRan.current) {
      autoRan.current = true
      start()
    }
  }, [params, input, result, start])

  const riskSeries = steps.filter((s) => s.risk !== undefined).map((s) => ({ step: `S${s.step}`, risk: s.risk }))
  const queriesCount = steps.reduce((a, s) => a + s.queries.length, 0)
  const queryMs = steps.reduce((a, s) => a + s.queries.reduce((b, q) => b + q.ms, 0), 0)

  if (error && !input) return <div className="p-6 text-sm" style={{ color: 'var(--critical)' }}>{error}</div>
  if (!input) return <div className="p-6 text-sm" style={{ color: 'var(--muted)' }}>Loading case…</div>

  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-wrap items-center gap-3">
        <Link to={`/investigate?batch=${caseId.split('-')[0]}`} className="flex items-center gap-1 text-xs" style={{ color: 'var(--ink-2)' }}>
          <ArrowLeft size={14} aria-hidden /> Case queue
        </Link>
        <h1 className="text-lg font-semibold">Case {caseId}</h1>
        <span className="text-xs" style={{ color: 'var(--ink-2)' }}>
          primary {input.primary} · {input.accounts.length} accounts · {input.edges.length} alerted transfers · {fmtUsd(input.amount)}
        </span>
        <div className="ml-auto flex items-center gap-2">
          <select value={policy} onChange={(e) => setPolicy(e.target.value as typeof policy)} disabled={running}
            className="text-xs rounded-md px-2 py-1.5" style={{ background: 'var(--surface)', border: '1px solid var(--border)', color: 'var(--ink)' }}
            aria-label="Investigation policy">
            <option value="claude">Adaptive agent (Claude)</option>
            <option value="heuristic">Adaptive rules (no LLM)</option>
            <option value="fixed">Fixed baseline</option>
          </select>
          <button onClick={start} disabled={running}
            className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-sm font-medium disabled:opacity-40"
            style={{ background: 'var(--s1)', color: '#fff' }}>
            <Play size={14} aria-hidden /> {result ? 'Re-run investigation' : 'Start investigation'}
          </button>
        </div>
      </div>
      {error && <p className="text-xs" style={{ color: 'var(--critical)' }}>{error}</p>}

      <div className="grid gap-4 xl:grid-cols-[minmax(0,1fr)_minmax(0,1fr)]">
        {/* LEFT: how the investigation runs */}
        <div className="flex flex-col gap-4 min-w-0">
          <ChartCard title="Graph traversal (FalkorDB)"
            subtitle={running ? phase : result ? `${result.graph.nodes.length} accounts and ${result.graph.edges.length} transfers retrieved` : 'Alerted transfers. Start the investigation to watch the agent traverse the graph.'}
            right={<span className="flex items-center gap-1 text-xs tabular" style={{ color: 'var(--ink-2)' }}><Database size={12} aria-hidden />{queriesCount} queries · {Math.round(queryMs)} ms</span>}>
            <GraphCanvas nodes={nodes} edges={edges} primary={input.primary} focus={focus} height={400} />
            {running && pending.length > 0 && (
              <div className="text-[11px] font-mono tabular" style={{ color: 'var(--muted)' }}>
                {pending.map((p, i) => <div key={i}>› {p}</div>)}
              </div>
            )}
          </ChartCard>

          {riskSeries.length > 0 && (
            <ChartCard title="Risk after each step" subtitle="Recomputed from the evidence ledger after every tool call.">
              <div className="h-36">
                <ResponsiveContainer>
                  <LineChart data={riskSeries} margin={{ top: 8, right: 12, left: 0, bottom: 0 }}>
                    <CartesianGrid vertical={false} stroke="var(--grid)" />
                    <XAxis dataKey="step" {...axisProps} />
                    <YAxis domain={[0, 1]} {...axisProps} axisLine={false} width={32} />
                    <Tooltip content={({ active, payload }: any) => active && payload?.length
                      ? <TipBox title={payload[0].payload.step} rows={[{ label: 'Risk', value: payload[0].payload.risk.toFixed(3), color: 'var(--s1)' }]} /> : null} />
                    <Line isAnimationActive={false} dataKey="risk" stroke="var(--s1)" strokeWidth={2} dot={{ r: 4 }} />
                  </LineChart>
                </ResponsiveContainer>
              </div>
            </ChartCard>
          )}

          <ChartCard title="Investigation run" subtitle="Each step: what the agent chose, why, the Cypher it ran, and what it found.">
            {steps.length === 0 && <p className="text-xs" style={{ color: 'var(--muted)' }}>No steps yet.</p>}
            <ol className="flex flex-col gap-3">
              {steps.map((s) => (
                <li key={s.step} className="rounded-lg p-3" style={{ border: '1px solid var(--border)', background: 'var(--surface-2)' }}>
                  <div className="flex flex-wrap items-center gap-2 text-sm">
                    <span className="tabular text-xs px-1.5 rounded" style={{ border: '1px solid var(--border)', color: 'var(--ink-2)' }}>{s.step}</span>
                    <span className="font-medium">{TOOL_LABEL[s.tool] ?? s.tool}</span>
                    <code className="text-[11px]" style={{ color: 'var(--muted)' }}>{s.tool}({Object.values(s.args).map((v) => Array.isArray(v) ? `[${v.length}]` : String(v)).join(', ')})</code>
                    {s.policy && <span className="text-[10px] uppercase tracking-wide" style={{ color: 'var(--muted)' }}>{s.policy}</span>}
                    <span className="ml-auto text-xs tabular" style={{ color: 'var(--ink-2)' }}>
                      {s.ms !== undefined ? `${s.ms} ms` : <span className="animate-pulse">running…</span>}
                      {s.risk !== undefined && ` · risk ${s.risk.toFixed(2)}`}
                    </span>
                  </div>
                  <p className="text-xs mt-1" style={{ color: 'var(--ink-2)' }}>
                    {s.hypothesis && <span style={{ color: 'var(--muted)' }}>[{s.hypothesis}] </span>}{s.reason}
                  </p>
                  {s.error && <p className="text-xs mt-1" style={{ color: 'var(--critical)' }}>{s.error}</p>}
                  {s.evidence.length > 0 && (
                    <ul className="mt-2 flex flex-col gap-1">
                      {s.evidence.map((e) => (
                        <li key={e.id} className="text-xs flex gap-2 cursor-default" onMouseEnter={() => setFocus(e.accounts)} onMouseLeave={() => setFocus([])}>
                          <span className="tabular shrink-0" style={{ color: 'var(--muted)' }}>{e.id}</span>
                          <KindTag kind={e.kind} />
                          <span style={{ color: 'var(--ink)' }}>{e.claim}</span>
                        </li>
                      ))}
                    </ul>
                  )}
                  {s.queries.length > 0 && (
                    <details className="mt-2">
                      <summary className="text-[11px] cursor-pointer" style={{ color: 'var(--muted)' }}>
                        {s.queries.length} Cypher quer{s.queries.length === 1 ? 'y' : 'ies'} · {Math.round(s.queries.reduce((a, q) => a + q.ms, 0))} ms
                      </summary>
                      {s.queries.map((q, i) => (
                        <div key={i} className="mt-1.5">
                          <div className="text-[11px] tabular" style={{ color: 'var(--ink-2)' }}>{q.label} · {q.ms} ms · {q.rows} rows</div>
                          <pre className="text-[10px] p-2 rounded overflow-x-auto" style={{ background: 'var(--page)', color: 'var(--ink-2)' }}>{q.cypher}</pre>
                        </div>
                      ))}
                    </details>
                  )}
                </li>
              ))}
            </ol>
          </ChartCard>
        </div>

        {/* RIGHT: the report */}
        <div className="flex flex-col gap-4 min-w-0">
          {result ? <ReportPanel r={result} input={input} onFocus={setFocus} /> : (
            <ChartCard title="Analyst report" subtitle="Appears when the investigation finishes.">
              <div className="grid grid-cols-2 gap-3">
                <StatTile label="Model score" value={input.ml_score.toFixed(2)} note="highest triage-model score in the case" />
                <StatTile label="Shape" value={input.shape.typology} note={input.shape.why} />
              </div>
              {input.detector_hits.length > 0 && (
                <ul className="text-xs flex flex-col gap-1" style={{ color: 'var(--ink-2)' }}>
                  {input.detector_hits.map((h, i) => <li key={i}><b style={{ color: 'var(--ink)' }}>{h.detector}</b> on {h.account}: {h.detail}</li>)}
                </ul>
              )}
            </ChartCard>
          )}
        </div>
      </div>
    </div>
  )
}

function Cite({ ids }: { ids: string[] }) {
  if (!ids.length) return null
  return <span className="text-[10px] tabular ml-1" style={{ color: 'var(--muted)' }}>[{ids.join(', ')}]</span>
}

function Para({ item }: { item: ReportItem }) {
  return <p className="text-sm leading-relaxed" style={{ color: 'var(--ink)' }}>{item.text}<Cite ids={item.evidence_ids} /></p>
}

function ScoreBreakdown({ d }: { d: DispositionInfo }) {
  const rows = [
    { name: 'Model score', v: d.components.model },
    { name: 'Red flags', v: d.components.red_flags },
    { name: 'Shape', v: d.components.shape },
    { name: 'Legitimacy', v: d.components.legitimacy },
  ]
  return (
    <div className="h-36">
      <ResponsiveContainer>
        <BarChart data={rows} layout="vertical" margin={{ top: 0, right: 40, left: 8, bottom: 0 }}>
          <CartesianGrid horizontal={false} stroke="var(--grid)" />
          <XAxis type="number" domain={[-0.5, 0.5]} {...axisProps} />
          <YAxis type="category" dataKey="name" {...axisProps} axisLine={false} width={84} />
          <Tooltip cursor={{ fill: 'var(--grid)' }} content={({ active, payload }: any) => active && payload?.length
            ? <TipBox title={payload[0].payload.name} rows={[{ label: 'Contribution to risk', value: payload[0].payload.v.toFixed(3) }]} /> : null} />
          <Bar isAnimationActive={false} dataKey="v" radius={4} maxBarSize={14}
            label={{ position: 'right', fill: 'var(--ink-2)', fontSize: 11, formatter: (v: any) => Number(v).toFixed(2) }}>
            {rows.map((r) => <Cell key={r.name} fill={r.v >= 0 ? 'var(--s2)' : 'var(--s1)'} />)}
          </Bar>
        </BarChart>
      </ResponsiveContainer>
    </div>
  )
}

function ReportPanel({ r, input, onFocus }: { r: CaseResult; input: CaseInput; onFocus: (a: string[]) => void }) {
  const d = r.disposition
  const rep = r.report
  const timeline = r.graph.edges.map((e) => ({ ...e, x: e.ts, y: e.amount, z: 1 }))
  const roles = [
    { role: 'alert', label: 'Alerted', color: 'var(--s2)' },
    { role: 'trace', label: 'Traced', color: 'var(--s1)' },
    { role: 'cycle', label: 'Loop', color: 'var(--s8)' },
  ]
  const support = r.evidence.filter((e) => e.kind === 'support')
  const counter = r.evidence.filter((e) => e.kind === 'counter')

  return (
    <>
      <ChartCard title={rep.headline}
        subtitle={`Written by ${rep.author ?? 'template'} from ${r.evidence.length} evidence items · ${rep.validation.statements_checked} statements verified, ${rep.validation.unsupported} removed`}>
        <div className="flex flex-wrap items-center gap-3">
          <ActionBadge action={d.action} large />
          <span className="text-sm tabular">risk <b>{d.risk.toFixed(2)}</b></span>
          <span className="text-sm">confidence <b>{d.confidence_label}</b> <span className="tabular" style={{ color: 'var(--ink-2)' }}>({Math.round(d.confidence * 100)}%)</span></span>
        </div>
        <div className="h-2 rounded-full relative" style={{ background: 'var(--grid)' }} aria-label={`risk ${d.risk}`}>
          <div className="h-2 rounded-full" style={{ width: `${d.risk * 100}%`, background: 'var(--s1)' }} />
          <span className="absolute -top-1 w-0.5 h-4" style={{ left: '25%', background: 'var(--good)' }} title="close below" />
          <span className="absolute -top-1 w-0.5 h-4" style={{ left: '55%', background: 'var(--critical)' }} title="escalate at" />
        </div>
        <div className="flex justify-between text-[10px]" style={{ color: 'var(--muted)' }}><span>0 · close below 0.25</span><span>escalate at 0.55 · 1</span></div>
        <ul className="text-xs list-disc pl-4" style={{ color: 'var(--ink-2)' }}>{d.reasons.map((x, i) => <li key={i}>{x}</li>)}</ul>
      </ChartCard>

      <div className="grid gap-4 md:grid-cols-2">
        <ChartCard title="Laundering shape" subtitle="Graph-structure classifier (96.5% accurate on ground-truth schemes with 3+ accounts).">
          <div className="text-xl font-semibold">{r.shape?.typology ?? '—'}</div>
          <p className="text-xs" style={{ color: 'var(--ink-2)' }}>{r.shape?.why} · confidence {Math.round((r.shape?.confidence ?? 0) * 100)}%</p>
        </ChartCard>
        <ChartCard title="Possible predicate crime" subtitle="Hypothesis only: the data does not record crime type.">
          {r.crime_hypotheses.length ? (
            <ul className="text-sm flex flex-col gap-1">
              {r.crime_hypotheses.map((h) => <li key={h.crime}>{h.crime} <span className="text-xs" style={{ color: 'var(--muted)' }}>· {h.basis}</span></li>)}
            </ul>
          ) : <p className="text-xs" style={{ color: 'var(--muted)' }}>No hypothesis.</p>}
        </ChartCard>
      </div>

      <ChartCard title="How the risk score was built" subtitle="Positive terms push towards escalation; legitimate explanations pull it down.">
        <ScoreBreakdown d={d} />
      </ChartCard>

      <ChartCard title="Executive summary">
        <Para item={rep.executive_summary} />
        <h3 className="text-xs font-semibold mt-1" style={{ color: 'var(--ink-2)' }}>Subject</h3>
        <Para item={rep.subject_profile} />
        <h3 className="text-xs font-semibold mt-1" style={{ color: 'var(--ink-2)' }}>Typology assessment</h3>
        <Para item={rep.typology_assessment} />
      </ChartCard>

      <div className="grid gap-4 md:grid-cols-2">
        <ChartCard title={`Red flags (${support.length})`}>
          <EvidenceList items={support} onFocus={onFocus} />
        </ChartCard>
        <ChartCard title={`Legitimate explanations (${counter.length})`}>
          <EvidenceList items={counter} onFocus={onFocus} empty="No legitimate explanation found in the graph." />
        </ChartCard>
      </div>

      {rep.money_trail.length > 0 && (
        <ChartCard title="Money trail">
          <ol className="flex flex-col gap-1.5 list-decimal pl-4">{rep.money_trail.map((m, i) => <li key={i} className="text-sm">{m.text}<Cite ids={m.evidence_ids} /></li>)}</ol>
        </ChartCard>
      )}

      <ChartCard title="Transfers over time" subtitle="Every transfer the investigation touched, by time and amount (log scale).">
        <div className="h-52">
          <ResponsiveContainer>
            <ScatterChart margin={{ top: 8, right: 12, left: 0, bottom: 0 }}>
              <CartesianGrid stroke="var(--grid)" />
              <XAxis dataKey="x" type="number" domain={['dataMin', 'dataMax']} {...axisProps} tickFormatter={(v) => fmtTime(v).slice(6)} />
              <YAxis dataKey="y" type="number" scale="log" domain={['auto', 'auto']} {...axisProps} axisLine={false} width={48} tickFormatter={(v) => fmtUsd(v)} />
              <ZAxis dataKey="z" range={[40, 40]} />
              <Tooltip content={({ active, payload }: any) => active && payload?.length
                ? <TipBox title={payload[0].payload.tx_id} rows={[
                  { label: 'When', value: fmtTime(payload[0].payload.ts) },
                  { label: 'Amount', value: fmtUsd(payload[0].payload.amount) },
                  { label: 'From', value: payload[0].payload.src },
                  { label: 'To', value: payload[0].payload.dst },
                  { label: 'Format', value: payload[0].payload.fmt ?? '—' },
                ]} /> : null} />
              {roles.map((ro) => (
                <Scatter key={ro.role} isAnimationActive={false} name={ro.label} data={timeline.filter((t) => (t.role ?? 'context') === ro.role)} fill={ro.color} />
              ))}
            </ScatterChart>
          </ResponsiveContainer>
        </div>
        <div className="flex gap-3 text-xs" style={{ color: 'var(--ink-2)' }}>
          {roles.map((ro) => <span key={ro.role} className="flex items-center gap-1.5"><span className="w-2 h-2 rounded-full" style={{ background: ro.color }} />{ro.label}</span>)}
        </div>
      </ChartCard>

      {rep.str_narrative && (
        <ChartCard title="Draft STR narrative (for FIU-IND)" subtitle="For human review and filing. FlowTrace never files or freezes anything itself.">
          <p className="text-sm leading-relaxed whitespace-pre-wrap">{rep.str_narrative}</p>
        </ChartCard>
      )}

      <div className="grid gap-4 md:grid-cols-2">
        <ChartCard title="Unknowns">
          <ul className="text-sm list-disc pl-4 flex flex-col gap-1">{rep.unknowns.map((u, i) => <li key={i}>{u}</li>)}</ul>
        </ChartCard>
        <ChartCard title="Next steps for the analyst">
          <ul className="text-sm list-disc pl-4 flex flex-col gap-1">{rep.next_steps.map((u, i) => <li key={i}>{u}</li>)}</ul>
        </ChartCard>
      </div>

      <ChartCard title="Run statistics">
        <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
          <StatTile label="Steps" value={String(r.stats.steps)} note={String(r.stats.stop_reason)} />
          <StatTile label="Cypher queries" value={String(r.stats.queries)} note={`${r.stats.query_ms} ms in FalkorDB`} />
          <StatTile label="Wall time" value={`${r.stats.seconds}s`} note={`${r.stats.llm_calls} LLM calls`} />
          <StatTile label="Unsupported claims" value={`${Math.round(rep.validation.unsupported_rate * 100)}%`} note="statements removed by the validator" />
        </div>
        {input.ground_truth !== null && (
          <p className="text-xs" style={{ color: 'var(--muted)' }}>
            Ground truth (hidden from the agent): {input.ground_truth === 1 ? 'contains labelled laundering' : 'no labelled laundering'}.
          </p>
        )}
      </ChartCard>
    </>
  )
}

function EvidenceList({ items, onFocus, empty }: { items: EvidenceItem[]; onFocus: (a: string[]) => void; empty?: string }) {
  if (!items.length) return <p className="text-xs" style={{ color: 'var(--muted)' }}>{empty ?? 'None found.'}</p>
  return (
    <ul className="flex flex-col gap-2">
      {items.map((e) => (
        <li key={e.id} className="text-sm" onMouseEnter={() => onFocus(e.accounts)} onMouseLeave={() => onFocus([])}>
          <div className="flex items-center gap-2 text-[11px]" style={{ color: 'var(--muted)' }}>
            <span className="tabular">{e.id}</span><span>{e.code}</span>
            {e.escalator && <span style={{ color: 'var(--ink-2)' }}>· overrides legitimacy</span>}
            <span className="ml-auto tabular">weight {e.weight.toFixed(2)} · {e.tx_ids.length} tx</span>
          </div>
          <div>{e.claim}</div>
        </li>
      ))}
    </ul>
  )
}
