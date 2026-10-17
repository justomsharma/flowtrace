import { describe, expect, it } from 'vitest'
import { caseSteps } from './stepper'

const states = (s: ReturnType<typeof caseSteps>) => s.map((x) => x.state)

describe('caseSteps', () => {
  it('has the five steps in order', () => {
    expect(caseSteps({ status: 'not_started', hasResult: false, actions: [], liveSteps: 0 }).map((s) => s.label))
      .toEqual(['Alert received', 'Initial analysis', 'Graph investigation', 'Report', 'Analyst review'])
  })

  it('not started: alert and scoring done, the rest to do', () => {
    expect(states(caseSteps({ status: 'not_started', hasResult: false, actions: [], liveSteps: 0 })))
      .toEqual(['done', 'done', 'todo', 'todo', 'todo'])
  })

  it('queued and running investigations are the active step', () => {
    const q = caseSteps({ status: 'queued', hasResult: false, actions: [], liveSteps: 0 })
    expect(q[2]).toMatchObject({ state: 'active', note: 'Queued' })
    const r = caseSteps({ status: 'investigating', hasResult: false, actions: [], liveSteps: 3 })
    expect(r[2]).toMatchObject({ state: 'active', note: '3 steps so far' })
  })

  it('a re-run makes the investigation active again even with an old result', () => {
    expect(states(caseSteps({ status: 'investigating', hasResult: true, actions: [], liveSteps: 1 })))
      .toEqual(['done', 'done', 'active', 'todo', 'todo'])
  })

  it('failed investigation is marked failed', () => {
    expect(caseSteps({ status: 'failed', hasResult: false, actions: [], liveSteps: 0 })[2].state).toBe('failed')
  })

  it('open proposals make the analyst review active', () => {
    const s = caseSteps({ status: 'escalate', hasResult: true, actions: [{ status: 'PROPOSED' }, { status: 'PROPOSED' }, { status: 'APPROVED' }], liveSteps: 0 })
    expect(states(s)).toEqual(['done', 'done', 'done', 'done', 'active'])
    expect(s[4].note).toBe('2 waiting for a decision')
  })

  it('a provisional auto-hold still waits for a person', () => {
    const s = caseSteps({ status: 'escalate', hasResult: true, actions: [{ status: 'PROVISIONAL' }, { status: 'APPROVED' }], liveSteps: 0 })
    expect(s[4]).toMatchObject({ state: 'active', note: '1 waiting for a decision' })
  })

  it('everything decided completes the review', () => {
    const s = caseSteps({ status: 'escalate', hasResult: true, actions: [{ status: 'APPROVED' }, { status: 'REJECTED' }], liveSteps: 0 })
    expect(s[4]).toMatchObject({ state: 'done', note: 'Decided' })
  })

  it('a result with nothing proposed still waits for a person', () => {
    expect(caseSteps({ status: 'review', hasResult: true, actions: [], liveSteps: 0 })[4]).toMatchObject({ state: 'active', note: 'Waiting for an analyst' })
  })
})
