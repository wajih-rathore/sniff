// Fixtures for every endpoint, used when VITE_MOCK=1. Dates are relative to today so Upcoming always looks current.
import type { Answer, Document, EvalResult, Fact, Hit, IndexStatus, UpcomingItem } from './api'

const day = 86_400_000
const iso = (offset: number) => new Date(Date.now() + offset * day).toISOString().slice(0, 10)

let fid = 0
const F = (file_id: number, field: string, value: string, value_type: Fact['value_type'], page = 1, verified = true): Fact => ({
  id: ++fid, file_id, field, value, value_type, page, verified,
})

// [id, name, doc_type, facts, page text]
const base = 'C:\\Users\\hp\\Documents\\'
const raw: [number, string, string, Fact[], string][] = [
  [1, 'cnic_front.jpg', 'id_card', [
    F(1, 'name', 'Ayesha Khan', 'text'), F(1, 'id_number', '35202-1234567-8', 'id'),
    F(1, 'date_of_birth', '1999-04-12', 'date'), F(1, 'issue_date', '2019-03-02', 'date'),
    F(1, 'expiry_date', iso(34), 'date', 1, false),
  ], 'National Identity Card of Pakistan. Name Ayesha Khan. Identity Number 35202-1234567-8. Date of Expiry'],
  [2, 'passport_scan.pdf', 'id_card', [
    F(2, 'name', 'Ayesha Khan', 'text'), F(2, 'id_number', 'AB1234567', 'id'), F(2, 'expiry_date', iso(-12), 'date'),
  ], 'Passport. Surname Khan. Given names Ayesha. Passport number AB1234567. Date of expiry'],
  [3, 'lesco_bill_oct.pdf', 'utility_bill', [
    F(3, 'provider', 'LESCO', 'text'), F(3, 'account_number', '04-11223-3344556', 'id'), F(3, 'billing_month', '2026-10', 'text'),
    F(3, 'amount_due', '14820.00', 'money', 1), F(3, 'due_date', iso(6), 'date'),
  ], 'LESCO electricity bill for October 2026. Units consumed 412. Total amount due 14820.00. Due date'],
  [4, 'ptcl_bill_oct.pdf', 'utility_bill', [
    F(4, 'provider', 'PTCL', 'text'), F(4, 'account_number', '042-99887766', 'id'), F(4, 'billing_month', '2026-10', 'text'),
    F(4, 'amount_due', '3450.00', 'money'), F(4, 'due_date', iso(15), 'date'),
  ], 'PTCL broadband invoice, October 2026. Amount due 3450.00. Please pay before the due date'],
  [5, 'sngpl_bill_sep.pdf', 'utility_bill', [
    F(5, 'provider', 'SNGPL', 'text'), F(5, 'billing_month', '2026-09', 'text'), F(5, 'amount_due', '2210.50', 'money', 1, false),
    F(5, 'due_date', iso(-3), 'date'),
  ], 'SNGPL gas bill September 2026. Payable within due date. Amount due 2210.50'],
  [6, 'degree_bscs.pdf', 'certificate', [
    F(6, 'holder_name', 'Ayesha Khan', 'text'), F(6, 'title', 'Bachelor of Science in Computer Science', 'text'),
    F(6, 'institution', 'FAST National University', 'text'), F(6, 'issue_date', '2024-08-20', 'date'),
  ], 'This is to certify that Ayesha Khan has been awarded the degree of Bachelor of Science in Computer Science by FAST National University'],
  [7, 'laptop_receipt.pdf', 'receipt', [
    F(7, 'merchant', 'Hafeez Centre Electronics', 'text'), F(7, 'date', '2026-02-14', 'date'),
    F(7, 'total_amount', '219000.00', 'money'), F(7, 'warranty_until', iso(81), 'date', 2),
  ], 'Hafeez Centre Electronics. Invoice for laptop, total 219000.00. Warranty valid for two years from the date of purchase'],
  [8, 'lecture_notes_week5.pptx', 'other', [], 'Week 5: retrieval augmented generation. BM25 and dense retrieval are fused with reciprocal rank fusion'],
]

const docs: Document[] = raw.map(([id, name, doc_type, facts]) => ({
  id, path: base + name, name, doc_type, indexed_at: new Date(Date.now() - id * 3_600_000).toISOString(), status: 'ok', facts,
}))
const allFacts = docs.flatMap((d) => d.facts)

const delay = <T>(v: T, ms = 250) => new Promise<T>((r) => setTimeout(() => r(v), ms))

function search(q: string): Hit[] {
  const words = q.toLowerCase().replace(/[?]/g, '').split(/\s+/).filter((w) => w.length > 1)
  return raw
    .map(([id, name, doc_type, , text]) => {
      const hay = (name + ' ' + text).toLowerCase()
      const score = words.filter((w) => hay.includes(w)).length / Math.max(words.length, 1)
      return { file_id: id, path: base + name, name, page: 1, snippet: text, score, doc_type } as Hit
    })
    .filter((h) => h.score > 0)
    .sort((a, b) => b.score - a.score)
}

