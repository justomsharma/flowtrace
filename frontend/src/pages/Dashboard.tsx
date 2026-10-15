import { useEffect, useMemo, useState } from 'react'
import {
  Bar, BarChart, CartesianGrid, Line, LineChart, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis,
} from 'recharts'
import { ChartCard, StatTile, TipBox, axisProps } from '../components/ui'
import { fmtCompact, fmtInt, fmtPct, getJSON, type ModelInfo, type Overview } from '../lib/api'

const FEATURE_LABELS: Record<string, string> = {
  payment_format: 'Payment format',
  pair_age_h: 'Age of the account relationship',
  dst_hist_in_cnt: 'Receiver: past incoming transfers',
  log_amount: 'Amount',
  src_out_cnt: 'Sender: transfers out today',
  src_out_sum: 'Sender: value out today',
  src_hist_active_days: 'Sender: days active before',
  dst_in_sum: 'Receiver: value in today',
  dst_in_distinct: 'Receiver: distinct senders today',
  src_entity_n_accounts: 'Sender owner: number of accounts',
  pair_same_amt_prior: 'Same amount paid to this receiver before',
  dst_entity_n_accounts: 'Receiver owner: number of accounts',
  pair_prior_cnt: 'Prior transfers between the pair',
  sg_paths: 'Scatter-gather paths today',
  reciprocal_same_day: 'Money sent back same day',
  hour: 'Hour of day',
}
const label = (f: string) => FEATURE_LABELS[f] ?? f.replaceAll('_', ' ')

/* eslint-disable @typescript-eslint/no-explicit-any */
const tip =
  (title: (p: any) => string, rows: (p: any) => { label: string; value: string; color?: string }[]) =>
  ({ active, payload }: any) =>
    active && payload?.length ? <TipBox title={title(payload[0].payload)} rows={rows(payload[0].payload)} /> : null

