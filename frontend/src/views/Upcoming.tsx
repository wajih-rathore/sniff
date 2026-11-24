import { useState } from 'react'
import { api } from '../api'
import { Async, Status, Tag, label, useFetch } from '../ui'

export default function Upcoming() {
  const items = useFetch(api.upcoming, [])
  const [added, setAdded] = useState<Record<number, 'sending' | 'done' | string>>({}) // by fact id; any other string is an error

  // The click is the user's approval; nothing is sent before it.
  const add = async (id: number) => {
    setAdded((a) => ({ ...a, [id]: 'sending' }))
    try {
      await api.remind(id)
      setAdded((a) => ({ ...a, [id]: 'done' }))
    } catch (e) {
      setAdded((a) => ({ ...a, [id]: `Failed: ${(e as Error).message}` }))
    }
  }

  return (
    <div className="mx-auto max-w-[760px] p-6">
      <Async s={items} empty="Nothing expires or falls due in the next 90 days.">
        {(list) => (
          <ul className="divide-y divide-line rounded-lg border border-line bg-panel">
            {[...list].sort((a, b) => a.days_left - b.days_left).map((it) => {
              const st = it.reminder_status === 'created' ? 'done' : added[it.fact.id]
              return (
                <li key={it.fact.id} className="flex items-center gap-3 px-4 py-3">
                  <div className="min-w-0 flex-1">
                    <div className="flex items-center gap-2">
                      <span className="truncate font-medium">{it.document}</span>
                      {it.doc_type && <Tag>{label(it.doc_type)}</Tag>}
                    </div>
                    <div className="flex items-center gap-2 text-[12.5px] text-muted">{label(it.fact.field)} · {it.fact.value} <Status f={it.fact} /></div>
                    {it.conflict && (
                      <div className="text-[12px] text-danger">
                        Conflict: {it.conflict.values.map((v) => `${v.value} in ${v.source.name}`).join(' but ')}
                      </div>
                    )}
                    {st && st !== 'sending' && st !== 'done' && <div className="text-[12px] text-danger">{st}</div>}
                  </div>
                  <div className={`shrink-0 text-[12.5px] ${it.days_left < 0 ? 'text-danger' : ''}`}>
                    {it.days_left < 0 ? `expired ${-it.days_left} days ago` : it.days_left === 0 ? 'today' : `in ${it.days_left} days`}
                  </div>
                  {st === 'done' ? (
                    <span className="w-44 shrink-0 whitespace-nowrap text-center text-[12px] text-ok">Added</span>
                  ) : (
                    <button
                      onClick={() => add(it.fact.id)}
                      disabled={st === 'sending'}
                      className="w-44 shrink-0 whitespace-nowrap rounded-md border border-line px-2 py-1 text-[12px] hover:bg-hover disabled:opacity-50"
                    >
                      {st === 'sending' ? 'Adding…' : 'Add to Google Calendar'}
                    </button>
                  )}
                </li>
              )
            })}
          </ul>
        )}
      </Async>
    </div>
  )
}
