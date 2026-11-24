import type { ReactNode } from 'react'
import { api } from '../api'
import { Async, Tag, label, useFetch, type Go } from '../ui'

export default function Overview({ go }: { go: Go }) {
  const s = useFetch(api.overview, [])
  return (
    <div className="mx-auto max-w-[880px] p-6">
      <Async s={s} empty="">
        {(o) => {
          const types = Object.entries(o.by_type).sort((a, b) => b[1] - a[1])
          const max = Math.max(1, ...types.map(([, n]) => n))
          return (
            <div className="grid gap-4">
              <div className="grid grid-cols-3 gap-3">
                <Stat n={o.documents} title="Documents" sub={`${o.total_files} files indexed`} />
                <Stat n={o.needs_review} title="Facts need review" sub="Open the review queue" warn={o.needs_review > 0}
                  onClick={() => go({ view: 'review', tab: 'queue' })} />
                <Stat n={o.conflicts} title="Conflicts" sub="Documents that disagree" warn={o.conflicts > 0}
                  onClick={() => go({ view: 'review', tab: 'conflicts' })} />
                <Stat n={o.exact_copies} title="Exact copies" sub="Same file in another folder, indexed once" />
                <Stat n={o.near_duplicates} title="Near-duplicates" sub="Same record in another file, counted once" />
                <Stat n={o.errors} title="Unreadable files" sub="Could not be read" warn={o.errors > 0} />
              </div>

              <Panel title="Documents by type">
                <table className="w-full text-[13px]">
                  <tbody>
                    {types.map(([t, n]) => (
                      <tr key={t} title={`${label(t)}: ${n}`}>
                        <td className="w-32 py-1 pr-3 text-muted">{label(t)}</td>
                        <td className="py-1">
                          <div className="h-2.5 rounded-r bg-accent" style={{ width: `${(n / max) * 100}%` }} />
                        </td>
                        <td className="w-10 py-1 pl-3 text-right tabular-nums">{n}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </Panel>

              <Panel title="Expiry and warranty dates coming up">
                <div className="grid grid-cols-3 gap-3">
                  {(['30', '60', '90'] as const).map((d) => (
                    <button key={d} onClick={() => go({ view: 'upcoming' })} className="rounded-md border border-line px-3 py-2 text-left hover:bg-hover">
                      <div className="text-[22px] font-semibold tabular-nums">{o.expiring[d]}</div>
                      <div className="text-[12px] text-muted">within {d} days</div>
                    </button>
                  ))}
                </div>
              </Panel>

              <Panel title={`Documents missing required fields (${o.missing_required.length})`}>
                {o.missing_required.length === 0 ? (
                  <p className="text-[13px] text-muted">Every document has its required fields.</p>
                ) : (
                  <ul className="divide-y divide-line">
                    {o.missing_required.map((m) => (
                      <li key={m.file_id}>
                        <button onClick={() => go({ view: 'library', doc: { id: m.file_id } })} className="flex w-full items-center gap-2 px-1 py-1.5 text-left hover:bg-hover">
                          <span className="truncate text-[13px]">{m.name}</span>
                          <Tag>{label(m.doc_type)}</Tag>
                          <span className="ml-auto shrink-0 text-[12px] text-warn">missing {m.fields.map(label).join(', ')}</span>
                        </button>
                      </li>
                    ))}
                  </ul>
                )}
              </Panel>
            </div>
          )
        }}
      </Async>
    </div>
  )
}

function Stat({ n, title, sub, warn, onClick }: { n: number; title: string; sub: string; warn?: boolean; onClick?: () => void }) {
  const body = (
    <>
      <div className={`text-[26px] font-semibold tabular-nums ${warn ? 'text-warn' : ''}`}>{n}</div>
      <div className="text-[13px]">{title}</div>
      <div className="text-[11px] text-muted">{sub}</div>
    </>
  )
  const cls = 'rounded-lg border border-line bg-panel px-4 py-3 text-left'
  return onClick ? <button onClick={onClick} className={`${cls} hover:border-accent`}>{body}</button> : <div className={cls}>{body}</div>
}

const Panel = ({ title, children }: { title: string; children: ReactNode }) => (
  <section className="rounded-lg border border-line bg-panel p-4">
    <h2 className="mb-3 text-[13px] font-medium">{title}</h2>
    {children}
  </section>
)
