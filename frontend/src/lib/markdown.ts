/** Just enough Markdown for the STR draft the server writes: headings, paragraphs, bullets, **bold**, tables. */

export interface Seg { text: string; bold: boolean }
export type Block =
  | { type: 'h1' | 'h2' | 'h3' | 'p' | 'li'; text: Seg[] }
  | { type: 'table'; rows: string[][] }

function inline(s: string): Seg[] {
  return s.split(/(\*\*[^*]+\*\*)/).filter(Boolean)
    .map((part) => part.startsWith('**') && part.endsWith('**') ? { text: part.slice(2, -2), bold: true } : { text: part, bold: false })
}

export function renderMarkdown(md: string): Block[] {
  const blocks: Block[] = []
  let para: string[] = []
  let table: string[][] | null = null
  const flush = () => {
    if (para.length) blocks.push({ type: 'p', text: inline(para.join(' ')) })
    if (table) blocks.push({ type: 'table', rows: table })
    para = []; table = null
  }
  for (const raw of md.split(/\r?\n/)) {
    const line = raw.trim()
    const h = /^(#{1,3})\s+(.*)$/.exec(line)
    if (!line) flush()
    else if (h) { flush(); blocks.push({ type: `h${h[1].length}` as 'h1' | 'h2' | 'h3', text: inline(h[2]) }) }
    else if (line.startsWith('- ')) { flush(); blocks.push({ type: 'li', text: inline(line.slice(2)) }) }
    else if (line.startsWith('|')) {
      if (para.length) { const p = para; para = []; blocks.push({ type: 'p', text: inline(p.join(' ')) }) }
      if (/^\|[\s|:-]+\|$/.test(line)) continue
      table = [...(table ?? []), line.slice(1, -1).split('|').map((c) => c.trim())]
    } else { if (table) flush(); para.push(line) }
  }
  flush()
  return blocks
}
