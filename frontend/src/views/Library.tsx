import { useState } from 'react'
import { api } from '../api'
import { Async, Tag, label, useFetch, type Go, type Nav } from '../ui'
import DocumentReview from './Document'

const TYPES = ['id_card', 'passport', 'utility_bill', 'bank_statement', 'receipt', 'warranty_card', 'certificate', 'transcript',
  'fee_voucher', 'offer_letter', 'insurance_policy', 'tenancy_agreement', 'other']

export default function Library({ open, go }: { open: Nav['doc']; go: Go }) {
  const [type, setType] = useState('')
  const docs = useFetch(() => api.documents(type || undefined), [type])

  return (
    <div className="p-6">
      <div className="mb-4 flex flex-wrap gap-1.5">
        {['', ...TYPES].map((t) => (
          <button
            key={t}
            onClick={() => setType(t)}
            className={`rounded-md border px-2.5 py-1 text-[12px] ${t === type ? 'border-accent bg-accent-soft text-accent' : 'border-line text-muted hover:bg-hover'}`}
          >
            {t ? label(t) : 'All'}
          </button>
        ))}
      </div>
      <div className="grid grid-cols-[280px_minmax(0,1fr)] gap-4">
        <Async s={docs} empty="No documents indexed yet. Add a folder in Settings.">
          {(list) => (
            <ul className="self-start rounded-lg border border-line bg-panel p-1.5">
              {list.map((d) => {
                const flagged = d.facts.filter((f) => f.status === 'needs_review').length
                return (
                  <li key={d.id}>
                    <button
                      onClick={() => go({ view: 'library', doc: { id: d.id } })}
                      className={`w-full rounded-md px-2.5 py-1.5 text-left ${d.id === open?.id ? 'bg-accent-soft' : 'hover:bg-hover'}`}
                    >
                      <div className="truncate text-[13px]">{d.name}</div>
                      <div className="flex items-center gap-2 text-[11px]">
                        {d.doc_type && <Tag>{label(d.doc_type)}</Tag>}
                        {d.paths.length > 1 && <span className="text-muted">in {d.paths.length} places</span>}
                        {flagged > 0 && <span className="text-warn">{flagged} to review</span>}
                        {d.status === 'error' && <span className="text-danger">could not read</span>}
                      </div>
                    </button>
                  </li>
                )
              })}
            </ul>
          )}
        </Async>
        <div className="min-w-0">
          {open ? <DocumentReview key={open.id} id={open.id} fact={open.fact} go={go} onSaved={docs.retry} />
            : <p className="pt-10 text-center text-[13px] text-muted">Select a document to review its facts.</p>}
        </div>
      </div>
    </div>
  )
}
