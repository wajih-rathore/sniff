// Typed client. Shapes mirror backend/sniff/schemas.py. With VITE_MOCK=1 every call is answered by src/mock.ts.
// The built desktop app runs its own back end on 8765 (backend/sniff/desktop.py), so a dev back end on 8000 can run beside it.
export const DESKTOP = '__TAURI_INTERNALS__' in window && !import.meta.env.DEV
export const BASE: string = import.meta.env.VITE_API ?? (DESKTOP ? 'http://127.0.0.1:8765' : 'http://127.0.0.1:8000')
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
  parser?: 'rules' | 'model' | null // who read the question: code's rules or the model router
  ungrounded_numbers?: string[] // numbers in a written answer that are not in its passages
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
export type WatchInfo = {
  folders: string[]
  last: { folder: string; at: string; indexed: number; unchanged: number; renamed: number; removed: number; errors: number } | null
  error: string | null
}
export type McpSettings = {
  enabled: boolean
  allow: Record<string, string[]> // document type -> fields; ["*"] is every field plus the page text; a missing type is hidden
  env_override: boolean // SNIFF_MCP_ALLOW is set, so the app's switches do nothing
  fields: Record<string, string[]> // every type and its fields
}
export type McpLogEntry = { time: string; tool: string; args: Record<string, unknown>; ids: number[]; refused?: boolean }
// Exactly what Google Calendar (or the .ics file) would get, and whether a person has looked at the date.
export type ReminderPreview = { fact_id: number; title: string; date: string; description: string; status: FactStatus; reviewed: boolean }

async function call<T>(method: 'GET' | 'POST' | 'PATCH' | 'PUT' | 'DELETE', path: string, body?: unknown): Promise<T> {
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
  document: (id: number) => call<Document>('GET', `/documents/${id}`),
  correct: (fact_id: number, value: string) => call<Fact>('PATCH', `/facts/${fact_id}`, { value }),
  approve: (fact_id: number) => call<Fact>('POST', `/facts/${fact_id}/approve`),
  review: () => call<ReviewItem[]>('GET', '/review'),
  overview: () => call<Overview>('GET', '/overview'),
  upcoming: () => call<UpcomingItem[]>('GET', '/upcoming?days=60'),
  remind: (fact_id: number) => call<unknown>('POST', '/reminders', { fact_id }),
  reminderPreview: (file_id: number, field: string) => call<ReminderPreview>('POST', '/reminders/preview', { file_id, field }),
  icsPath: (file_id: number, field: string) => `/reminders/ics?file_id=${file_id}&field=${enc(field)}`, // relative: the desktop app fetches it
  calendarStatus: () => call<{ connected: boolean }>('GET', '/calendar/status'), // Google Calendar
  watched: () => call<WatchInfo>('GET', '/watch'),
  watch: (folder: string) => call<WatchInfo>('POST', '/watch', { folder }),
  unwatch: (folder: string) => call<WatchInfo>('DELETE', '/watch', { folder }),
  mcpSettings: () => call<McpSettings>('GET', '/mcp/settings'),
  saveMcp: (enabled: boolean, allow: McpSettings['allow']) => call<McpSettings>('PUT', '/mcp/settings', { enabled, allow }),
  mcpLog: () => call<McpLogEntry[]>('GET', '/mcp/log?limit=50'),
}
