import { useEffect, useRef, useState } from 'react'
import { Command } from 'cmdk'
import { FileText } from 'lucide-react'
import { api, type Answer, type Hit, type Source } from '../api'
import { Highlight, Status, Tag, label, type Go } from '../ui'

export default function Search({ focus, go }: { focus: number; go: Go }) {
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
          {ans && <AnswerCard a={ans} open={open} go={go} />}
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

function AnswerCard({ a, open, go }: { a: Answer; open: (id: number, page: number) => void; go: Go }) {
  const name = (file_id: number) => [...a.sources, ...a.counted].find((s) => s.file_id === file_id)?.name ?? `document ${file_id}`
  const chips = (list: Source[], muted = false) => (
    <div className="flex flex-wrap gap-1.5">
      {list.map((s, i) => (
        <button key={i} onClick={() => open(s.file_id, s.page)}
          className={`inline-flex items-center gap-1 rounded-md border border-line px-2 py-0.5 text-[12px] hover:bg-accent-soft ${muted ? 'text-muted' : 'text-accent'}`}>
          <FileText size={12} /> {s.name} · p. {s.page}
        </button>
      ))}
    </div>
  )
  const review = (file_id: number, fact: number) => go({ view: 'library', doc: { id: file_id, fact } })
  return (
    <div className="mx-1.5 mt-2 rounded-md border border-line p-3">
      <div className="mb-1 text-[11px] font-medium text-muted">Answer</div>
      <p className="text-[14px] leading-relaxed">{a.answer}</p>
      {a.route === 'search' && a.sources.length > 0 && <div className="mt-2">{chips(a.sources)}</div>}
      {a.counted.length > 0 && <Part title={`Counted (${a.counted.length} ${a.counted.length === 1 ? 'document' : 'documents'})`}>{chips(a.counted)}</Part>}
      {a.skipped_duplicates.length > 0 && <Part title="Skipped as duplicates of documents already counted">{chips(a.skipped_duplicates, true)}</Part>}
      {a.facts.length > 0 && (
        <Part title="Values used">
          <dl className="grid grid-cols-[auto_1fr_auto] gap-x-3 gap-y-0.5 text-[12.5px]">
            {a.facts.map((f) => (
              <div key={f.id} className="contents">
                <dt className="text-muted">{label(f.field)}</dt>
                <dd className="truncate">{f.value} <span className="text-muted">· {name(f.file_id)}</span></dd>
                <dd><Status f={f} /></dd>
              </div>
            ))}
          </dl>
        </Part>
      )}
      {a.unreviewed.length > 0 && (
        <Part title="Not reviewed yet: check these values">
          <div className="flex flex-wrap gap-1.5">
            {a.unreviewed.map((f) => (
              <button key={f.id} onClick={() => review(f.file_id, f.id)} title={f.reason ?? undefined}
                className="rounded-md border border-line px-2 py-0.5 text-[12px] text-warn hover:bg-hover">
                {label(f.field)} {f.value} · {name(f.file_id)} · review
              </button>
            ))}
          </div>
        </Part>
      )}
      {a.conflicts.map((c, i) => (
        <Part key={i} title={`Conflict: ${label(c.field)} differs for ${c.key}`}>
          {c.values.map((v) => (
            <button key={v.fact_id} onClick={() => review(v.source.file_id, v.fact_id)} className="mr-3 text-[12.5px] hover:underline">
              <span className="font-medium text-danger">{v.value}</span> <span className="text-muted">in {v.source.name}, p. {v.source.page}</span>
            </button>
          ))}
        </Part>
      ))}
    </div>
  )
}

const Part = ({ title, children }: { title: string; children: React.ReactNode }) => (
  <div className="mt-2 border-t border-line pt-2">
    <div className="mb-1 text-[11px] font-medium text-muted">{title}</div>
    {children}
  </div>
)
