/** Left-to-right layered layout for a case's money flow (a handful of accounts, so no layout library).
 *  1. Break cycles: a depth-first walk, started from accounts with no inflow, marks edges that point back
 *     into the walk ("back edges"); they are drawn curved and ignored for layering.
 *  2. Longest-path layers: each account sits one layer right of the furthest account paying it.
 *  3. Order within a layer by the average position of its neighbours (one sweep down, one up) to cut crossings.
 *  4. Centre every layer vertically on the tallest one. */

export interface LayoutOpts { colGap?: number; rowGap?: number; padX?: number; padY?: number }
export interface Placed { x: number; y: number; layer: number }
export interface Layout { pos: Record<string, Placed>; width: number; height: number; back: Set<string> }

export const edgeKey = (src: string, dst: string) => `${src}>${dst}`

export function layoutFlow(nodes: { id: string }[], edges: { src: string; dst: string }[], opts: LayoutOpts = {}): Layout {
  const { colGap = 190, rowGap = 96, padX = 70, padY = 56 } = opts
  const ids = nodes.map((n) => n.id)
  const known = new Set(ids)
  const out = new Map<string, string[]>(ids.map((id) => [id, []]))
  const inDeg = new Map<string, number>(ids.map((id) => [id, 0]))
  for (const { src, dst } of edges) {
    if (src === dst || !known.has(src) || !known.has(dst) || out.get(src)!.includes(dst)) continue
    out.get(src)!.push(dst)
    inDeg.set(dst, inDeg.get(dst)! + 1)
  }

  // 1. Back edges.
  const back = new Set<string>()
  const state = new Map<string, 'open' | 'done'>()
  const visit = (u: string) => {
    state.set(u, 'open')
    for (const v of out.get(u)!) {
      if (state.get(v) === 'open') back.add(edgeKey(u, v))
      else if (!state.has(v)) visit(v)
    }
    state.set(u, 'done')
  }
  for (const id of [...ids.filter((i) => inDeg.get(i) === 0), ...ids]) if (!state.has(id)) visit(id)

  // 2. Longest-path layers over the forward edges (Kahn's order).
  const fwd = new Map(ids.map((id) => [id, out.get(id)!.filter((v) => !back.has(edgeKey(id, v)))]))
  const preds = new Map<string, string[]>(ids.map((id) => [id, []]))
  for (const [u, vs] of fwd) for (const v of vs) preds.get(v)!.push(u)
  const pending = new Map(ids.map((id) => [id, preds.get(id)!.length]))
  const layer = new Map<string, number>()
  const queue = ids.filter((id) => pending.get(id) === 0)
  for (let i = 0; i < queue.length; i++) {
    const u = queue[i]
    const l = Math.max(0, ...preds.get(u)!.map((p) => layer.get(p)! + 1))
    layer.set(u, l)
    for (const v of fwd.get(u)!) {
      pending.set(v, pending.get(v)! - 1)
      if (pending.get(v) === 0) queue.push(v)
    }
  }

  // 3. Order within layers.
  const count = Math.max(0, ...layer.values()) + 1
  const layers: string[][] = Array.from({ length: ids.length ? count : 0 }, () => [])
  for (const id of ids) layers[layer.get(id)!].push(id)
  const succs = fwd
  const sweep = (from: number, to: number, step: number, nbrs: Map<string, string[]>) => {
    for (let l = from; l !== to; l += step) {
      const ref = new Map(layers[l - step].map((id, i) => [id, i]))
      const at = new Map(layers[l].map((id, i) => [id, i]))
      const score = (id: string) => {
        const ns = nbrs.get(id)!.filter((x) => ref.has(x))
        return ns.length ? ns.reduce((s, x) => s + ref.get(x)!, 0) / ns.length : at.get(id)!
      }
      layers[l] = [...layers[l]].sort((a, b) => score(a) - score(b) || at.get(a)! - at.get(b)!)
    }
  }
  if (layers.length > 1) {
    sweep(1, layers.length, 1, preds)
    sweep(layers.length - 2, -1, -1, succs)
  }

  // 4. Coordinates.
  const tallest = Math.max(1, ...layers.map((l) => l.length))
  const pos: Record<string, Placed> = {}
  layers.forEach((col, l) => col.forEach((id, i) => {
    pos[id] = { x: padX + l * colGap, y: padY + ((tallest - col.length) / 2 + i) * rowGap, layer: l }
  }))
  return { pos, width: 2 * padX + Math.max(0, layers.length - 1) * colGap, height: 2 * padY + (tallest - 1) * rowGap, back }
}
