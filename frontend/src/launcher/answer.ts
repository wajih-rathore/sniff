// The answer screen shows a value in large type and one sentence under it. The back end writes facts answers in a
// few fixed forms (ask.py: _compute and _finish); this reads them back. Details (counted, copies, conflicts) have
// their own view, so their sentences are dropped here. Checked by frontend/scripts/answer-check.ts.
import type { Answer } from '../api'

// shortcut: facts store the number only, so the currency comes from the line a value was read from ("Current Charges
// Rs. 5,558"); move to a currency field if the back end adds one.
export const CURRENCY = /\b(PKR|Rs\.?|USD|EUR|GBP)(?=[\s\d])|[$€£]/

export type Headline = { value: string | null; sentence: string; total: boolean; alts: string[] | null }

const cap = (s: string) => s.replace(/^./, (c) => c.toUpperCase())
const DAY = /^(\d{4})-(\d{2})-(\d{2})$/
const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']
export const fmtDay = (iso: string) => { const m = DAY.exec(iso); return m ? `${+m[3]} ${MONTHS[+m[2] - 1]} ${m[1]}` : iso }

export function currency(a: Pick<Answer, 'facts'>): string {
  return a.facts.map((f) => (f.value_type === 'money' && f.source_text?.match(CURRENCY)?.[0]) || '').find(Boolean) ?? ''
}

/** File names in a written answer become the documents' titles. */
export function titled(text: string, a: Pick<Answer, 'sources' | 'counted' | 'alternatives'>) {
  for (const s of [...a.sources, ...a.counted, ...(a.alternatives ?? []).map((x) => x.source)])
    if (s.title && s.title !== s.name) text = text.split(s.name).join(s.title)
  return text
}

export function headline(a: Answer): Headline {
  const cur = currency(a)
  const money = (n: string) => (cur ? `${cur}\u00a0${n}` : n) // never wrap "Rs." away from its number
  const one = (text: string, route = a.route): Omit<Headline, 'alts'> => {
    text = titled(text, a).replace(/\b1 documents\b/g, '1 document')
      .replace(/ Conflict: [\s\S]*$/, '')
      .replace(/ Left out \d+ duplicate cop(?:y|ies) of documents already counted\./, '')
      .replace(/ \d+ of the values used needs? review\./, '')
    let m
    if ((m = /^(-?[\d,]+(?:\.\d+)?) (in total|outstanding)\b,?\s*/.exec(text))) {
      const rest = text.slice(m[0].length).replace(/^from (\d+) documents?\.\s*/, '')
      return { value: money(m[1]), sentence: cap(rest) || cap(`${m[2]}, ${text.slice(m[0].length)}`.replace(/,\s*$/, '')), total: true }
    }
    if ((m = /^(.+): ([a-z ]+) (\d{4}-\d{2}-\d{2}), in (\d+) days?\.$/.exec(text)))
      return { value: fmtDay(m[3]), sentence: `${cap(m[2])} of ${m[1]}, in ${m[4]} days.`, total: false }
    if (route === 'facts' && (m = /^([^();]+?) \(([^()]+)\)(, from the newest document \([^()]+\))?\.(.*)$/.exec(text))) {
      const v = m[1].trim()
      return { value: /^-?[\d,]+(\.\d+)?$/.test(v) ? money(v) : DAY.test(v) ? fmtDay(v) : v, sentence: `From ${m[2]}${m[3] ? ', the newest document' : ''}.${m[4]}`.trim(), total: false }
    }
    const [first, ...rest] = text.split(/(?<=\.)\s+(?=[A-Z0-9])/)
    if (route === 'facts' && a.sources.length && first.length <= 48)
      return { value: first.replace(/\.$/, ''), sentence: rest.join(' '), total: false }
    return { value: null, sentence: text, total: false }
  }
  if (a.alternatives?.length) {
    const sides = a.alternatives.map((x) => one(x.answer, 'facts'))
    // the citations list both disagreeing copies, so say how many files the records came from
    const n = /from (\d+) documents?\./.exec(a.alternatives[0].answer)?.[1]
    const sentence = n && a.sources.length > +n ? `In total, from ${n} ${n === '1' ? 'record' : 'records'} (${a.sources.length} files).` : sides[0].sentence
    return { ...sides[0], sentence, value: null, alts: sides.map((s) => s.value ?? '–') }
  }
  return { ...one(a.answer), alts: null }
}
