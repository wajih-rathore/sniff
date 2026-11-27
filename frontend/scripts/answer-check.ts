// node --experimental-strip-types scripts/answer-check.ts : the answer forms the back end writes (ask.py), read back.
import assert from 'node:assert/strict'
import { headline } from '../src/launcher/answer.ts'
import type { Answer, Fact } from '../src/api'

const src = (file_id: number, name: string, title: string) => ({ file_id, path: `D:\\Bills\\${name}`, name, page: 1, title })
const gas = src(1, 'ece95bbc.pdf', 'Metro Gas, utility bill, Nov 2025'), ne = src(2, '7b3e19a2.pdf', 'Northern Electric, utility bill, Nov 2025')
const fact = (value: string, source_text: string): Fact => ({ id: 1, file_id: 1, field: 'current_charges', value, value_type: 'money', page: 1, verified: true, status: 'verified', reason: null, source_text })
const A = (answer: string, more: Partial<Answer> = {}): Answer => ({ question: 'q', answer, route: 'facts', sources: [gas, ne], facts: [fact('5558.0', 'Current Charges Rs. 5,558')], query: null,
  counted: [gas, ne], skipped_duplicates: [], unreviewed: [], conflicts: [], ...more })

let h = headline(A('16,798 in total, from 2 documents. Counted current charges, not amount due, so unpaid amounts carried from one bill to the next are not counted twice. Left out 1 duplicate copy of documents already counted. 1 of the values used needs review.'))
assert.deepEqual(h, { value: 'Rs.\u00a016,798', sentence: 'Counted current charges, not amount due, so unpaid amounts carried from one bill to the next are not counted twice.', total: true, alts: null })
h = headline(A('268,800 in total, from 2 documents. Conflict: amount differs for registration_number L1F21BSCS0301, semester Spring 2025: 134,400 (a.pdf) or 140,000 (b.pdf).', { facts: [] }))
assert.equal(h.value, '268,800'); assert.equal(h.sentence, 'In total, from 2 documents.')
h = headline(A('7b3e19a2.pdf: expiry date 2026-02-14, in 43 days.'))
assert.equal(h.value, '14 Feb 2026'); assert.equal(h.sentence, 'Expiry date of Northern Electric, utility bill, Nov 2025, in 43 days.')
h = headline(A('6,002.64 (ece95bbc.pdf), from the newest document (2025-11).'))
assert.equal(h.value, 'Rs.\u00a06,002.64'); assert.equal(h.sentence, 'From Metro Gas, utility bill, Nov 2025, the newest document.')
assert.equal(headline(A('3 documents.')).value, '3 documents')
assert.deepEqual(headline(A('No matching documents found.', { sources: [], counted: [] })), { value: null, sentence: 'No matching documents found.', total: false, alts: null })
const long = 'The warranty covers parts and labour for two years from the date of purchase [1].'
assert.equal(headline(A(long, { route: 'search' })).value, null)
h = headline(A('Copies of one record disagree, so Sniff has not picked one. If a.pdf is right: 16,798 in total, from 2 documents. If b.pdf is right: 16,762 in total, from 2 documents. Pick the right copy to settle it.', {
  alternatives: [{ source: ne, answer: '16,798 in total, from 2 documents.', key: 'k', fact_id: 1 }, { source: { ...ne, name: 'NE bill Nov.pdf' }, answer: '16,762 in total, from 2 documents.', key: 'k', fact_id: 2 }] }))
assert.deepEqual(h.alts, ['Rs.\u00a016,798', 'Rs.\u00a016,762']); assert.equal(h.value, null); assert.equal(h.sentence, 'In total, from 2 documents.')
// the chips list both disagreeing copies: the sentence counts records and files, so the numbers on screen match
h = headline(A('x', { sources: [gas, ne, { ...ne, name: 'NE bill Nov.pdf' }], alternatives: [{ source: ne, answer: '16,798 in total, from 2 documents.', key: 'k', fact_id: 1 }, { source: ne, answer: '16,762 in total, from 2 documents.', key: 'k', fact_id: 2 }] }))
assert.equal(h.sentence, 'In total, from 2 records (3 files).')
console.log('answer-check: all passed')
