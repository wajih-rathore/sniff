import { useState } from 'react'
import { api, type Fact } from '../api'
import { Async, Tag, label, useFetch, type Go } from '../ui'
import DocumentReview from './Document'

/** Work through every flagged fact, or every conflict, with the document open next to the list. */
export default function Review({ tab, go }: { tab: 'queue' | 'conflicts'; go: Go }) {
  const queue = useFetch(api.review, [])
  const conflicts = useFetch(api.conflicts, [])
  const [open, setOpen] = useState<{ id: number; fact: number } | null>(null)

  const items = queue.data ?? []
  const cur = open ?? (tab === 'queue' && items[0] ? { id: items[0].fact.file_id, fact: items[0].fact.id } : null)

  // after a fix, move on to the next flagged fact
  const saved = (f: Fact) => {
    const i = items.findIndex((x) => x.fact.id === f.id)
    const next = i >= 0 ? items[i + 1] : undefined
    if (tab === 'queue') setOpen(next ? { id: next.fact.file_id, fact: next.fact.id } : null)
    queue.retry()
    conflicts.retry()
  }

  const tabBtn = (t: 'queue' | 'conflicts', text: string) => (
    <button onClick={() => { setOpen(null); go({ view: 'review', tab: t }) }}
      className={`rounded-md border px-2.5 py-1 text-[12px] ${t === tab ? 'border-accent bg-accent-soft text-accent' : 'border-line text-muted hover:bg-hover'}`}>
      {text}
    </button>
  )

  return (
    <div className="grid grid-cols-[300px_minmax(0,1fr)] gap-4 p-6">
      <div className="min-w-0">
        <div className="mb-3 flex gap-1.5">
          {tabBtn('queue', `Needs review${queue.data ? ` (${items.length})` : ''}`)}
          {tabBtn('conflicts', `Conflicts${conflicts.data ? ` (${conflicts.data.length})` : ''}`)}
        </div>
        {tab === 'queue' ? (
          <Async s={queue} empty="Nothing to review. Every fact is verified, corrected or approved.">
            {(list) => (
              <ul className="rounded-lg border border-line bg-panel p-1.5">
                {list.map((it) => (
                  <li key={it.fact.id}>
                    <button onClick={() => setOpen({ id: it.fact.file_id, fact: it.fact.id })}
                      className={`w-full rounded-md px-2.5 py-1.5 text-left ${cur?.fact === it.fact.id ? 'bg-accent-soft' : 'hover:bg-hover'}`}>
                      <div className="flex items-baseline gap-2 text-[13px]">
                        <span className="shrink-0 text-muted">{label(it.fact.field)}</span>
                        <span className="truncate">{it.fact.value}</span>
                      </div>
                      <div className="truncate text-[11px] text-muted">{it.document}</div>
                      {it.fact.reason && <div className="line-clamp-2 text-[11px] text-warn">{it.fact.reason}</div>}
                    </button>
                  </li>
                ))}
              </ul>
            )}
          </Async>
        ) : (
          <Async s={conflicts} empty="No conflicts: no two documents of the same record disagree.">
            {(list) => (
              <ul className="space-y-2">
                {list.map((c, i) => (
                  <li key={i} className="rounded-lg border border-line bg-panel p-2.5">
                    <div className="flex items-center gap-2 text-[13px]">
                      <span className="font-medium">{label(c.field)}</span>
                      <Tag>{label(c.doc_type)}</Tag>
                    </div>
                    <div className="mb-1.5 truncate text-[11px] text-muted" title={c.key}>{c.key}</div>
                    {c.values.map((v) => (
                      <button key={v.fact_id} onClick={() => setOpen({ id: v.source.file_id, fact: v.fact_id })}
                        className={`flex w-full items-baseline gap-2 rounded-md px-2 py-1 text-left text-[12.5px] ${cur?.fact === v.fact_id ? 'bg-accent-soft' : 'hover:bg-hover'}`}>
                        <span className="whitespace-nowrap font-medium">{v.value}</span>
                        <span className="truncate text-muted">{v.source.name}, p. {v.source.page}</span>
                      </button>
                    ))}
                  </li>
                ))}
              </ul>
            )}
          </Async>
        )}
      </div>
      <div className="min-w-0">
        {cur ? <DocumentReview key={cur.id} id={cur.id} fact={cur.fact} go={go} onSaved={saved} />
          : <p className="pt-10 text-center text-[13px] text-muted">Select an item to open its document.</p>}
      </div>
    </div>
  )
}
