import { useMemo, useRef, useState, type MouseEvent } from 'react'
import { Landmark, Lock, User, type LucideIcon } from 'lucide-react'
import type { FlowEdge, FlowNode, Role } from '../../lib/api'
import { fmtUtc } from '../../lib/api'
import { edgeKey, layoutFlow } from '../../lib/flowLayout'
import { fmtMoney } from '../../lib/money'
import { fullId, useName } from '../../lib/names'

const R = 26
const ROLE: Record<Role, { fill: string; ink: string; Icon: LucideIcon; pillBg: string }> = {
  Sender: { fill: '#e6f0fc', ink: 'var(--s1)', Icon: User, pillBg: '#e6f0fc' },
  Intermediary: { fill: 'var(--violet-tint)', ink: 'var(--s7)', Icon: Landmark, pillBg: 'var(--violet-tint)' },
  Destination: { fill: '#fdece4', ink: 'var(--s2)', Icon: User, pillBg: '#fdece4' },
}
/** Plain names for the roles: what the account did with the money. */
export const ROLE_NAME: Record<Role, string> = { Sender: 'Sender', Intermediary: 'Middle', Destination: 'Receiver' }
const STATUS_NAME = { flagged: 'Suspicious', trail: 'Followed by the investigation', normal: 'Normal' }
const FLOW_COLOR = { flagged: 'var(--critical)', trail: 'var(--s1)', normal: '#9aa7b8', hold: 'var(--hold)' }
const edgeColor = (e: FlowEdge) => (e.hold ? 'hold' : e.status) as keyof typeof FLOW_COLOR

interface Tip { x: number; y: number; below: boolean; lines: string[] }

export default function FlowGraph({ nodes, edges, currency, rate }: { nodes: FlowNode[]; edges: FlowEdge[]; currency: string; rate: number }) {
  const m = (v: number) => fmtMoney(v, currency, rate)
  const name = useName()
  const L = useMemo(() => layoutFlow(nodes, edges, { colGap: 220, rowGap: 142, padX: 90, padY: 70 }), [nodes, edges])
  const [tip, setTip] = useState<Tip | null>(null)
  const box = useRef<HTMLDivElement>(null)
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
  const edgeLines = (e: FlowEdge) => [
    `${name(e.src)} → ${name(e.dst)}`,
    `${m(e.amount)} · ${e.count} payment${e.count === 1 ? '' : 's'} · ${fmtUtc(e.first_ts)}`,
    e.hold ? 'Into an account with a hold' : STATUS_NAME[e.status],
  ]
  const nodeLines = (n: FlowNode) => [
    `${name(n.id)} · ${ROLE_NAME[n.role]}${n.entity_type ? ` · ${n.entity_type}` : ''}`,
    fullId(n.id),
    n.role === 'Sender' ? `Sent ${m(n.sent)}` : n.role === 'Destination' ? `Received ${m(n.received)}` : `Received ${m(n.received)} · Sent ${m(n.sent)}`,
    ...(n.hold ? [n.hold === 'approved' ? 'Hold approved' : 'Hold proposed'] : n.in_case ? [] : ['Found by following the money']),
  ]
  // The tooltip follows the pointer and goes below it near the top edge, so it never covers what you point at.
  const show = (ev: MouseEvent, lines: string[]) => {
    const r = box.current?.getBoundingClientRect()
    if (!r) return
    const x = Math.min(Math.max(ev.clientX - r.left, 120), r.width - 120)
    const y = ev.clientY - r.top
    setTip({ x, y, below: y < 90, lines })
  }
  const showAt = (x: number, y: number, lines: string[]) => {
    const r = box.current?.getBoundingClientRect()
    if (!r) return
    setTip({ x: (x / W) * r.width, y: (y / H) * r.height, below: (y / H) * r.height < 90, lines })
  }
  const hide = () => setTip(null)

  return (
    <div ref={box} className="relative mx-auto w-full" style={{ maxWidth: Math.min(W, (W * 720) / H) }} onMouseLeave={hide}>
      <svg viewBox={`0 0 ${W} ${H}`} className="w-full h-auto block" role="group"
        aria-label={`Money flow between ${nodes.length} accounts: ${nodes.map((n) => `${name(n.id)} ${ROLE_NAME[n.role]}`).join(', ')}`}>
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
            <g key={edgeKey(e.src, e.dst)} onMouseMove={(ev) => show(ev, edgeLines(e))} onMouseLeave={hide}
              onFocus={() => showAt(mid.x, mid.y, edgeLines(e))} onBlur={hide}>
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
            <g key={n.id} tabIndex={0} role="img" aria-label={label} className="outline-none focus-visible:[&>circle:first-child]:stroke-[var(--focus)]"
              onMouseMove={(ev) => show(ev, nodeLines(n))} onMouseLeave={hide}
              onFocus={() => showAt(p.x, p.y - R, nodeLines(n))} onBlur={hide}>
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
              <text x={p.x} y={p.y + R + 18} textAnchor="middle" fontSize={14} fontWeight={600} fill="var(--ink)">{name(n.id).replace('Account ', '')}</text>
              <text x={p.x} y={p.y + R + 35} textAnchor="middle" fontSize={13} fill="var(--ink-2)" className="tabular">{m(n.amount)}</text>
              <RolePill x={p.x} y={p.y + R + 44} role={n.role} hold={n.hold} />
            </g>
          )
        })}
      </svg>

      {tip && (
        <div role="tooltip" className={`pointer-events-none absolute z-10 -translate-x-1/2 rounded-lg px-3 py-2 text-xs shadow-lg w-max max-w-[240px] ${tip.below ? '' : '-translate-y-full'}`}
          style={{ left: tip.x, top: tip.below ? tip.y + 18 : tip.y - 12, background: 'var(--ink)', color: '#fff' }}>
          {tip.lines.map((l, i) => <div key={i} className={i === 0 ? 'font-semibold' : ''} style={{ opacity: i === 0 ? 1 : 0.85 }}>{l}</div>)}
        </div>
      )}
    </div>
  )
}

