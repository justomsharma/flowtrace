import { useEffect, useState, type ReactNode } from 'react'
import { Link } from 'react-router-dom'
import { Download, FileText } from 'lucide-react'
import { ActionBadge } from '../components/badges'
import { useBatch } from '../lib/batch'
import { renderMarkdown, type Block, type Seg } from '../lib/markdown'

const caseNo = (id: string) => id.slice(id.lastIndexOf('-') + 1)

/** STR drafts: one per investigated case, written from the investigation, for a person to review before filing. */
export default function Reports() {
  const { batch, caseId, query } = useBatch()
  const done = (batch?.cases ?? []).filter((c) => c.result)
  const open = done.find((c) => c.case_id === caseId) ?? done[0] ?? null
  const openId = open?.case_id
  const [md, setMd] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    setMd(null); setError(null)
    if (!openId) return
    let live = true
    fetch(`/api/cases/${openId}/report.md`)
      .then(async (r) => { if (!r.ok) throw new Error(await r.text()); return r.text() })
      .then((t) => { if (live) setMd(t) })
      .catch((e) => { if (live) setError(String(e)) })
    return () => { live = false }
  }, [openId])

  return (
    <div className="px-6 py-5 flex flex-col gap-5 max-w-[1240px]">
      <header>
        <h1 className="text-2xl font-semibold">Reports</h1>
        <p className="text-sm mt-1" style={{ color: 'var(--ink-2)' }}>
          Draft suspicious transaction reports (STR), one per investigated case. A person reviews each draft before it is filed.
        </p>
      </header>

      {done.length === 0 ? (
        <p className="text-sm" style={{ color: 'var(--ink-2)' }}>
          No case has been investigated yet. <Link to={{ pathname: '/', search: query(null) }} className="font-medium underline" style={{ color: 'var(--brand)' }}>Open Today's work</Link>.
        </p>
      ) : (
        <div className="grid gap-5 lg:grid-cols-[260px_minmax(0,1fr)] items-start">
          <nav aria-label="Investigated cases" className="card p-2">
            <ul className="flex flex-col gap-0.5">
              {done.map((c) => (
                <li key={c.case_id}>
                  <Link to={{ pathname: '/reports', search: query(c.case_id) }} aria-current={c.case_id === open?.case_id ? 'page' : undefined}
                    className="flex items-center gap-2 px-3 py-2 rounded-lg text-sm tabular"
                    style={{ background: c.case_id === open?.case_id ? 'var(--brand-tint)' : 'transparent', fontWeight: c.case_id === open?.case_id ? 600 : 400 }}>
                    <FileText size={15} style={{ color: 'var(--muted)' }} aria-hidden />{caseNo(c.case_id)}
                    <span className="ml-auto">{c.result && <ActionBadge action={c.result.action} />}</span>
                  </Link>
                </li>
              ))}
            </ul>
          </nav>

          {open && (
            <article className="card p-6 flex flex-col gap-3 min-w-0">
              <div className="flex flex-wrap items-center gap-3 justify-end">
                <Link to={{ pathname: '/case', search: query(open.case_id) }} className="text-sm font-medium" style={{ color: 'var(--brand)' }}>Open the case</Link>
                <a href={`/api/cases/${open.case_id}/report.md`} download
                  className="inline-flex items-center gap-2 px-4 py-2 rounded-lg text-sm font-semibold" style={{ background: 'var(--brand)', color: 'var(--brand-ink)' }}>
                  <Download size={16} aria-hidden /> Download (.md)
                </a>
              </div>
              {error && <p className="text-sm" style={{ color: 'var(--critical)' }}>{error}</p>}
              {md === null && !error && <p className="text-sm" style={{ color: 'var(--ink-2)' }}>Loading the draft…</p>}
              {md && <Draft blocks={renderMarkdown(md)} />}
            </article>
          )}
        </div>
      )}
    </div>
  )
}

const Text = ({ segs }: { segs: Seg[] }) => <>{segs.map((s, i) => s.bold ? <strong key={i}>{s.text}</strong> : <span key={i}>{s.text}</span>)}</>

function Draft({ blocks }: { blocks: Block[] }) {
  const out: ReactNode[] = []
  let list: ReactNode[] = []
  const endList = () => { if (list.length) out.push(<ul key={`ul${out.length}`} className="list-disc pl-5 flex flex-col gap-1 text-sm" style={{ color: 'var(--ink-2)' }}>{list}</ul>); list = [] }
  blocks.forEach((b, i) => {
    if (b.type === 'li') { list.push(<li key={i}><Text segs={b.text} /></li>); return }
    endList()
    if (b.type === 'h1') out.push(<h2 key={i} className="text-xl font-semibold">{<Text segs={b.text} />}</h2>)
    else if (b.type === 'h2') out.push(<h3 key={i} className="text-base font-semibold mt-3">{<Text segs={b.text} />}</h3>)
    else if (b.type === 'h3') out.push(<h4 key={i} className="text-sm font-semibold mt-2">{<Text segs={b.text} />}</h4>)
    else if (b.type === 'p') out.push(<p key={i} className="text-sm leading-relaxed max-w-[75ch]" style={{ color: 'var(--ink-2)' }}><Text segs={b.text} /></p>)
    else if (b.type === 'table') out.push(
      <div key={i} className="overflow-x-auto">
        <table className="text-sm tabular whitespace-nowrap">
          <thead><tr className="text-left text-xs" style={{ color: 'var(--muted)' }}>{b.rows[0].map((c, j) => <th key={j} className="pr-6 py-1 font-medium">{c}</th>)}</tr></thead>
          <tbody>{b.rows.slice(1).map((r, j) => <tr key={j} style={{ borderTop: '1px solid var(--border)' }}>{r.map((c, k) => <td key={k} className="pr-6 py-1.5">{c}</td>)}</tr>)}</tbody>
        </table>
      </div>,
    )
  })
  endList()
  return <div className="flex flex-col gap-2">{out}</div>
}
