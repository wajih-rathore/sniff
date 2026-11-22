import { useState } from 'react'
import { api } from '../api'
import { Async, Tag, Verified, label, useFetch } from '../ui'

const TYPES = ['id_card', 'utility_bill', 'certificate', 'receipt', 'other']

export default function Library() {
  const [type, setType] = useState('')
  const [sel, setSel] = useState<number | null>(null)
  const docs = useFetch(() => api.documents(type || undefined), [type])

  return (
    <div className="mx-auto max-w-[880px] p-6">
      <div className="mb-4 flex flex-wrap gap-1.5">
        {['', ...TYPES].map((t) => (
          <button
            key={t}
            onClick={() => { setType(t); setSel(null) }}
            className={`rounded-md border px-2.5 py-1 text-[12px] ${t === type ? 'border-accent bg-accent-soft text-accent' : 'border-line text-muted hover:bg-hover'}`}
          >
            {t ? label(t) : 'All'}
          </button>
        ))}
      </div>
      <Async s={docs} empty="No documents indexed yet. Add a folder in Settings.">
        {(list) => {
          const doc = list.find((d) => d.id === sel)
          return (
            <div className="grid grid-cols-[1fr_1fr] gap-4">
              <ul className="rounded-lg border border-line bg-panel p-1.5">
                {list.map((d) => (
                  <li key={d.id}>
                    <button
                      onClick={() => setSel(d.id)}
                      className={`flex w-full items-center gap-2 rounded-md px-2.5 py-2 text-left ${d.id === sel ? 'bg-accent-soft' : 'hover:bg-hover'}`}
                    >
                      <span className="truncate">{d.name}</span>
                      {d.doc_type && <Tag>{label(d.doc_type)}</Tag>}
                      {d.status !== 'ok' && <span className="ml-auto text-[11px] text-danger">{d.status}</span>}
                    </button>
                  </li>
                ))}
              </ul>
              <div className="rounded-lg border border-line bg-panel p-4">
                {!doc ? (
                  <p className="text-[13px] text-muted">Select a document to see its facts.</p>
                ) : (
                  <>
                    <div className="font-medium">{doc.name}</div>
                    <div className="mb-3 truncate text-[11px] text-muted" title={doc.path}>{doc.path}</div>
                    {doc.facts.length === 0 ? (
                      <p className="text-[13px] text-muted">No facts extracted from this document.</p>
                    ) : (
                      <dl className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-2 text-[13px]">
                        {doc.facts.map((f) => (
                          <div key={f.id} className="contents">
                            <dt className="text-muted">{label(f.field)}</dt>
                            <dd className="flex items-baseline gap-2">
                              <span>{f.value}</span>
                              <Verified f={f} />
                              <span className="text-[11px] text-muted">p. {f.page}</span>
                            </dd>
                          </div>
                        ))}
                      </dl>
                    )}
                    <button onClick={() => api.open(doc.id, 1)} className="mt-4 rounded-md border border-line px-2.5 py-1 text-[12px] hover:bg-hover">
                      Open file
                    </button>
                  </>
                )}
              </div>
            </div>
          )
        }}
      </Async>
    </div>
  )
}
