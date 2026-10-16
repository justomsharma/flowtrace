import { describe, expect, it } from 'vitest'
import { layoutFlow } from './flowLayout'

const n = (...ids: string[]) => ids.map((id) => ({ id }))
const e = (...pairs: string[]) => pairs.map((p) => { const [src, dst] = p.split('>'); return { src, dst } })

describe('layoutFlow', () => {
  it('puts a chain in three layers, left to right, on one row', () => {
    const { pos, back } = layoutFlow(n('A', 'B', 'C'), e('A>B', 'B>C'))
    expect([pos.A.layer, pos.B.layer, pos.C.layer]).toEqual([0, 1, 2])
    expect(pos.A.x < pos.B.x && pos.B.x < pos.C.x).toBe(true)
    expect(pos.A.y).toBe(pos.C.y)
    expect(back.size).toBe(0)
  })

  it('breaks a pure cycle with one back edge and places every node', () => {
    const { pos, back } = layoutFlow(n('A', 'B', 'C'), e('A>B', 'B>C', 'C>A'))
    expect(Object.keys(pos).sort()).toEqual(['A', 'B', 'C'])
    expect([...back]).toEqual(['C>A'])
    expect([pos.A.layer, pos.B.layer, pos.C.layer]).toEqual([0, 1, 2])
  })

  it('handles a two-account round trip', () => {
    const { pos, back } = layoutFlow(n('A', 'B'), e('A>B', 'B>A'))
    expect(back.size).toBe(1)
    expect(pos.A.layer).not.toBe(pos.B.layer)
  })

  it('fans out: one sender, five receivers, sender centred on them', () => {
    const { pos } = layoutFlow(n('A', 'B', 'C', 'D', 'E', 'F'), e('A>B', 'A>C', 'A>D', 'A>E', 'A>F'))
    const ys = ['B', 'C', 'D', 'E', 'F'].map((k) => pos[k].y)
    expect(['B', 'C', 'D', 'E', 'F'].every((k) => pos[k].layer === 1)).toBe(true)
    expect(pos.A.y).toBeCloseTo((Math.min(...ys) + Math.max(...ys)) / 2)
  })

  it('starts from accounts with no inflow even when they come later in the list', () => {
    const { pos } = layoutFlow(n('B', 'C', 'A'), e('A>B', 'B>C', 'C>B'))
    expect(pos.A.layer).toBe(0)
    expect(pos.B.layer).toBe(1)
  })

  it('ignores self-transfers and edges to unknown accounts', () => {
    const { pos, back } = layoutFlow(n('A', 'B'), e('A>A', 'A>B', 'B>Z'))
    expect(back.size).toBe(0)
    expect(pos.Z).toBeUndefined()
    expect(pos.B.layer).toBe(1)
  })

  it('orders a layer to avoid crossings', () => {
    const { pos } = layoutFlow(n('A', 'B', 'D', 'C'), e('A>C', 'B>D'))
    expect(pos.A.y < pos.B.y).toBe(true)
    expect(pos.C.y < pos.D.y).toBe(true)
  })

  it('sizes the canvas to the layers', () => {
    const one = layoutFlow(n('A', 'B'), e('A>B'), { colGap: 200, rowGap: 100, padX: 50, padY: 40 })
    expect(one.width).toBe(300)
    expect(one.height).toBe(80)
    const tall = layoutFlow(n('A', 'B', 'C', 'D'), e('A>B', 'A>C', 'A>D'), { colGap: 200, rowGap: 100, padX: 50, padY: 40 })
    expect(tall.height).toBe(280)
  })
})
