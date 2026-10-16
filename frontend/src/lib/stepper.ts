/** The case's progress, mapped to real states: the case exists (alert), it was scored, the agent investigates
 *  the graph, the report is written, and a person decides on what the agent proposed. */

export type CaseState = 'not_started' | 'queued' | 'investigating' | 'failed' | 'escalate' | 'review' | 'close'
export type StepState = 'done' | 'active' | 'todo' | 'failed'
export interface CaseStep { key: string; label: string; state: StepState; note: string }

export function caseSteps(i: { status: CaseState; hasResult: boolean; actions: { status: string }[]; liveSteps: number }): CaseStep[] {
  const running = i.status === 'queued' || i.status === 'investigating'
  const finished = i.hasResult && !running
  const open = i.actions.filter((a) => a.status === 'PROPOSED').length
  const plural = (n: number, w: string) => `${n} ${w}${n === 1 ? '' : 's'}`

  const graph: Pick<CaseStep, 'state' | 'note'> =
    i.status === 'queued' ? { state: 'active', note: 'Queued' }
    : i.status === 'investigating' ? { state: 'active', note: i.liveSteps ? `${plural(i.liveSteps, 'step')} so far` : 'Starting' }
    : i.status === 'failed' && !i.hasResult ? { state: 'failed', note: 'Failed: run it again' }
    : finished ? { state: 'done', note: 'Done' }
    : { state: 'todo', note: 'Not started' }

  const review: Pick<CaseStep, 'state' | 'note'> =
    !finished ? { state: 'todo', note: 'Pending' }
    : open > 0 ? { state: 'active', note: `${open} waiting for a decision` }
    : i.actions.length > 0 ? { state: 'done', note: 'Decided' }
    : { state: 'active', note: 'Waiting for an analyst' }

  return [
    { key: 'alert', label: 'Alert received', state: 'done', note: 'Case opened' },
    { key: 'scored', label: 'Initial analysis', state: 'done', note: 'Scored' },
    { key: 'graph', label: 'Graph investigation', ...graph },
    { key: 'report', label: 'Report', ...(finished ? { state: 'done', note: 'Written' } : { state: 'todo', note: 'Pending' }) },
    { key: 'review', label: 'Analyst review', ...review },
  ]
}