export default function Dashboard() {
  const [ov, setOv] = useState<Overview | null>(null)
  const [model, setModel] = useState<ModelInfo | null>(null)
  const [err, setErr] = useState<string | null>(null)
  const [thrIdx, setThrIdx] = useState<number | null>(null)

  useEffect(() => {
    getJSON<Overview>('/api/overview').then(setOv).catch((e) => setErr(String(e)))
    getJSON<ModelInfo>('/api/model').then(setModel).catch((e) => setErr(String(e)))
  }, [])

  const sweep = model?.curves?.threshold_sweep ?? []
  const defaultIdx = useMemo(() => {
    if (!model?.curves) return 0
    const t = model.curves.threshold
    return sweep.reduce((best, s, i) => (Math.abs(s.threshold - t) < Math.abs(sweep[best].threshold - t) ? i : best), 0)
  }, [model, sweep])
  const sel = sweep[thrIdx ?? defaultIdx]

  if (err) return <div className="p-6 text-sm" style={{ color: 'var(--critical)' }}>Could not load data: {err}</div>
  if (!ov || !model) return <div className="p-6 text-sm" style={{ color: 'var(--muted)' }}>Loading…</div>

  const t = model.metrics.test
  const positives = t.positives
  const features = Object.entries(model.metrics.feature_importance_gain)
    .slice(0, 10)
    .map(([k, v]) => ({ feature: label(k), gain: v }))
  const daily = ov.daily.filter((d) => d.date <= '2022-09-10')
  const relMax = Math.max(...ov.relationship_history.map((r) => r.rate_pct))
  const rel1 = ov.relationship_history.find((r) => r.bucket === '1')
  const relMany = ov.relationship_history.find((r) => r.bucket === '>10')

  return (
    <div className="flex flex-col gap-4">
      {/* Headline tiles */}
      <div className="grid gap-3 grid-cols-2 md:grid-cols-3 xl:grid-cols-6">
        <StatTile label="Transactions screened" value={fmtCompact(ov.totals.transactions)}
          note={`${fmtInt(ov.totals.accounts)} accounts · 18 days`} />
        <StatTile label="Known laundering" value={fmtInt(ov.totals.laundering)}
          note={`${fmtPct(ov.totals.laundering / ov.totals.transactions, 2)} of all transfers`} />
        <StatTile label="Precision @ top 100" value={fmtPct(t.precision_at_100, 0)}
          note="of the 100 highest-risk test transfers are laundering" accent="var(--good)" />
        <StatTile label="F1 (laundering class)" value={t.f1.toFixed(3)}
          note={`P ${fmtPct(t.precision, 0)} · R ${fmtPct(t.recall, 0)} · PR-AUC ${t.pr_auc.toFixed(2)}`} />
        <StatTile label="Accuracy" value={fmtPct(t.accuracy, 2)}
          note={`Flagging nothing scores ${fmtPct(t.accuracy_all_legit_baseline, 2)}`} />
        <StatTile label="Ground-truth patterns" value={fmtInt(ov.totals.pattern_attempts)}
          note={`${fmtInt(ov.totals.pattern_transactions)} transfers in 8 laundering shapes`} />
      </div>

      {/* Insight + format */}
      <div className="grid gap-4 lg:grid-cols-2">
        <ChartCard
          title="Relationship history separates genuine payments from laundering"
          subtitle={rel1 && relMany
            ? `Pairs that transacted once launder at ${rel1.rate_pct.toFixed(2)}%; pairs with over 10 transfers at ${relMany.rate_pct.toFixed(3)}% (${Math.round(rel1.rate_pct / relMany.rate_pct)}× lower).`
            : undefined}
        >
          <div className="h-56">
            <ResponsiveContainer>
              <BarChart data={ov.relationship_history} margin={{ top: 16, right: 8, left: 0, bottom: 12 }}>
                <CartesianGrid vertical={false} stroke="var(--grid)" />
                <XAxis dataKey="bucket" {...axisProps} label={{ value: 'Transfers between the same two accounts', position: 'insideBottom', offset: -2, fill: 'var(--muted)', fontSize: 11 }} height={36} />
                <YAxis {...axisProps} axisLine={false} tickFormatter={(v) => `${v}%`} domain={[0, Math.ceil(relMax)]} width={40} />
                <Tooltip cursor={{ fill: 'var(--grid)' }} content={tip(
                  (p) => `${p.bucket} transfer${p.bucket === '1' ? '' : 's'} in the pair`,
                  (p) => [
                    { label: 'Laundering rate', value: `${p.rate_pct.toFixed(3)}%`, color: 'var(--s1)' },
                    { label: 'Transfers', value: fmtInt(p.transactions) },
                    { label: 'Laundering', value: fmtInt(p.laundering) },
                  ])} />
                <Bar isAnimationActive={false} dataKey="rate_pct" fill="var(--s1)" radius={[4, 4, 0, 0]} maxBarSize={56}
                  label={{ position: 'top', fill: 'var(--ink-2)', fontSize: 11, formatter: (v: any) => `${Number(v).toFixed(2)}%` }} />
              </BarChart>
            </ResponsiveContainer>
          </div>
        </ChartCard>

        <ChartCard title="Laundering rate by payment format"
          subtitle="ACH carries most laundering; Wire and Reinvestment carry none in this data.">
          <div className="h-56">
            <ResponsiveContainer>
              <BarChart data={[...ov.by_format].sort((a, b) => b.rate_pct - a.rate_pct)} layout="vertical" margin={{ top: 0, right: 48, left: 8, bottom: 0 }}>
                <CartesianGrid horizontal={false} stroke="var(--grid)" />
                <XAxis type="number" {...axisProps} tickFormatter={(v) => `${v}%`} />
                <YAxis type="category" dataKey="payment_format" {...axisProps} axisLine={false} width={86} />
                <Tooltip cursor={{ fill: 'var(--grid)' }} content={tip(
                  (p) => p.payment_format,
                  (p) => [
                    { label: 'Laundering rate', value: `${p.rate_pct.toFixed(3)}%`, color: 'var(--s1)' },
                    { label: 'Transfers', value: fmtInt(p.transactions) },
                    { label: 'Laundering', value: fmtInt(p.laundering) },
                  ])} />
                <Bar isAnimationActive={false} dataKey="rate_pct" fill="var(--s1)" radius={[0, 4, 4, 0]} maxBarSize={18}
                  label={{ position: 'right', fill: 'var(--ink-2)', fontSize: 11, formatter: (v: any) => `${Number(v).toFixed(2)}%` }} />
              </BarChart>
            </ResponsiveContainer>
          </div>
        </ChartCard>
      </div>

      {/* Threshold explorer */}
      {sel && (
        <ChartCard
          title="Alert threshold explorer (test days Sep 9–10, never used for tuning)"
          subtitle="Move the threshold to trade analyst workload against missed laundering."
          right={<span className="text-xs tabular" style={{ color: 'var(--ink-2)' }}>threshold {sel.threshold.toFixed(2)}</span>}
        >
          <input
            type="range" min={0} max={sweep.length - 1} value={thrIdx ?? defaultIdx}
            onChange={(e) => setThrIdx(Number(e.target.value))}
            aria-label="Alert threshold" className="w-full accent-[var(--s1)]"
          />
          <div className="grid gap-3 grid-cols-2 md:grid-cols-4">
            <StatTile label="Alerts raised" value={fmtInt(sel.alerts)} note={`from ${fmtInt(t.n)} transfers`} />
            <StatTile label="Laundering caught" value={`${fmtInt(sel.tp)} / ${fmtInt(positives)}`} note={`recall ${fmtPct(sel.recall)}`} accent="var(--s1)" />
            <StatTile label="False alarms" value={fmtInt(sel.alerts - sel.tp)} note={`precision ${fmtPct(sel.precision)}`} accent="var(--s2)" />
            <StatTile label="Missed" value={fmtInt(positives - sel.tp)} note="laundering transfers below threshold" />
          </div>
          <div className="h-56">
            <ResponsiveContainer>
              <LineChart data={sweep} margin={{ top: 8, right: 16, left: 0, bottom: 0 }}>
                <CartesianGrid vertical={false} stroke="var(--grid)" />
                <XAxis dataKey="threshold" type="number" domain={[0, 1]} {...axisProps} tickFormatter={(v) => v.toFixed(1)} />
                <YAxis {...axisProps} axisLine={false} domain={[0, 1]} tickFormatter={(v) => `${Math.round(v * 100)}%`} width={40} />
                <ReferenceLine x={sel.threshold} stroke="var(--ink-2)" strokeDasharray="3 3" />
                <Tooltip content={tip(
                  (p) => `Threshold ${p.threshold.toFixed(2)}`,
                  (p) => [
                    { label: 'Precision', value: fmtPct(p.precision), color: 'var(--s2)' },
                    { label: 'Recall', value: fmtPct(p.recall), color: 'var(--s1)' },
                    { label: 'Alerts', value: fmtInt(p.alerts) },
                  ])} />
                <Line isAnimationActive={false} dataKey="recall" name="Recall" stroke="var(--s1)" strokeWidth={2} dot={false} />
                <Line isAnimationActive={false} dataKey="precision" name="Precision" stroke="var(--s2)" strokeWidth={2} dot={false} />
              </LineChart>
            </ResponsiveContainer>
          </div>
          <div className="flex gap-4 text-xs" style={{ color: 'var(--ink-2)' }}>
            <span className="flex items-center gap-1.5"><span className="w-3 h-0.5" style={{ background: 'var(--s1)' }} />Recall: share of laundering caught</span>
            <span className="flex items-center gap-1.5"><span className="w-3 h-0.5" style={{ background: 'var(--s2)' }} />Precision: share of alerts that are real</span>
          </div>
        </ChartCard>
      )}

      {/* Shape recognition + agent evaluation */}
      <div className="grid gap-4 lg:grid-cols-2">
        {model.typology && (
          <ChartCard title="Laundering-shape recognition vs ground truth"
            subtitle={`${fmtPct(model.typology.accuracy_3plus_accounts)} correct on the ${model.typology.attempts_3plus_accounts} schemes with 3+ accounts; ${fmtPct(model.typology.accuracy)} on all ${model.typology.attempts} (1–2 transfer schemes are inherently ambiguous).`}>
            <div className="h-56">
              <ResponsiveContainer>
                <BarChart data={Object.entries(model.typology.per_class).map(([k, v]) => ({ typology: k, recall: v.recall, attempts: v.attempts }))
                  .sort((a, b) => b.recall - a.recall)} layout="vertical" margin={{ top: 0, right: 40, left: 8, bottom: 0 }}>
                  <CartesianGrid horizontal={false} stroke="var(--grid)" />
                  <XAxis type="number" domain={[0, 1]} {...axisProps} tickFormatter={(v) => `${Math.round(v * 100)}%`} />
                  <YAxis type="category" dataKey="typology" {...axisProps} axisLine={false} width={104} />
                  <Tooltip cursor={{ fill: 'var(--grid)' }} content={tip((p) => p.typology, (p) => [
                    { label: 'Recognised', value: fmtPct(p.recall), color: 'var(--s1)' },
                    { label: 'Schemes', value: fmtInt(p.attempts) },
                  ])} />
                  <Bar isAnimationActive={false} dataKey="recall" fill="var(--s1)" radius={[0, 4, 4, 0]} maxBarSize={14}
                    label={{ position: 'right', fill: 'var(--ink-2)', fontSize: 11, formatter: (v: any) => `${Math.round(Number(v) * 100)}%` }} />
                </BarChart>
              </ResponsiveContainer>
            </div>
          </ChartCard>
        )}
        <ChartCard title="Adaptive agent vs fixed workflow"
          subtitle={model.agent_eval ? `Same alerts, same budget: ${model.agent_eval.cases} cases from ${model.agent_eval.sample}.`
            : 'Not run yet. With FalkorDB loaded: python -m flowtrace.eval.compare'}>
          {model.agent_eval ? (
            <div className="overflow-x-auto">
              <table className="w-full text-xs tabular">
                <thead style={{ color: 'var(--muted)' }}>
                  <tr className="text-left">
                    <th className="py-1.5 pr-2 font-medium">Policy</th>
                    <th className="py-1.5 pr-2 font-medium text-right">Legit auto-closed</th>
                    <th className="py-1.5 pr-2 font-medium text-right">Laundering kept open</th>
                    <th className="py-1.5 pr-2 font-medium text-right">Escalate precision</th>
                    <th className="py-1.5 pr-2 font-medium text-right">Evidence coverage</th>
                    <th className="py-1.5 pr-2 font-medium text-right">Queries</th>
                    <th className="py-1.5 pr-2 font-medium text-right">Seconds</th>
                  </tr>
                </thead>
                <tbody>
                  {Object.entries(model.agent_eval.policies).map(([k, v]) => (
                    <tr key={k} style={{ borderTop: '1px solid var(--border)' }}>
                      <td className="py-1.5 pr-2 font-medium">{k}</td>
                      {['legit_auto_closed', 'laundering_kept_open', 'escalate_precision', 'evidence_coverage'].map((m) => (
                        <td key={m} className="py-1.5 pr-2 text-right">{v[m] == null ? '—' : fmtPct(Number(v[m]), 0)}</td>
                      ))}
                      <td className="py-1.5 pr-2 text-right">{Number(v.avg_queries).toFixed(1)}</td>
                      <td className="py-1.5 pr-2 text-right">{Number(v.avg_seconds).toFixed(1)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : <p className="text-xs" style={{ color: 'var(--muted)' }}>Results appear here once the comparison has been run on the real graph.</p>}
        </ChartCard>
      </div>

      {/* Volume, typologies, features */}
      <div className="grid gap-4 lg:grid-cols-3">
        <ChartCard title="Transfers per day" subtitle="Weekends dip; Sep 1 is a month-start payment spike.">
          <div className="h-64">
            <ResponsiveContainer>
              <BarChart data={daily} margin={{ top: 4, right: 4, left: 0, bottom: 0 }}>
                <CartesianGrid vertical={false} stroke="var(--grid)" />
                <XAxis dataKey="date" {...axisProps} tickFormatter={(d) => d.slice(8)} />
                <YAxis {...axisProps} axisLine={false} tickFormatter={fmtCompact} width={40} />
                <Tooltip cursor={{ fill: 'var(--grid)' }} content={tip((p) => p.date, (p) => [
                  { label: 'Transfers', value: fmtInt(p.transactions), color: 'var(--s1)' },
                  { label: 'Laundering', value: fmtInt(p.laundering) },
                ])} />
                <Bar isAnimationActive={false} dataKey="transactions" fill="var(--s1)" radius={[4, 4, 0, 0]} />
              </BarChart>
            </ResponsiveContainer>
          </div>
        </ChartCard>

        <ChartCard title="Laundering shapes in ground truth" subtitle="Attempts per typology (patterns file).">
          <div className="h-64">
            <ResponsiveContainer>
              <BarChart data={[...ov.typologies].sort((a, b) => b.attempts - a.attempts)} layout="vertical" margin={{ top: 0, right: 28, left: 8, bottom: 0 }}>
                <XAxis type="number" hide />
                <YAxis type="category" dataKey="typology" {...axisProps} axisLine={false} width={104} />
                <Tooltip cursor={{ fill: 'var(--grid)' }} content={tip((p) => p.typology, (p) => [
                  { label: 'Attempts', value: fmtInt(p.attempts), color: 'var(--s1)' },
                  { label: 'Transfers', value: fmtInt(p.transactions) },
                ])} />
                <Bar isAnimationActive={false} dataKey="attempts" fill="var(--s1)" radius={[0, 4, 4, 0]} maxBarSize={14}
                  label={{ position: 'right', fill: 'var(--ink-2)', fontSize: 11 }} />
              </BarChart>
            </ResponsiveContainer>
          </div>
        </ChartCard>

        <ChartCard title="What the model relies on" subtitle="Top 10 features by gain.">
          <div className="h-64">
            <ResponsiveContainer>
              <BarChart data={features} layout="vertical" margin={{ top: 0, right: 8, left: 8, bottom: 0 }}>
                <XAxis type="number" hide />
                <YAxis type="category" dataKey="feature" {...axisProps} axisLine={false} width={200} interval={0}
                  tickFormatter={(s: string) => (s.length > 32 ? s.slice(0, 31) + '…' : s)} />
                <Tooltip cursor={{ fill: 'var(--grid)' }} content={tip((p) => p.feature, (p) => [
                  { label: 'Gain', value: fmtCompact(p.gain), color: 'var(--s1)' },
                ])} />
                <Bar isAnimationActive={false} dataKey="gain" fill="var(--s1)" radius={[0, 4, 4, 0]} maxBarSize={12} />
              </BarChart>
            </ResponsiveContainer>
          </div>
        </ChartCard>
      </div>

      <p className="text-xs" style={{ color: 'var(--muted)' }}>
        Data: IBM synthetic AML dataset (HI-Small, CDLA-Sharing-1.0). Model trained on Sep 1–6, tuned on Sep 7–8,
        tested on Sep 9–10. Sep 11–18 excluded: normal activity stops on Sep 10 while laundering rows continue.
      </p>
    </div>
  )
}
