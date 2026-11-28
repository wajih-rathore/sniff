// Typed client. Shapes mirror backend/sniff/schemas.py. With VITE_MOCK=1 every call is answered by src/mock.ts.
// The built desktop app runs its own back end on 8765 (backend/sniff/desktop.py), so a dev back end on 8000 can run beside it.
export const DESKTOP = '__TAURI_INTERNALS__' in window && !import.meta.env.DEV
export const BASE: string = import.meta.env.VITE_API ?? (DESKTOP ? 'http://127.0.0.1:8765' : 'http://127.0.0.1:8000')
export const MOCK = import.meta.env.VITE_MOCK === '1'

export type FactStatus = 'verified' | 'needs_review' | 'corrected' | 'approved' | 'superseded' // superseded: the user chose another copy's value
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
export type Hit = {
  file_id: number; path: string; name: string; page: number; snippet: string; score: number; doc_type: string | null
  reading?: boolean // findable already; its type and facts are still being read
  title: string // "Metro Gas, utility bill, Nov 2025", from the facts; the file name when they give none
  date: string | null // the document's own date, ISO
  match: 'fact' | 'name' | 'text' | 'meaning' // meaning: vector search only, a weaker match
  match_reason: string
  fact: Fact | null // the fact holding the query's words
}
export type Source = { file_id: number; path: string; name: string; page: number; title?: string }
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
  alternatives?: { source: Source; answer: string; key: string; fact_id: number }[] // the answer for each side of a conflict; POST /conflicts/pick {key, fact_id}
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
  title: string
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
export type ReviewItem = { fact: Fact; document: string; path: string; doc_type: string | null; title: string }
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
  counts?: Record<string, { files: number; errors: number; waiting: number }> // per folder; waiting: facts not read yet
}
export type McpSettings = {
  enabled: boolean
  allow: Record<string, string[]> // document type -> fields; ["*"] is every field plus the page text; a missing type is hidden
  env_override: boolean // SNIFF_MCP_ALLOW is set, so the app's switches do nothing
  fields: Record<string, string[]> // every type and its fields
}
// GET/PUT /ai/settings. AI is off until a provider is picked; the key is write-only and never comes back.
export type AiProvider = 'off' | 'anthropic' | 'openai' | 'local'
export type AiSettings = {
  provider: AiProvider
  base_url: string // OpenAI-compatible only
  model: string // empty: default_model
  allow: string[] // hidden types the user lets go to a provider off this laptop
  key_saved: boolean // a key for this provider is in the system's password store
  hidden: string[] // types never sent unless allowed: the ones agent access hides by default
  local_available: boolean // this laptop's model file is downloaded
  default_model: string
}
export type AiSave = Pick<AiSettings, 'provider' | 'base_url' | 'model' | 'allow'> & { key?: string } // key: "" removes it
// GET /ai/local, desktop back end only (backend/sniff/desktop.py): This laptop's model file and its download. error: the back end's words.
export type LocalModel = { name: string; size: number; have: number; ready: boolean; running: boolean; error: string | null }
// GET /ai/preview: exactly what each model call would send, masked as sent. While AI is off, what a cloud provider would get.
// note: for a document not sorted into a type yet, what sorting it means for what is sent.
export type AiPreview = { provider: AiProvider; leaves_laptop: boolean; note: string | null; calls: { purpose: string; messages: { role: string; content: string }[] }[] }
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

// Mock mode has no pages: a plain strip of printed lines, the value outlined when there is one.
const mockPage = (mark: boolean, ar = .5) => 'data:image/svg+xml,' + encodeURIComponent(`<svg xmlns="http://www.w3.org/2000/svg" width="1190" height="${1190 * ar}" viewBox="0 0 1190 ${1190 * ar}">
<rect width="1190" height="${1190 * ar}" fill="#fdfcf9"/><rect width="1190" height="14" fill="#b5461a"/>
${Array.from({ length: Math.floor((1190 * ar - 78) / 60) }, (_, i) => 90 + i * 60).map((y, i) => `<rect x="60" y="${y}" width="${[420, 300, 360, 520, 280, 340, 400, 260][i % 8]}" height="18" rx="4" fill="#d9d6ce"/><rect x="${i % 2 ? 900 : 960}" y="${y}" width="${i % 2 ? 230 : 170}" height="18" rx="4" fill="#d9d6ce"/>`).join('')}
${mark ? '<rect x="940" y="380" width="210" height="38" rx="4" fill="rgb(79 70 229 / .16)" stroke="rgb(79 70 229)" stroke-width="3"/>' : ''}</svg>`)

// POST /ask/stream: server-sent events. "token" pieces arrive while a search answer is written, then one "answer".
async function askStream(question: string, onToken: (text: string) => void): Promise<Answer> {
  if (MOCK) return (await import('./mock')).mockStream(question, onToken)
  const r = await fetch(BASE + '/ask/stream', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ question }) })
  if (!r.ok || !r.body) throw new Error(`${r.status} ${(await r.text()).slice(0, 200)}`)
  const reader = r.body.pipeThrough(new TextDecoderStream()).getReader()
  let buf = ''
  for (;;) {
    const { value, done } = await reader.read()
    if (done) throw new Error('The answer stopped before it was finished')
    buf += value
    let end
    while ((end = buf.indexOf('\n\n')) >= 0) { // one event: "event: <kind>" then "data: <json>"
      const ev = buf.slice(0, end)
      buf = buf.slice(end + 2)
      const kind = /^event: (.*)$/m.exec(ev)?.[1]
      const data = JSON.parse(/^data: (.*)$/m.exec(ev)?.[1] ?? 'null')
      if (kind === 'token') onToken(data)
      else if (kind === 'answer') return data as Answer
      else if (kind === 'error') throw new Error(data)
    }
  }
}

const enc = encodeURIComponent
export const api = {
  search: (q: string) => call<Hit[]>('GET', `/search?q=${enc(q)}&k=10`),
  ask: (question: string) => call<Answer>('POST', '/ask', { question }),
  askStream,
  pick: (key: string, fact_id: number) => call<Conflict[]>('POST', '/conflicts/pick', { key, fact_id }),
  recent: (n: number) => call<Document[]>('GET', `/documents?recent=${n}`),
  indexStatus: () => call<{ queued: number; running: boolean }>('GET', '/index/status'),
  model: () => call<{ ready: boolean; starting: boolean }>('GET', '/model'), // starting: the chat model is loading (a cold start)
  // A PNG strip of the page around a fact (or the query's words); the top of the page without either.
  cropUrl: (file_id: number, o: { fact_id?: number; page?: number; q?: string; ar?: number }) => // ar: height / width of the strip
    MOCK ? mockPage(o.fact_id != null, o.ar)
    : `${BASE}/documents/${file_id}/crop?${new URLSearchParams(Object.entries(o).filter(([, v]) => v != null && v !== '').map(([k, v]) => [k, String(v)]))}`,
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
  aiSettings: () => call<AiSettings>('GET', '/ai/settings'),
  saveAi: (s: AiSave) => call<AiSettings>('PUT', '/ai/settings', s),
  aiTest: () => call<{ ok: boolean; error?: string }>('POST', '/ai/test'), // one token, nothing from your files
  localModel: () => call<LocalModel>('GET', '/ai/local'),
  downloadLocal: () => call<LocalModel>('POST', '/ai/local/download'), // starts or resumes; a second call while it runs does nothing
  aiPreview: (o: { file_id: number } | { question: string }) => call<AiPreview>('GET', `/ai/preview?${new URLSearchParams(Object.entries(o).map(([k, v]) => [k, String(v)]))}`),
}
