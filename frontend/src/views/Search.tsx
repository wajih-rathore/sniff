import { useEffect, useRef, useState } from 'react'
import { Command } from 'cmdk'
import { FileText } from 'lucide-react'
import { api, type Answer, type Hit } from '../api'
import { Highlight, Tag, Verified, label } from '../ui'

export default function Search({ focus }: { focus: number }) {
  const [q, setQ] = useState('')
  const [hits, setHits] = useState<Hit[]>([])
  const [ans, setAns] = useState<Answer | null>(null)
  const [busy, setBusy] = useState(false) // search or ask in flight
  const [err, setErr] = useState('')
  const input = useRef<HTMLInputElement>(null)
  const seq = useRef(0) // drops responses that belong to an older query

  useEffect(() => input.current?.focus(), [focus])

  const ask = async (question: string, id: number) => {
    setBusy(true)
    try {
      const a = await api.ask(question)
      if (id === seq.current) setAns(a)
    } catch (e) {
      if (id === seq.current) setErr((e as Error).message)
    } finally {
      if (id === seq.current) setBusy(false)
    }
  }

  useEffect(() => {
    const id = ++seq.current
    setAns(null)
    setErr('')
    const query = q.trim()
    if (!query) { setHits([]); setBusy(false); return }
    const t = setTimeout(async () => {
      setBusy(true)
      try {
        const h = await api.search(query)
        if (id === seq.current) setHits(h)
      } catch (e) {
        if (id === seq.current) setErr((e as Error).message)
      }
      if (id === seq.current) setBusy(false)
      if (query.endsWith('?')) ask(query, id)
    }, 150)
    return () => clearTimeout(t)
  }, [q])

  const open = (file_id: number, page: number) =>
    api.open(file_id, page).catch((e) => setErr(`Could not open the file (${e.message})`))

  const onKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Tab' && q.trim()) { e.preventDefault(); ask(q.trim(), seq.current) }
    if (e.key === 'Escape') { if (q) setQ(''); else input.current?.blur() }
  }

  const query = q.trim()
  return (
    <div className="mx-auto w-full max-w-[680px] px-4 pt-[12vh]">
      <Command shouldFilter={false} loop label="Search documents" className="overflow-hidden rounded-lg border border-line bg-panel shadow-bar">
        <Command.Input
          ref={input}
          value={q}
          onValueChange={setQ}
          onKeyDown={onKeyDown}
          placeholder="Search your documents, or ask a question ending with ?"
          className="h-12 w-full border-b border-line bg-transparent px-4 text-[15px] outline-none placeholder:text-muted"
        />
        <Command.List>
          {!query && <p className="px-4 py-6 text-center text-[13px] text-muted">Type to search. End with ? or press Tab to ask a question.</p>}
          {ans && <AnswerCard a={ans} open={open} />}
          {query && hits.length > 0 && (
            <Command.Group heading="Documents">
              {hits.map((h, i) => (
                <Command.Item key={`${h.file_id}-${h.page}-${i}`} value={`${i}`} onSelect={() => open(h.file_id, h.page)}>
                  <div className="flex items-center gap-2">
                    <span className="truncate font-medium">{h.name}</span>
                    {h.doc_type && <Tag>{label(h.doc_type)}</Tag>}
                    <span className="ml-auto shrink-0 text-[11px] text-muted">p. {h.page}</span>
                  </div>
                  <p className="mt-0.5 line-clamp-2 text-[12.5px] text-muted"><Highlight text={h.snippet} q={query} /></p>
                </Command.Item>
              ))}
            </Command.Group>
          )}
          {query && !busy && !err && !hits.length && !ans && <p className="px-4 py-6 text-center text-[13px] text-muted">No results. If nothing is indexed yet, add a folder in Settings.</p>}
          {query && busy && <p className="px-4 py-3 text-[12px] text-muted">Searching…</p>}
          {err && <p className="px-4 py-3 text-[12px] text-danger">{err}</p>}
        </Command.List>
        <div className="flex gap-4 border-t border-line px-4 py-2 text-[11px] text-muted">
          <span>↑↓ move</span><span>Enter open</span><span>Tab ask</span><span>Esc clear</span>
        </div>
      </Command>
    </div>
  )
}

function AnswerCard({ a, open }: { a: Answer; open: (id: number, page: number) => void }) {
  const n = new Set(a.facts.map((f) => f.file_id)).size
  return (
    <div className="mx-1.5 mt-2 rounded-md border border-line p-3">
      <div className="mb-1 text-[11px] font-medium text-muted">Answer</div>
      <p className="text-[14px] leading-relaxed">{a.answer}</p>
      {a.sources.length > 0 && (
        <div className="mt-2 flex flex-wrap gap-1.5">
          {a.sources.map((s, i) => (
            <button key={i} onClick={() => open(s.file_id, s.page)} className="inline-flex items-center gap-1 rounded-md border border-line px-2 py-0.5 text-[12px] text-accent hover:bg-accent-soft">
              <FileText size={12} /> {s.name} · p. {s.page}
            </button>
          ))}
        </div>
      )}
      {a.facts.length > 0 && (
        <dl className="mt-2 grid grid-cols-[auto_1fr_auto] gap-x-3 gap-y-0.5 border-t border-line pt-2 text-[12.5px]">
          {a.facts.map((f) => (
            <div key={f.id} className="contents">
              <dt className="text-muted">{label(f.field)}</dt>
              <dd>{f.value}</dd>
              <dd><Verified f={f} /></dd>
            </div>
          ))}
        </dl>
      )}
      {a.route === 'facts' && <p className="mt-2 text-[11px] text-muted">Calculated from {n} {n === 1 ? "document" : "documents"}</p>}
    </div>
  )
}
