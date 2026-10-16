import { useMemo, useState } from 'react'
import { Landmark, Lock, User, type LucideIcon } from 'lucide-react'
import type { FlowEdge, FlowNode, Role } from '../../lib/api'
import { fmtUtc } from '../../lib/api'
import { edgeKey, layoutFlow } from '../../lib/flowLayout'
import { fmtMoney } from '../../lib/money'
import { shortAcct } from '../../lib/plain'

const R = 26
const ROLE: Record<Role, { fill: string; ink: string; Icon: LucideIcon; pillBg: string }> = {
  Sender: { fill: '#e6f0fc', ink: 'var(--s1)', Icon: User, pillBg: '#e6f0fc' },
  Intermediary: { fill: 'var(--violet-tint)', ink: 'var(--s7)', Icon: Landmark, pillBg: 'var(--violet-tint)' },
  Destination: { fill: '#fdece4', ink: 'var(--s2)', Icon: User, pillBg: '#fdece4' },
}
const FLOW_COLOR = { flagged: 'var(--critical)', trail: 'var(--s1)', normal: '#9aa7b8', hold: 'var(--hold)' }
const edgeColor = (e: FlowEdge) => (e.hold ? 'hold' : e.status) as keyof typeof FLOW_COLOR

interface Tip { x: number; y: number; lines: string[] }

export default function FlowGraph({ nodes, edges, currency, rate }: { nodes: FlowNode[]; edges: FlowEdge[]; currency: string; rate: number }) {
  const m = (v: number) => fmtMoney(v, currency, rate)
  const L = useMemo(() => layoutFlow(nodes, edges, { colGap: 220, rowGap: 142, padX: 90, padY: 70 }), [nodes, edges])
  const [tip, setTip] = useState<Tip | null>(null)
  const max = Math.max(1, ...edges.map((e) => e.amount))
  const W = Math.max(L.width, 360)
  const H = L.height + 64  // room for the labels under the bottom row
  const off = (W - L.width) / 2

  const pos = (id: string) => ({ x: L.pos[id].x + off, y: L.pos[id].y })
  const holdOf = new Map(nodes.map((n) => [n.id, n.hold]))
  const gapAt = (id: string) => R + (holdOf.get(id) ? 11 : 6)  // arrows stop outside a hold's ring
  const path = (e: FlowEdge) => {
    const a = pos(e.src), b = pos(e.dst)
    if (L.back.has(edgeKey(e.src, e.dst)) || L.pos[e.dst].layer <= L.pos[e.src].layer) {
      // Money going back (a loop) or within a layer: an arc bowed to one side of the straight line, so the
      // pair A→B, B→A reads as two separate flows.
      const dx = b.x - a.x, dy = b.y - a.y, len = Math.hypot(dx, dy) || 1
      const ux = dx / len, uy = dy / len
      const bulge = 46 + 0.12 * len
      const cx = (a.x + b.x) / 2 - uy * bulge, cy = (a.y + b.y) / 2 + ux * bulge
      const s = { x: a.x + (cx - a.x) / Math.hypot(cx - a.x, cy - a.y) * R, y: a.y + (cy - a.y) / Math.hypot(cx - a.x, cy - a.y) * R }
      const t = { x: b.x + (cx - b.x) / Math.hypot(cx - b.x, cy - b.y) * gapAt(e.dst), y: b.y + (cy - b.y) / Math.hypot(cx - b.x, cy - b.y) * gapAt(e.dst) }
      return `M ${s.x} ${s.y} Q ${cx} ${cy}, ${t.x} ${t.y}`
    }
    const x1 = a.x + R, x2 = b.x - gapAt(e.dst), mx = (x1 + x2) / 2
    return `M ${x1} ${a.y} C ${mx} ${a.y}, ${mx} ${b.y}, ${x2} ${b.y}`
  }
  const span = (e: FlowEdge) => e.first_ts === e.last_ts ? fmtUtc(e.first_ts) : `${fmtUtc(e.first_ts)} to ${fmtUtc(e.last_ts, false)}`
  const edgeLines = (e: FlowEdge) => [
    `${shortAcct(e.src)} → ${shortAcct(e.dst)}`,
    `${m(e.amount)} in ${e.count} payment${e.count === 1 ? '' : 's'}`,
    span(e),
    e.hold ? 'Carried case money into a hold account' : e.status === 'flagged' ? 'Flagged' : e.status === 'trail' ? 'Followed by the investigation' : 'Normal',
  ]
  const nodeLines = (n: FlowNode) => [
    `Account ${n.id}${n.entity_type ? ` (${n.entity_type})` : ''}`,
    `${n.role}: received ${m(n.received)}, sent ${m(n.sent)} in this flow`,
    n.in_case ? 'In the case' : 'Found by following the money',
    ...(n.hold ? [n.hold === 'approved' ? 'Hold approved' : 'Hold proposed, waiting for a decision'] : []),
  ]
  const show = (x: number, y: number, lines: string[]) => setTip({ x: (x / W) * 100, y: (y / H) * 100, lines })

  return (
    <div className="relative mx-auto w-full" style={{ maxWidth: Math.min(W, (W * 560) / H) }} onMouseLeave={() => setTip(null)}>
      <svg viewBox={`0 0 ${W} ${H}`} className="w-full h-auto block" role="img"
        aria-label={`Money flow between ${nodes.length} accounts: ${nodes.map((n) => `${shortAcct(n.id)} ${n.role}`).join(', ')}`}>
        <defs>
          {Object.entries(FLOW_COLOR).map(([k, c]) => (
            <marker key={k} id={`arrow-${k}`} viewBox="0 0 10 10" refX="8" refY="5" markerWidth="11" markerHeight="11" markerUnits="userSpaceOnUse" orient="auto-start-reverse">
              <path d="M 0 0 L 10 5 L 0 10 z" fill={c} />
            </marker>
          ))}
        </defs>

        {edges.map((e) => {
          const k = edgeColor(e)
          const d = path(e)
          const mid = { x: (pos(e.src).x + pos(e.dst).x) / 2, y: (pos(e.src).y + pos(e.dst).y) / 2 }
          return (
            <g key={edgeKey(e.src, e.dst)} onMouseEnter={() => show(mid.x, mid.y, edgeLines(e))}>
              <path d={d} fill="none" stroke="transparent" strokeWidth={14} />
              <path d={d} fill="none" stroke={FLOW_COLOR[k]} strokeWidth={1.5 + 2.5 * Math.sqrt(e.amount / max)}
                strokeDasharray={e.status === 'normal' && !e.hold ? '4 4' : undefined} markerEnd={`url(#arrow-${k})`} strokeLinecap="round" />
            </g>
          )
        })}

        {nodes.map((n) => {
          const p = pos(n.id)
          const r = ROLE[n.role]
          const label = nodeLines(n).join('. ')
          return (
            <g key={n.id} tabIndex={0} aria-label={label} className="outline-none focus-visible:[&>circle:first-child]:stroke-[var(--focus)]"
              onMouseEnter={() => show(p.x, p.y - R, nodeLines(n))} onFocus={() => show(p.x, p.y - R, nodeLines(n))} onBlur={() => setTip(null)}>
              <circle cx={p.x} cy={p.y} r={R + 7} fill="none" stroke="transparent" strokeWidth={3} />
              {n.hold && (
                <circle cx={p.x} cy={p.y} r={R + 5} fill="none" stroke="var(--hold)" strokeWidth={3}
                  strokeDasharray={n.hold === 'proposed' ? '5 4' : undefined} />
              )}
              <circle cx={p.x} cy={p.y} r={R} fill={r.fill} stroke={r.ink} strokeOpacity={n.in_case ? 0.5 : 0.25} strokeWidth={1.5} />
              <r.Icon x={p.x - 11} y={p.y - 11} width={22} height={22} color={r.ink} aria-hidden />
              {n.hold && (
                <g aria-hidden>
                  <circle cx={p.x + R - 2} cy={p.y - R + 2} r={10} fill="var(--hold)" stroke="var(--surface)" strokeWidth={2} />
                  <Lock x={p.x + R - 8} y={p.y - R - 4} width={12} height={12} color="#fff" />
                </g>
              )}
              <text x={p.x} y={p.y + R + 18} textAnchor="middle" fontSize={14} fontWeight={600} fill="var(--ink)">{shortAcct(n.id)}</text>
              <text x={p.x} y={p.y + R + 35} textAnchor="middle" fontSize={13} fill="var(--ink-2)" className="tabular">{m(n.amount)}</text>
              <RolePill x={p.x} y={p.y + R + 44} role={n.role} hold={n.hold} />
            </g>
          )
        })}
      </svg>

      {tip && (
        <div role="tooltip" className="pointer-events-none absolute z-10 -translate-x-1/2 -translate-y-full rounded-lg px-3 py-2 text-xs shadow-lg max-w-[280px]"
          style={{ left: `${tip.x}%`, top: `calc(${tip.y}% - 8px)`, background: 'var(--ink)', color: '#fff' }}>
          {tip.lines.map((l, i) => <div key={i} className={i === 0 ? 'font-semibold' : ''} style={{ opacity: i === 0 ? 1 : 0.85 }}>{l}</div>)}
        </div>
      )}
    </div>
  )
}

