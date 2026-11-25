// Fixtures for every endpoint, used when VITE_MOCK=1. Dates are relative to today so Upcoming always looks current.
import type { Answer, Document, Fact, Hit, McpLogEntry, McpSettings, Overview, UpcomingItem, WatchInfo } from './api'
import { label, money } from './ui'

const day = 86_400_000
const iso = (offset: number) => new Date(Date.now() + offset * day).toISOString().slice(0, 10)

let fid = 0
const F = (file_id: number, field: string, value: string, value_type: Fact['value_type'], page = 1, verified = true): Fact => ({
  id: ++fid, file_id, field, value, value_type, page, verified,
  status: verified ? 'verified' : 'needs_review', reason: verified ? null : 'not next to its label', source_text: null,
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
  id, path: base + name, name, doc_type, indexed_at: new Date(Date.now() - id * 3_600_000).toISOString(), status: 'indexed', facts,
  paths: [base + name], near_duplicates: [], pages: 1,
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
  const none = { counted: [], skipped_duplicates: [], unreviewed: [], conflicts: [] }
  const src = (ids: number[]) => ids.map((id) => ({ file_id: id, path: docs[id - 1].path, name: docs[id - 1].name, page: 1 }))
  const pick = (field: string) => allFacts.filter((f) => f.field === field)
  if (/expir|renew|valid/i.test(question)) {
    const f = pick('expiry_date').filter((x) => x.file_id === 1)
    return { ...none, question, route: 'facts', answer: `Your national ID card expires on ${f[0].value}, in 34 days.`, sources: src([1]), counted: src([1]), unreviewed: f.filter((x) => !x.verified), facts: f, query: { doc_type: 'id_card', field: 'expiry_date', op: 'next_expiring', filters: {} } }
  }
  if (/bill|total|spent|paid|owe|amount/i.test(question)) {
    const f = pick('amount_due')
    const total = f.reduce((s, x) => s + Number(x.value), 0).toFixed(2)
    const s = src([...new Set(f.map((x) => x.file_id))])
    // a phone photo of the LESCO bill is skipped as a copy; a reprint of the SNGPL bill disagrees on the amount
    const photo = { file_id: 9, path: base + 'Downloads\\lesco_bill_oct_photo.jpg', name: 'lesco_bill_oct_photo.jpg', page: 1 }
    const sngpl = f.find((x) => x.file_id === 5)!
    const conflicts = [{ doc_type: 'utility_bill', key: 'SNGPL, 2026-09', field: 'amount_due', values: [
      { value: sngpl.value, fact_id: sngpl.id, status: sngpl.status, source: s.find((x) => x.file_id === 5)! },
      { value: '2201.50', fact_id: 999, status: 'verified' as const, source: { file_id: 10, path: base + 'Downloads\\sngpl_sep_reprint.pdf', name: 'sngpl_sep_reprint.pdf', page: 1 } },
    ] }]
    return { ...none, question, route: 'facts', answer: `${money(total)} in total, from ${s.length} documents.`, sources: s, counted: s, skipped_duplicates: [photo], conflicts, unreviewed: f.filter((x) => !x.verified), facts: f, query: { doc_type: 'utility_bill', field: 'amount_due', op: 'sum', filters: {} } }
  }
  const h = search(question)[0]
  if (!h) return { ...none, question, route: 'none', answer: 'I could not find this in your documents.', sources: [], facts: [], query: null }
  return { ...none, question, route: 'search', answer: h.snippet + '.', sources: src([h.file_id]), facts: [], query: null }
}

function upcoming(days = 90): UpcomingItem[] {
  const names = ['expiry_date', 'due_date', 'warranty_until']
  return allFacts
    .filter((f) => names.includes(f.field))
    .map((fact) => {
      const d = docs[fact.file_id - 1]
      const days_left = Math.round((Date.parse(fact.value) - Date.now()) / day) + (fact.value === iso(0) ? 0 : 1)
      return { fact, document: d.name, path: d.path, doc_type: d.doc_type, days_left, reminder_status: null, conflict: null }
    })
    .filter((u) => u.days_left <= days)
    .sort((a, b) => a.days_left - b.days_left) // as the back end returns them
}

function overview(): Overview {
  const by_type: Record<string, number> = {}
  for (const d of docs) by_type[d.doc_type ?? 'other'] = (by_type[d.doc_type ?? 'other'] ?? 0) + 1
  const ex = upcoming().filter((u) => u.fact.field !== 'due_date' && u.days_left >= 0)
  const within = (n: number) => ex.filter((u) => u.days_left <= n).length
  return { total_files: docs.length, documents: docs.length, by_type, exact_copies: 0, near_duplicates: 0, expiring: { '30': within(30), '60': within(60), '90': within(90) },
    missing_required: [], needs_review: allFacts.filter((f) => f.status === 'needs_review').length, conflicts: 1, errors: 0 } // the SNGPL reprint in the bills answer
}

// MCP settings, as GET/PUT /mcp/settings keeps them
const FIELDS: Record<string, string[]> = {
  id_card: ['name', 'id_number', 'date_of_birth', 'issue_date', 'expiry_date'],
  utility_bill: ['provider', 'account_number', 'billing_month', 'bill_date', 'due_date', 'current_charges', 'previous_balance', 'tax', 'amount_due', 'payment_status', 'payment_date'],
  certificate: ['holder_name', 'title', 'institution', 'issue_date'],
  receipt: ['merchant', 'date', 'subtotal', 'tax', 'total_amount', 'warranty_until'],
  passport: ['name', 'passport_number', 'nationality', 'date_of_birth', 'issue_date', 'expiry_date'],
  transcript: ['student_name', 'registration_number', 'institution', 'program', 'cgpa', 'issue_date'],
  bank_statement: ['bank', 'account_number', 'period_start', 'period_end', 'opening_balance', 'total_credits', 'total_debits', 'closing_balance'],
  warranty_card: ['product', 'brand', 'serial_number', 'purchase_date', 'warranty_until'],
  offer_letter: ['employer', 'candidate_name', 'position', 'monthly_salary', 'offer_date', 'start_date'],
  insurance_policy: ['insurer', 'policy_number', 'insured_name', 'premium', 'start_date', 'end_date'],
  tenancy_agreement: ['landlord', 'tenant', 'property_address', 'monthly_rent', 'security_deposit', 'start_date', 'end_date'],
  fee_voucher: ['student_name', 'registration_number', 'institution', 'semester', 'amount', 'issue_date', 'due_date'],
  other: [],
}
let mcp: McpSettings = {
  enabled: false, env_override: false, fields: FIELDS,
  allow: Object.fromEntries(Object.keys(FIELDS).filter((t) => !['passport', 'bank_statement', 'offer_letter', 'transcript', 'id_card'].includes(t)).map((t) => [t, ['*']])),
}
const ago = (min: number) => new Date(Date.now() - min * 60_000).toISOString().slice(0, 19)
const mcpLog: McpLogEntry[] = [
  { time: ago(3), tool: 'search_documents', args: { query: 'electricity bill october' }, ids: [3, 4] },
  { time: ago(4), tool: 'ask', args: { question: 'When does my passport expire?' }, ids: [] },
  { time: ago(9), tool: 'list_upcoming', args: { days: 30 }, ids: [3, 4] },
  { time: ago(1440), tool: 'search_documents', args: { query: 'degree certificate' }, ids: [], refused: true },
]
// The title POST /reminders builds (api.py _reminder_title).
const VERB: Record<string, string> = { expiry_date: 'expires', due_date: 'payment due', warranty_until: 'warranty ends', end_date: 'ends' }
function preview(fact_id: number) {
  const f = allFacts.find((x) => x.id === fact_id)
  if (!f || !VERB[f.field]) throw new Error('This fact is not a date Sniff can remind you about')
  const d = docs[f.file_id - 1]
  const who = d.facts.find((x) => ['name', 'holder_name', 'provider', 'merchant', 'institution'].includes(x.field))
  return { title: `${label(d.doc_type ?? 'document')} ${VERB[f.field]}${who ? ` (${who.value})` : ''}`, date: f.value, description: `From ${d.name}, page ${f.page}. Added by Sniff.` }
}

let watched: string[] = ['C:\\Users\\you\\Documents\\Bills']
let watchLast: WatchInfo['last'] = { folder: watched[0], at: new Date(Date.now() - 90_000).toISOString().slice(0, 19), indexed: 1, unchanged: 0, renamed: 0, removed: 0, errors: 0 }

export async function mock(method: string, path: string, body?: unknown): Promise<unknown> {
  const url = new URL(path, 'http://x')
  const p = url.pathname
  if (p === '/search') return delay(search(url.searchParams.get('q') ?? ''), 120)
  if (p === '/ask') return delay(ask((body as { question: string }).question), 900)
  if (p.startsWith('/documents/')) return delay(docs.find((d) => d.id === Number(p.split('/')[2])))
  if (p === '/review') return delay(allFacts.filter((f) => f.status === 'needs_review').map((fact) => ({ fact, document: docs[fact.file_id - 1].name, path: docs[fact.file_id - 1].path, doc_type: docs[fact.file_id - 1].doc_type })))
  if (p.startsWith('/facts/')) {
    const f = allFacts.find((x) => x.id === Number(p.split('/')[2]))
    if (!f) throw new Error('Fact not found')
    if (method === 'PATCH') Object.assign(f, { value: (body as { value: string }).value, status: 'corrected', reason: null })
    else Object.assign(f, { status: 'approved', reason: null })
    return delay({ ...f }, 120)
  }
  if (p === '/overview') return delay(overview())
  if (p === '/upcoming') return delay(upcoming(Number(url.searchParams.get('days') ?? 90)))
  if (p === '/watch') {
    const folder = (body as { folder?: string } | undefined)?.folder ?? ''
    if (method === 'POST' && !watched.includes(folder)) watched = [...watched, folder]
    if (method === 'DELETE') {
      if (!watched.includes(folder)) throw new Error('This folder is not being watched')
      watched = watched.filter((f) => f !== folder)
    }
    return delay({ folders: watched, last: watchLast, error: null } satisfies WatchInfo, 80)
  }
  if (p === '/mcp/settings') {
    if (method === 'PUT') mcp = { ...mcp, ...(body as Pick<McpSettings, 'enabled' | 'allow'>) }
    return delay(mcp, 80)
  }
  if (p === '/mcp/log') return delay(mcpLog)
  if (p === '/calendar/status') return delay({ google: true })
  if (p === '/reminders/preview') return delay(preview((body as { fact_id: number }).fact_id), 80)
  if (p === '/open' || p === '/reminders') return delay({ ok: true }, p === '/reminders' ? 600 : 50)
  throw new Error(`mock: no fixture for ${method} ${path}`)
}
