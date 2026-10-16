import { describe, expect, it } from 'vitest'
import { holdPromise, isAutoHold } from './holds'
import { businessEnd } from './time'
import { fmtOriginal } from './money'

describe('holdPromise', () => {
  it('promises nothing is frozen when auto-hold is off or approvals are not set up', () => {
    expect(holdPromise(null)).toBe('Nothing is frozen until you approve.')
    expect(holdPromise({ active: false, min_confidence: 0.9, confirm_business_days: 1 })).toBe('Nothing is frozen until you approve.')
  })
  it('states exactly when auto-hold acts', () => {
    const s = holdPromise({ active: true, min_confidence: 0.9, confirm_business_days: 1 })
    expect(s).toContain('confidence score of 0.90 or more')
    expect(s).toContain('provisionally for 1 business day.')
    expect(s).toContain('unconfirmed, it is released')
  })
})

describe('isAutoHold', () => {
  it('knows provisional holds and holds the agent placed', () => {
    expect(isAutoHold({ status: 'PROVISIONAL' })).toBe(true)
    expect(isAutoHold({ status: 'APPROVED', held_by: 'agent (auto-hold)' })).toBe(true)
    expect(isAutoHold({ status: 'APPROVED' })).toBe(false)
  })
})

describe('businessEnd', () => {
  it('counts business days like the server: Friday + 4 ends Thursday night UTC', () => {
    const fri = Date.UTC(2026, 9, 16, 9, 30)
    expect(businessEnd(fri, 4)).toBe(Date.UTC(2026, 9, 22, 23, 59, 59, 999))
    expect(businessEnd(fri, 1)).toBe(Date.UTC(2026, 9, 19, 23, 59, 59, 999))  // Friday + 1 is Monday
  })
})

describe('fmtOriginal', () => {
  it('shows a payment in its own currency', () => {
    expect(fmtOriginal(1050.4, 'Euro')).toBe('EUR 1,050')
    expect(fmtOriginal(0.04123, 'Bitcoin')).toBe('BTC 0.0412')
    expect(fmtOriginal(null, 'Euro')).toBe('')
  })
})