function RolePill({ x, y, role, hold }: { x: number; y: number; role: Role; hold: FlowNode['hold'] }) {
  const text = hold ? `${ROLE_NAME[role]} · ${hold === 'approved' ? 'held' : 'hold?'}` : ROLE_NAME[role]
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
  const roles: [Role, string][] = [['Sender', ''], ['Intermediary', ''], ['Destination', '']]
  const lines: [keyof typeof FLOW_COLOR, string][] = [['flagged', 'Suspicious'], ['trail', 'Followed'], ['normal', 'Normal'], ['hold', 'Into a hold']]
  return (
    <ul className="flex flex-wrap gap-x-5 gap-y-2 text-xs" style={{ color: 'var(--ink-2)' }} aria-label="Legend">
      {roles.map(([r, note]) => (
        <li key={r} className="flex items-center gap-1.5">
          <span className="w-3 h-3 rounded-full" style={{ background: ROLE[r].ink }} aria-hidden />{ROLE_NAME[r]}{note}
        </li>
      ))}
      {lines.map(([k, l]) => (
        <li key={k} className="flex items-center gap-1.5">
          <svg width="22" height="8" aria-hidden><line x1="1" y1="4" x2="21" y2="4" stroke={FLOW_COLOR[k]} strokeWidth="2.5" strokeLinecap="round" strokeDasharray={k === 'normal' ? '4 4' : undefined} /></svg>{l}
        </li>
      ))}
      <li className="flex items-center gap-1.5">
        <svg width="16" height="16" aria-hidden><circle cx="8" cy="8" r="6" fill="none" stroke="var(--hold)" strokeWidth="2.5" strokeDasharray="3 2.5" /></svg>
        Hold
      </li>
    </ul>
  )
}
