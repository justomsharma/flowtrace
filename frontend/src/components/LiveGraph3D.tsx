import { useEffect, useRef } from 'react'
import ForceGraph3D from '3d-force-graph'

/** A payment on the map. `kind` decides the colour. */
export interface MapLink {
  tx_id: string
  source: string
  target: string
  amount: number
  kind: 'normal' | 'flagged' | 'focus' | 'traced' | 'concern'
}

const css = (v: string) => getComputedStyle(document.documentElement).getPropertyValue(v).trim() || '#888'

/* eslint-disable @typescript-eslint/no-explicit-any */
export default function LiveGraph3D({ links, focus, pulse }: {
  links: MapLink[]
  focus: string[]       // accounts to fly the camera to
  pulse: Set<string>    // tx_ids that should show moving particles (the latest step)
}) {
  const host = useRef<HTMLDivElement>(null)
  const g = useRef<any>(null)
  const nodes = useRef(new Map<string, any>())
  const linkMap = useRef(new Map<string, any>())
  const orbit = useRef(true)

  useEffect(() => {
    if (!host.current) return
    const el = host.current
    const col = { normal: css('--axis'), flagged: css('--s2'), focus: css('--s2'), traced: css('--s1'), concern: css('--s8'), node: css('--ink-2') }
    const graph = (ForceGraph3D as any)()(el)
      .backgroundColor(css('--page'))
      .showNavInfo(false)
      .width(el.clientWidth).height(el.clientHeight)
      .nodeRelSize(2.6).nodeOpacity(0.95).linkOpacity(0.6)
      .nodeColor((n: any) => n.hot ? col.focus : n.flag ? col.flagged : col.node)
      .nodeVal((n: any) => (n.hot ? 4 : n.flag ? 1.6 : 0.6))
      .linkColor((l: any) => col[l.kind as keyof typeof col] ?? col.normal)
      .linkWidth((l: any) => (l.kind === 'normal' ? 0 : l.kind === 'flagged' ? 0.6 : 1.6))
      .linkDirectionalParticleWidth(2.4).linkDirectionalParticleSpeed(0.014)
      .linkDirectionalParticleColor((l: any) => col[l.kind as keyof typeof col] ?? col.flagged)
      .nodeLabel((n: any) => `<div style="font:12px system-ui;padding:3px 6px;background:${css('--surface-2')};color:${css('--ink')};border-radius:6px">account …${n.id.split(':')[1]?.slice(-5) ?? n.id}</div>`)
      .graphData({ nodes: [], links: [] })
    graph.d3Force('charge').strength(-14)
    el.addEventListener('pointerdown', () => { orbit.current = false })
    g.current = graph

    let raf = 0
    const start = performance.now()
    const spin = (now: number) => {
      raf = requestAnimationFrame(spin)
      if (!orbit.current) return
      const a = (now - start) * 0.00005
      graph.cameraPosition({ x: 520 * Math.sin(a), z: 520 * Math.cos(a), y: 40 })
    }
    raf = requestAnimationFrame(spin)
    const ro = new ResizeObserver(() => graph.width(el.clientWidth).height(el.clientHeight))
    ro.observe(el)
    return () => {
      cancelAnimationFrame(raf)
      ro.disconnect()
      graph._destructor?.()
      el.innerHTML = ''
      nodes.current.clear()
      linkMap.current.clear()
    }
  }, [])

  // Merge links in place so existing nodes keep their positions.
  useEffect(() => {
    const graph = g.current
    if (!graph) return
    let changed = false
    const seen = new Set<string>()
    for (const l of links) {
      seen.add(l.tx_id)
      for (const id of [l.source, l.target]) {
        if (!nodes.current.has(id)) {
          nodes.current.set(id, { id })
          changed = true
        }
      }
      const cur = linkMap.current.get(l.tx_id)
      if (!cur) {
        linkMap.current.set(l.tx_id, { ...l })
        changed = true
      } else if (cur.kind !== l.kind) {
        cur.kind = l.kind
      }
    }
    for (const [id] of linkMap.current) if (!seen.has(id)) { linkMap.current.delete(id); changed = true }
    const hot = new Set(focus)
    const flagged = new Set<string>()
    for (const l of links) if (l.kind !== 'normal') { flagged.add(l.source); flagged.add(l.target) }
    for (const n of nodes.current.values()) { n.hot = hot.has(n.id); n.flag = flagged.has(n.id) }
    if (changed) {
      const used = new Set<string>()
      for (const l of linkMap.current.values()) {
        used.add(typeof l.source === 'object' ? l.source.id : l.source)
        used.add(typeof l.target === 'object' ? l.target.id : l.target)
      }
      for (const id of [...nodes.current.keys()]) if (!used.has(id)) nodes.current.delete(id)
      graph.graphData({ nodes: [...nodes.current.values()], links: [...linkMap.current.values()] })
    } else {
      graph.nodeColor(graph.nodeColor()).linkColor(graph.linkColor()).linkWidth(graph.linkWidth()).nodeVal(graph.nodeVal())
    }
    graph.linkDirectionalParticles((l: any) => (pulse.has(l.tx_id) ? 3 : 0))
  }, [links, focus, pulse])

  // Fly to the focused accounts.
  useEffect(() => {
    const graph = g.current
    if (!graph || !focus.length) return
    const t = window.setTimeout(() => {
      const pts = focus.map((id) => nodes.current.get(id)).filter((n) => n && n.x !== undefined)
      if (!pts.length) return
      orbit.current = false
      const c = pts.reduce((a, n) => ({ x: a.x + n.x / pts.length, y: a.y + n.y / pts.length, z: a.z + n.z / pts.length }), { x: 0, y: 0, z: 0 })
      graph.cameraPosition({ x: c.x + 120, y: c.y + 60, z: c.z + 160 }, c, 1200)
    }, 600)
    return () => window.clearTimeout(t)
  }, [focus])

  return <div ref={host} className="absolute inset-0" role="img" aria-label="3D map of the uploaded payments" />
}