function RolePill({ x, y, role, hold }: { x: number; y: number; role: Role; hold: FlowNode['hold'] }) {
  const text = hold ? `${role} · ${hold === 'approved' ? 'held' : 'hold proposed'}` : role
  const w = text.length * 6.6 + 16
  const r = ROLE[role]
  return (
    <g aria-hidden>
      <rect x={x - w / 2} y={y} width={w} height={20} rx={6} fill={hold ? 'var(--hold-tint)' : r.pillBg} />
      <text x={x} y={y + 14} textAnchor="middle" fontSize={11.5} fontWeight={500} fill={hold ? '#94620a' : r.ink}>{text}</text>
    </g>
  )
}

export function FlowLegend() {
  const roles: [Role, string][] = [['Sender', 'no money in'], ['Intermediary', 'in and out'], ['Destination', 'no money out']]
  const lines: [keyof typeof FLOW_COLOR, string][] = [['flagged', 'Suspicious flow'], ['hold', 'Into a proposed hold'], ['trail', 'Money followed onward']]
  return (
    <ul className="flex flex-wrap gap-x-5 gap-y-2 text-xs" style={{ color: 'var(--ink-2)' }} aria-label="Legend">
      {roles.map(([r, note]) => (
        <li key={r} className="flex items-center gap-1.5">
          <span className="w-3 h-3 rounded-full" style={{ background: ROLE[r].ink }} aria-hidden />{r} <span style={{ color: 'var(--muted)' }}>({note})</span>
        </li>
      ))}
      {lines.map(([k, l]) => (
        <li key={k} className="flex items-center gap-1.5">
          <svg width="22" height="8" aria-hidden><line x1="1" y1="4" x2="21" y2="4" stroke={FLOW_COLOR[k]} strokeWidth="2.5" strokeLinecap="round" /></svg>{l}
        </li>
      ))}
      <li className="flex items-center gap-1.5">
        <svg width="16" height="16" aria-hidden><circle cx="8" cy="8" r="6" fill="none" stroke="var(--hold)" strokeWidth="2.5" strokeDasharray="3 2.5" /></svg>
        Hold proposed (solid when approved)
      </li>
    </ul>
  )
}