function ask(question: string): Answer {
  const src = (ids: number[]) => ids.map((id) => ({ file_id: id, path: docs[id - 1].path, name: docs[id - 1].name, page: 1 }))
  const pick = (field: string) => allFacts.filter((f) => f.field === field)
  if (/expir|renew|valid/i.test(question)) {
    const f = pick('expiry_date').filter((x) => x.file_id === 1)
    return { question, route: 'facts', answer: `Your national ID card expires on ${f[0].value}, in 34 days.`, sources: src([1]), facts: f, query: { doc_type: 'id_card', field: 'expiry_date', op: 'next_expiring', filters: {} } }
  }
  if (/bill|total|spent|paid|owe|amount/i.test(question)) {
    const f = pick('amount_due')
    const total = f.reduce((s, x) => s + Number(x.value), 0).toFixed(2)
    return { question, route: 'facts', answer: `The utility bills add up to ${total}.`, sources: src([...new Set(f.map((x) => x.file_id))]), facts: f, query: { doc_type: 'utility_bill', field: 'amount_due', op: 'sum', filters: {} } }
  }
  const h = search(question)[0]
  if (!h) return { question, route: 'none', answer: 'I could not find this in your documents.', sources: [], facts: [], query: null }
  return { question, route: 'search', answer: h.snippet + '.', sources: src([h.file_id]), facts: [], query: null }
}

function upcoming(): UpcomingItem[] {
  const names = ['expiry_date', 'due_date', 'warranty_until']
  return allFacts
    .filter((f) => names.includes(f.field))
    .map((fact) => {
      const d = docs[fact.file_id - 1]
      const days_left = Math.round((Date.parse(fact.value) - Date.now()) / day) + (fact.value === iso(0) ? 0 : 1)
      return { fact, document: d.name, path: d.path, doc_type: d.doc_type, days_left, reminder_status: null }
    })
    .filter((u) => u.days_left <= 90)
}

const confusion = {
  labels: ['id_card', 'utility_bill', 'certificate', 'receipt', 'other'],
  matrix: [[22, 0, 0, 0, 1], [0, 27, 0, 1, 0], [0, 0, 18, 0, 2], [0, 2, 0, 20, 0], [1, 1, 1, 0, 24]],
}
const evals: EvalResult[] = [
  { component: 'search', n: 60, failures: 12, updated: '2026-11-22T10:00:00', latency_ms: { p50: 120, p95: 300 }, rows: [
    { system: 'Sniff (hybrid)', 'Recall@5': 0.87, MRR: 0.71 },
    { system: 'BM25 only (baseline)', 'Recall@5': 0.74, MRR: 0.58 },
    { system: 'File-name match (baseline)', 'Recall@5': 0.31, MRR: 0.22 },
  ] },
  { component: 'document_type', n: 120, failures: 10, updated: '2026-11-22T10:05:00', latency_ms: { p50: 410, p95: 880 }, confusion, rows: [
    { system: 'Sniff (LLM)', Accuracy: 0.92, 'Macro-F1': 0.91 },
    { system: 'Keyword rules (baseline)', Accuracy: 0.78, 'Macro-F1': 0.74 },
  ] },
  { component: 'fact_extraction', n: 110, failures: 14, updated: '2026-11-22T10:10:00', latency_ms: { p50: 1900, p95: 4200 }, rows: [
    { system: 'Sniff (LLM + verify)', 'Field accuracy': 0.88, 'Schema-valid rate': 0.99 },
    { system: 'Regex rules (baseline)', 'Field accuracy': 0.61, 'Schema-valid rate': 1 },
  ] },
  { component: 'ocr', n: 50, failures: 11, updated: '2026-11-22T10:15:00', latency_ms: { p50: 850, p95: 2100 }, rows: [
    { system: 'RapidOCR', 'Character error rate': 0.06 },
    { system: 'Tesseract defaults (baseline)', 'Character error rate': 0.11 },
  ] },
  { component: 'ask', n: 55, failures: 10, updated: '2026-11-22T10:20:00', latency_ms: { p50: 2400, p95: 5600 }, rows: [
    { system: 'Sniff (router + facts)', Correctness: 0.82, 'Citation correctness': 0.9, Abstention: 0.8 },
    { system: 'Plain RAG (baseline)', Correctness: 0.58, 'Citation correctness': 0.71, Abstention: 0.4 },
  ] },
]

let indexStart = 0
function indexStatus(): IndexStatus {
  const total = 24
  const done = indexStart ? Math.min(total, Math.floor((Date.now() - indexStart) / 300)) : 0
  if (!indexStart) return { running: false, done: 0, total: 0, current: null, errors: 0 }
  return { running: done < total, done, total, current: done < total ? `${base}scans\\page_${done + 1}.png` : null, errors: done > 15 ? 1 : 0 }
}

export async function mock(method: string, path: string, body?: unknown): Promise<unknown> {
  const url = new URL(path, 'http://x')
  const p = url.pathname
  if (p === '/search') return delay(search(url.searchParams.get('q') ?? ''), 120)
  if (p === '/ask') return delay(ask((body as { question: string }).question), 900)
  if (p === '/documents') { const t = url.searchParams.get('type'); return delay(t ? docs.filter((d) => d.doc_type === t) : docs) }
  if (p === '/upcoming') return delay(upcoming())
  if (p === '/eval') return delay(evals)
  if (p === '/index/status') return indexStatus()
  if (p === '/index' && method === 'POST') { indexStart = Date.now(); return { started: true } }
  if (p === '/open' || p === '/reminders') return delay({ ok: true }, p === '/reminders' ? 600 : 50)
  throw new Error(`mock: no fixture for ${method} ${path}`)
}
