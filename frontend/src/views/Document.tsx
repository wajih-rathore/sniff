import { useEffect, useRef, useState } from 'react'
import { ChevronLeft, ChevronRight } from 'lucide-react'
import { api, type Fact } from '../api'
import { Async, Highlight, Status, Tag, label, useFetch, type Go } from '../ui'

/** The review screen for one document: the page image on the left, its fields with status on the right.
 *  Keys in the field list: up/down select, E edit, A approve. */
export default function DocumentReview({ id, fact, go, onSaved }: { id: number; fact?: number; go: Go; onSaved?: (f: Fact) => void }) {
  const doc = useFetch(() => api.document(id), [id])
  const [sel, setSel] = useState(fact)
  const [page, setPage] = useState(1)
  const [noImage, setNoImage] = useState('') // url of a page image that failed to load
  useEffect(() => setSel(fact), [fact])

  const facts = doc.data?.facts ?? []
  const cur = facts.find((f) => f.id === sel)
  useEffect(() => { if (cur) setPage(cur.page) }, [cur?.id, doc.data]) // eslint-disable-line react-hooks/exhaustive-deps

  const saved = (f: Fact) => { doc.retry(); onSaved?.(f) }
  const url = api.pageUrl(id, page)

  const onKey = (e: React.KeyboardEvent) => {
    if ((e.target as HTMLElement).tagName === 'INPUT') return
    const i = facts.findIndex((f) => f.id === sel)
    if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
      e.preventDefault()
      const next = facts[Math.min(facts.length - 1, Math.max(0, i + (e.key === 'ArrowDown' ? 1 : -1)))]
      if (next) setSel(next.id)
    }
    if (e.key.toLowerCase() === 'a' && cur && cur.status !== 'approved') api.approve(cur.id).then(saved, doc.retry)
    if (e.key.toLowerCase() === 'e' && cur) { e.preventDefault(); document.getElementById(`edit-${cur.id}`)?.focus() }
  }

  return (
    <Async s={doc} empty="">
      {(d) => (
        <div className="grid min-w-0 grid-cols-[minmax(0,1fr)_minmax(300px,380px)] gap-4">
          <div className="min-w-0">
            <div className="mb-2 flex items-center gap-2 text-[12px] text-muted">
              <button disabled={page <= 1} onClick={() => setPage(page - 1)} className="rounded p-0.5 hover:bg-hover disabled:opacity-30" aria-label="Previous page"><ChevronLeft size={15} /></button>
              <span>Page {page} of {d.pages}</span>
              <button disabled={page >= d.pages} onClick={() => setPage(page + 1)} className="rounded p-0.5 hover:bg-hover disabled:opacity-30" aria-label="Next page"><ChevronRight size={15} /></button>
              <button onClick={() => api.open(d.id, page)} className="ml-auto rounded-md border border-line px-2 py-0.5 hover:bg-hover">Open file</button>
            </div>
            {noImage === url ? (
              <p className="rounded-lg border border-line bg-panel p-6 text-center text-[13px] text-muted">
                No page image for this kind of file. Select a field to see the text it was read from.
              </p>
            ) : (
              <img key={url} src={url} alt={`Page ${page} of ${d.name}`} onError={() => setNoImage(url)} className="w-full rounded-lg border border-line bg-white" />
            )}
          </div>

          <div className="min-w-0">
            <div className="font-medium">{d.name}</div>
            <div className="mb-1 flex items-center gap-2">
              {d.doc_type && <Tag>{label(d.doc_type)}</Tag>}
              <span className="truncate text-[11px] text-muted" title={d.path}>{d.path}</span>
            </div>
            {d.paths.length > 1 && (
              <div className="mb-1 text-[11px] text-muted">
                Same file also at: {d.paths.slice(1).map((p) => <div key={p} className="truncate" title={p}>{p}</div>)}
              </div>
            )}
            {d.near_duplicates.length > 0 && (
              <div className="mb-1 text-[11px] text-muted">
                Same record as{' '}
                {d.near_duplicates.map((n) => (
                  <button key={n} onClick={() => go({ view: 'library', doc: { id: n } })} className="mr-1 text-accent underline">document {n}</button>
                ))}
              </div>
            )}
            {facts.length === 0 ? (
              <p className="mt-3 text-[13px] text-muted">No facts extracted from this document.</p>
            ) : (
              <ul tabIndex={0} onKeyDown={onKey} className="mt-3 divide-y divide-line rounded-lg border border-line bg-panel outline-none">
                {facts.map((f) => (
                  <li key={f.id}>
                    <button onClick={() => setSel(f.id === sel ? undefined : f.id)} className={`flex w-full items-baseline gap-2 px-3 py-2 text-left ${f.id === sel ? 'bg-accent-soft' : 'hover:bg-hover'}`}>
                      <span className="w-28 shrink-0 text-[12px] text-muted">{label(f.field)}</span>
                      <span className="min-w-0 flex-1 break-words text-[13px]">{f.value}</span>
                      <Status f={f} />
                    </button>
                    {f.id === sel && <FactEditor f={f} onSaved={saved} />}
                  </li>
                ))}
              </ul>
            )}
            <p className="mt-2 text-[11px] text-muted">↑↓ select · E edit · A approve</p>
          </div>
        </div>
      )}
    </Async>
  )
}

function FactEditor({ f, onSaved }: { f: Fact; onSaved: (f: Fact) => void }) {
  const [value, setValue] = useState(f.value)
  const [err, setErr] = useState('')
  const [busy, setBusy] = useState(false)
  const input = useRef<HTMLInputElement>(null)
  useEffect(() => setValue(f.value), [f.value])

  const run = async (p: Promise<Fact>) => {
    setBusy(true)
    setErr('')
    try { onSaved(await p) } catch (e) { setErr((e as Error).message) } finally { setBusy(false) }
  }
  const changed = value.trim() !== f.value

  return (
    <div className="space-y-2 bg-accent-soft/40 px-3 pb-3 pt-1 text-[12.5px]">
      {f.reason && <p className="text-warn">{f.reason}</p>}
      <div>
        <span className="text-muted">Read from page {f.page}: </span>
        {f.source_text ? <q className="text-fg"><Highlight text={f.source_text} q={f.value} /></q> : <span className="text-muted">no source text saved</span>}
      </div>
      <form onSubmit={(e) => { e.preventDefault(); if (changed) run(api.correct(f.id, value)) }} className="flex gap-2">
        <input
          id={`edit-${f.id}`}
          ref={input}
          value={value}
          onChange={(e) => { setValue(e.target.value); setErr('') }}
          onKeyDown={(e) => { if (e.key === 'Escape') { setValue(f.value); input.current?.blur() } }}
          aria-label={`Value of ${label(f.field)}`}
          className="h-8 min-w-0 flex-1 rounded-md border border-line bg-panel px-2 outline-none focus:border-accent"
        />
        <button disabled={!changed || busy} className="h-8 rounded-md border border-line px-2.5 hover:bg-hover disabled:opacity-40">Save</button>
        <button type="button" disabled={changed || busy || f.status === 'approved'} onClick={() => run(api.approve(f.id))}
          className="h-8 rounded-md bg-accent px-2.5 text-bg disabled:opacity-40">Approve</button>
      </form>
      {err && <p className="text-danger">{err}</p>}
    </div>
  )
}
