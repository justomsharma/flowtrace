import cytoscape, { type Core, type ElementDefinition } from 'cytoscape'
import { useEffect, useRef } from 'react'
import type { Edge, GNode } from '../lib/api'

/** Edge colour by why it is on screen. Text never uses these colours; the legend names them. */
export const EDGE_ROLES: { role: string; label: string; color: string }[] = [
  { role: 'alert', label: 'Alerted transfer', color: 'var(--s2)' },
  { role: 'trace', label: 'Found by money trace', color: 'var(--s1)' },
  { role: 'cycle', label: 'Part of a loop', color: 'var(--s8)' },
  { role: 'context', label: 'Context', color: 'var(--muted)' },
]

function cssVar(name: string): string {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim() || '#888'
}

function roleColor(role?: string) {
  const r = EDGE_ROLES.find((x) => x.role === role) ?? EDGE_ROLES[3]
  return cssVar(r.color.slice(4, -1))
}

export default function GraphCanvas({
  nodes,
  edges,
  primary,
  focus,
  height = 420,
}: {
  nodes: GNode[]
  edges: Edge[]
  primary?: string
  focus?: string[]
  height?: number
}) {
  const ref = useRef<HTMLDivElement>(null)
  const cy = useRef<Core | null>(null)
  const layoutTimer = useRef<number | null>(null)

  useEffect(() => {
    if (!ref.current) return
    cy.current = cytoscape({
      container: ref.current,
      wheelSensitivity: 0.2,
      style: [
        {
          selector: 'node',
          style: {
            width: 14, height: 14,
            'background-color': cssVar('--surface-2'),
            'border-width': 2, 'border-color': cssVar('--axis'),
            label: 'data(label)', 'font-size': 8, color: cssVar('--muted'),
            'text-valign': 'bottom', 'text-margin-y': 3,
          },
        },
        { selector: 'node[role="subject"]', style: { 'background-color': cssVar('--ink-2'), 'border-color': cssVar('--ink') } },
        { selector: 'node[?primary]', style: { width: 22, height: 22, 'background-color': cssVar('--s2'), 'border-color': cssVar('--ink'), color: cssVar('--ink'), 'font-size': 10 } },
        { selector: 'node.focus', style: { 'border-color': cssVar('--warning'), 'border-width': 3 } },
        {
          selector: 'edge',
          style: {
            width: 'data(w)', 'line-color': 'data(color)', 'target-arrow-color': 'data(color)',
            'target-arrow-shape': 'triangle', 'arrow-scale': 0.7, 'curve-style': 'bezier', opacity: 0.85,
          },
        },
        { selector: '.fresh', style: { opacity: 0 } },
      ],
      layout: { name: 'grid' },
    })
    return () => cy.current?.destroy()
  }, [])

  useEffect(() => {
    const c = cy.current
    if (!c) return
    const add: ElementDefinition[] = []
    for (const n of nodes) {
      if (c.getElementById(n.id).nonempty()) {
        c.getElementById(n.id).data('role', n.role)
        continue
      }
      add.push({ group: 'nodes', data: { id: n.id, role: n.role, label: n.id.split(':')[1]?.slice(-5) ?? n.id, primary: n.id === primary } })
    }
    const nodeIds = new Set([...nodes.map((n) => n.id), ...c.nodes().map((n) => n.id())])
    for (const e of edges) {
      for (const a of [e.src, e.dst]) {
        if (!nodeIds.has(a)) {
          nodeIds.add(a)
          add.push({ group: 'nodes', data: { id: a, role: 'related', label: a.split(':')[1]?.slice(-5) ?? a } })
        }
      }
      if (c.getElementById(e.tx_id).nonempty()) continue
      add.push({
        group: 'edges',
        data: {
          id: e.tx_id, source: e.src, target: e.dst, color: roleColor(e.role),
          w: Math.max(1, Math.min(5, Math.log10(Math.max(e.amount, 10)) - 1)),
        },
      })
    }
    if (!add.length) return
    const added = c.add(add)
    added.addClass('fresh')
    added.animate({ style: { opacity: 1 } }, { duration: 450, complete: () => added.removeClass('fresh') })
    if (layoutTimer.current) window.clearTimeout(layoutTimer.current)
    layoutTimer.current = window.setTimeout(() => {
      c.layout({ name: 'cose', animate: true, animationDuration: 400, nodeRepulsion: () => 6000, idealEdgeLength: () => 60, randomize: false, fit: true, padding: 20 }).run()
    }, 150)
  }, [nodes, edges, primary])

  useEffect(() => {
    const c = cy.current
    if (!c) return
    c.nodes().removeClass('focus')
    for (const id of focus ?? []) c.getElementById(id).addClass('focus')
  }, [focus])

  return (
    <div className="relative">
      <div ref={ref} style={{ height }} className="w-full rounded-lg" aria-label="Money-flow graph" role="img" />
      <div className="flex flex-wrap gap-3 text-xs mt-2" style={{ color: 'var(--ink-2)' }}>
        {EDGE_ROLES.map((r) => (
          <span key={r.role} className="flex items-center gap-1.5">
            <span className="w-3 h-0.5" style={{ background: r.color }} />
            {r.label}
          </span>
        ))}
        <span className="flex items-center gap-1.5">
          <span className="w-2.5 h-2.5 rounded-full" style={{ background: 'var(--s2)', border: '1px solid var(--ink)' }} />
          Primary account
        </span>
      </div>
    </div>
  )
}
