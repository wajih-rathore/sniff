// Typed client. Shapes mirror backend/sniff/schemas.py. With VITE_MOCK=1 every call is answered by src/mock.ts.
export const BASE: string = import.meta.env.VITE_API ?? 'http://127.0.0.1:8000'
export const MOCK = import.meta.env.VITE_MOCK === '1'

export type Fact = {
  id: number
  file_id: number
  field: string
  value: string
  value_type: 'date' | 'money' | 'text' | 'id'
  page: number
  verified: boolean
}
export type Hit = { file_id: number; path: string; name: string; page: number; snippet: string; score: number; doc_type: string | null }
export type Source = { file_id: number; path: string; name: string; page: number }
export type Answer = {
  question: string
  answer: string
  route: 'facts' | 'search' | 'none'
  sources: Source[]
  facts: Fact[]
  query: Record<string, unknown> | null
}
export type Document = { id: number; path: string; name: string; doc_type: string | null; indexed_at: string | null; status: string; facts: Fact[] }
export type UpcomingItem = {
  fact: Fact
  document: string
  path: string
  doc_type: string | null
  days_left: number
  reminder_status: 'created' | null
}
export type IndexStatus = { running: boolean; done: number; total: number; current: string | null; errors: number }
export type EvalResult = {
  component: string
  n: number
  rows: Record<string, string | number>[]
  latency_ms?: { p50: number; p95: number }
  failures: number
  updated: string
  confusion?: { labels: string[]; matrix: number[][] }
}

async function call<T>(method: 'GET' | 'POST', path: string, body?: unknown): Promise<T> {
  if (MOCK) return (await import('./mock')).mock(method, path, body) as Promise<T>
  const r = await fetch(BASE + path, {
    method,
    headers: body ? { 'Content-Type': 'application/json' } : undefined,
    body: body ? JSON.stringify(body) : undefined,
  })
  if (!r.ok) throw new Error(`${r.status} ${(await r.text()).slice(0, 200)}`)
  return r.json()
}

const enc = encodeURIComponent
export const api = {
  search: (q: string) => call<Hit[]>('GET', `/search?q=${enc(q)}&k=10`),
  ask: (question: string) => call<Answer>('POST', '/ask', { question }),
  open: (file_id: number, page: number) => call<unknown>('POST', '/open', { file_id, page }),
  documents: (type?: string) => call<Document[]>('GET', `/documents${type ? `?type=${enc(type)}` : ''}`),
  upcoming: () => call<UpcomingItem[]>('GET', '/upcoming?days=90'),
  remind: (fact_id: number) => call<unknown>('POST', '/reminders', { fact_id }),
  eval: () => call<EvalResult[]>('GET', '/eval'),
  index: (folder: string) => call<unknown>('POST', '/index', { folder }),
  indexStatus: () => call<IndexStatus>('GET', '/index/status'),
}
