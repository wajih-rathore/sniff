// Typed client. Shapes mirror backend/sniff/schemas.py. With VITE_MOCK=1 every call is answered by src/mock.ts.
export const BASE: string = import.meta.env.VITE_API ?? 'http://127.0.0.1:8000'
export const MOCK = import.meta.env.VITE_MOCK === '1'

export type FactStatus = 'verified' | 'needs_review' | 'corrected' | 'approved'
export type Fact = {
  id: number
  file_id: number
  field: string
  value: string
  value_type: 'date' | 'money' | 'text' | 'id'
  page: number
  verified: boolean
  status: FactStatus
  reason: string | null // why it needs review
  source_text: string | null
}
export type Hit = { file_id: number; path: string; name: string; page: number; snippet: string; score: number; doc_type: string | null }
export type Source = { file_id: number; path: string; name: string; page: number }
export type Conflict = {
  doc_type: string
  key: string // what the documents share, e.g. "id_number 35202-1234567-1"
  field: string
  values: { value: string; fact_id: number; status: FactStatus; source: Source }[]
}
export type Answer = {
  question: string
  answer: string
  route: 'facts' | 'search' | 'none'
  sources: Source[]
  facts: Fact[]
  query: Record<string, unknown> | null
  counted: Source[]
  skipped_duplicates: Source[]
  unreviewed: Fact[]
  conflicts: Conflict[]
}
export type Document = {
  id: number
  path: string
  name: string
  doc_type: string | null
  indexed_at: string | null
  status: string
  facts: Fact[]
  paths: string[] // every place this exact file is stored; the first is `path`
  near_duplicates: number[] // other documents holding the same record
  pages: number
}
export type UpcomingItem = {
  fact: Fact
  document: string
  path: string
  doc_type: string | null
  days_left: number
  reminder_status: 'created' | null
  conflict: Conflict | null
}
export type ReviewItem = { fact: Fact; document: string; path: string; doc_type: string | null }
export type Overview = {
  total_files: number
  documents: number
  by_type: Record<string, number>
  exact_copies: number
  near_duplicates: number
  expiring: Record<'30' | '60' | '90', number>
  missing_required: { file_id: number; name: string; doc_type: string; fields: string[] }[]
  needs_review: number
  conflicts: number
  errors: number
}
export type IndexStatus = { running: boolean; done: number; total: number; current: string | null; errors: number }
// Every eval/results/*.json with a "component". Only `component` is certain; the rest differs per file.
export type EvalResult = {
  component: string
  n?: number
  rows?: Record<string, string | number | boolean | null>[]
  latency_ms?: { p50: number; p95: number }
  failures?: number
  updated?: string
  confusion?: { labels: string[]; matrix: number[][] }
  [extra: string]: unknown
}

async function call<T>(method: 'GET' | 'POST' | 'PATCH', path: string, body?: unknown): Promise<T> {
  if (MOCK) return (await import('./mock')).mock(method, path, body) as Promise<T>
  const r = await fetch(BASE + path, {
    method,
    headers: body ? { 'Content-Type': 'application/json' } : undefined,
    body: body ? JSON.stringify(body) : undefined,
  })
  if (!r.ok) {
    const text = await r.text()
    let detail: unknown
    try { detail = JSON.parse(text).detail } catch { /* not JSON */ }
    throw new Error(typeof detail === 'string' ? detail : `${r.status} ${text.slice(0, 200)}`) // FastAPI's message, e.g. a validation error
  }
  return r.json()
}

const enc = encodeURIComponent
export const api = {
  search: (q: string) => call<Hit[]>('GET', `/search?q=${enc(q)}&k=10`),
  ask: (question: string) => call<Answer>('POST', '/ask', { question }),
  open: (file_id: number, page: number) => call<unknown>('POST', '/open', { file_id, page }),
  documents: (type?: string) => call<Document[]>('GET', `/documents${type ? `?type=${enc(type)}` : ''}`),
  document: (id: number) => call<Document>('GET', `/documents/${id}`),
  pageUrl: (id: number, page: number) => `${BASE}/documents/${id}/pages/${page}.png`,
  correct: (fact_id: number, value: string) => call<Fact>('PATCH', `/facts/${fact_id}`, { value }),
  approve: (fact_id: number) => call<Fact>('POST', `/facts/${fact_id}/approve`),
  review: () => call<ReviewItem[]>('GET', '/review'),
  conflicts: () => call<Conflict[]>('GET', '/conflicts'),
  overview: () => call<Overview>('GET', '/overview'),
  upcoming: () => call<UpcomingItem[]>('GET', '/upcoming?days=90'),
  remind: (fact_id: number) => call<unknown>('POST', '/reminders', { fact_id }),
  eval: () => call<EvalResult[]>('GET', '/eval'),
  index: (folder: string) => call<unknown>('POST', '/index', { folder }),
  indexStatus: () => call<IndexStatus>('GET', '/index/status'),
}
