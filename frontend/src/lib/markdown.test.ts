import { describe, expect, it } from 'vitest'
import { renderMarkdown } from './markdown'

describe('renderMarkdown', () => {
  it('reads headings, paragraphs with bold, bullets and tables', () => {
    const md = '# Title\n\nSome **bold** text\nwrapped\n\n## Part\n- one\n- two\n\n| a | b |\n|---|---|\n| 1 | 2 |\n'
    expect(renderMarkdown(md)).toEqual([
      { type: 'h1', text: [{ text: 'Title', bold: false }] },
      { type: 'p', text: [{ text: 'Some ', bold: false }, { text: 'bold', bold: true }, { text: ' text wrapped', bold: false }] },
      { type: 'h2', text: [{ text: 'Part', bold: false }] },
      { type: 'li', text: [{ text: 'one', bold: false }] },
      { type: 'li', text: [{ text: 'two', bold: false }] },
      { type: 'table', rows: [['a', 'b'], ['1', '2']] },
    ])
  })

  it('returns nothing for an empty draft', () => {
    expect(renderMarkdown('')).toEqual([])
  })
})
