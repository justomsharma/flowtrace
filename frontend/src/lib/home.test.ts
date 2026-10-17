import { describe, expect, it } from 'vitest'
import { homeTarget, newestBatch } from './home'

describe('homeTarget', () => {
  it("opens the newest upload's Today's work when anything was uploaded", () => {
    expect(homeTarget('', 'BC0C047')).toBe('/today?batch=BC0C047')
    expect(homeTarget('?case=BC0C047-C005', 'BC0C047')).toBe('/today?batch=BC0C047&case=BC0C047-C005')
  })
  it('shows the upload box only when nothing was uploaded', () => {
    expect(homeTarget('', null)).toBeNull()
  })
  it('keeps a chosen batch and a fresh screen on the Live Network', () => {
    expect(homeTarget('?batch=OLD1', 'BC0C047')).toBeNull()
    expect(homeTarget('?fresh=1', 'BC0C047')).toBeNull()
  })
})

describe('newestBatch', () => {
  it('picks the latest upload', () => {
    expect(newestBatch([{ batch_id: 'A', uploaded_at: 1 }, { batch_id: 'B', uploaded_at: 3 }, { batch_id: 'C', uploaded_at: 2 }])).toBe('B')
    expect(newestBatch([])).toBeNull()
  })
})
