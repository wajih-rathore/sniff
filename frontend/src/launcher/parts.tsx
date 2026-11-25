import { useEffect, useRef, useState, type ReactNode } from 'react'
import { Check, File, FileImage, FileText, Presentation, TriangleAlert, X, type LucideIcon } from 'lucide-react'
import type { Answer, Fact, ReviewItem, Source } from '../api'
import { Status, Tag, label, money, show } from '../ui'

export type Action = { name: string; icon: LucideIcon; keys?: string; run: () => unknown; confirm?: ReactNode }
export type Row = {
  id: string
  head?: string // a section heading drawn above this row
  icon?: LucideIcon
  title: string
  tag?: string | null
  detail?: ReactNode
  right?: ReactNode
  body?: ReactNode // replaces title, tag and detail
  enter?: () => unknown // Enter
  path?: string // Ctrl+Enter shows it in Explorer
  more?: () => void // → or Ctrl+I, e.g. this document's facts
  remind?: Fact // the date the reminder actions are for; opening the menu fetches what Google would receive
  actions: Action[] // Ctrl+K
  note?: string // a warning at the top of the actions menu, e.g. a date nobody has reviewed
  answer?: Answer // drawn as the answer card
}

const ICONS: Record<string, LucideIcon> = { pdf: FileText, doc: FileText, docx: FileText, txt: FileText, ppt: Presentation, pptx: Presentation, jpg: FileImage, jpeg: FileImage, png: FileImage, tif: FileImage, tiff: FileImage }
export const fileIcon = (name: string) => ICONS[name.split('.').pop()!.toLowerCase()] ?? File

/** The Windows 11 selection mark at the left edge of the selected row. */
export const Pill = () => <span aria-hidden className="absolute left-0 top-1/2 h-4 w-[3px] -translate-y-1/2 rounded-full bg-accent" />

export const plural = (n: number, one: string, many: string) => `${n} ${n === 1 ? one : many}`

/** How the answer was found, in one muted line. */
export function notes(a: Answer) {
  return [
    a.counted.length > 1 && plural(a.counted.length, 'document counted', 'documents counted'),
    a.skipped_duplicates.length > 0 && plural(a.skipped_duplicates.length, 'copy skipped', 'copies skipped'),
    a.conflicts.length > 0 && plural(a.conflicts.length, 'conflict', 'conflicts'),
    a.unreviewed.length > 0 && plural(a.unreviewed.length, 'value not reviewed', 'values not reviewed'),
  ].filter(Boolean) as string[]
}
export const hasDetails = (a: Answer) => a.counted.length + a.skipped_duplicates.length + a.conflicts.length + a.facts.length > 0

export function AnswerCard({ a, id, selected, details, open, hover }: {
  a: Answer; id: string; selected: boolean; details: boolean; open: (s: Source) => void; hover: () => void
}) {
  const n = notes(a)
  const name = (file_id: number) => [...a.sources, ...a.counted].find((s) => s.file_id === file_id)?.name ?? `document ${file_id}`
  const isMoney = (field: string) => a.facts.some((f) => f.field === field && f.value_type === 'money') // conflict values carry no type
  return (
    <div id={id} role="option" aria-selected={selected} onMouseMove={hover} onMouseDown={(e) => e.preventDefault()}
      onClick={() => a.sources[0] && open(a.sources[0])}
      className={`relative mx-1.5 mb-1.5 rounded-lg border border-line px-4 py-3 ${selected ? 'bg-sel' : 'bg-panel'}`}>
      {selected && <Pill />}
      {/* the first sentence is the answer; the rest (how it was counted) is smaller */}
      {(() => {
        const [first, ...rest] = a.answer.split(/(?<=[.!?])\s+(?=[A-Z])/)
        return (
          <>
            <p className={`text-[20px] font-semibold leading-snug ${details ? '' : 'line-clamp-2'}`}>{first}</p>
            {rest.length > 0 && <p className={`mt-0.5 text-[13px] text-fg2 ${details ? '' : 'line-clamp-2'}`}>{rest.join(' ')}</p>}
          </>
        )
      })()}
      {a.sources.length > 0 && (
        <div className="mt-2 flex flex-wrap gap-1.5">
          {a.sources.map((s, i) => {
            const I = fileIcon(s.name)
            return (
              <span key={i} onClick={(e) => { e.stopPropagation(); open(s) }}
                className="inline-flex h-6 cursor-pointer items-center gap-1 rounded border border-line bg-panel px-2 text-[12px] text-fg2 hover:bg-hover">
                <I size={12} aria-hidden /> {s.name} · p. {s.page}
              </span>
            )
          })}
        </div>
      )}
      {(n.length > 0 || (!details && hasDetails(a))) && (
        <p className="mt-2 text-[12px] text-muted">
          {n.join(' · ')}
          {!details && hasDetails(a) && <span className="ml-2"><kbd>→</kbd> details</span>}
        </p>
      )}
      {details && (
        <div className="mt-3 space-y-3 border-t border-line pt-3 text-[12.5px]">
          {a.counted.length > 0 && <Part title={`Counted (${a.counted.length})`}>{a.counted.map((s, i) => <SourceLine key={i} s={s} />)}</Part>}
          {a.skipped_duplicates.length > 0 && (
            <Part title="Skipped as copies of a document already counted">{a.skipped_duplicates.map((s, i) => <SourceLine key={i} s={s} />)}</Part>
          )}
          {a.conflicts.map((c, i) => (
            <Part key={i} title={`Conflict: ${label(c.field).toLowerCase()} differs for ${c.key}`}>
              {c.values.map((v) => (
                <div key={v.fact_id} className="flex items-baseline gap-2">
                  <span className="tnum font-semibold text-danger">{isMoney(c.field) ? money(v.value) : v.value}</span>
                  <span className="truncate text-muted">{v.source.name}, p. {v.source.page}</span>
                </div>
              ))}
            </Part>
          ))}
          {a.facts.length > 0 && (
            <Part title="Values used">
              <div className="grid grid-cols-[auto_auto_minmax(0,1fr)_auto] items-baseline gap-x-3 gap-y-0.5">
                {a.facts.map((f) => (
                  <div key={f.id} className="contents">
                    <span className="text-muted">{label(f.field)}</span>
                    <span className="tnum">{show(f)}</span>
                    <span className="truncate text-muted">{name(f.file_id)}, p. {f.page}</span>
                    <Status f={f} />
                  </div>
                ))}
              </div>
            </Part>
          )}
        </div>
      )}
    </div>
  )
}

const Part = ({ title, children }: { title: string; children: ReactNode }) => (
  <section>
    <h3 className="mb-1 text-[12px] font-semibold text-fg2">{title}</h3>
    {children}
  </section>
)
const SourceLine = ({ s }: { s: Source }) => {
  const I = fileIcon(s.name)
  return <div className="flex items-center gap-2"><I size={13} className="shrink-0 text-muted" aria-hidden /><span className="truncate">{s.name}</span><span className="text-muted">p. {s.page}</span></div>
}

/** Text box for correcting a value: Enter saves, Esc cancels. */
export function EditBox({ f, value, set, save, big }: { f: Fact; value: string; set: (v: string | null) => void; save: (v: string) => void; big?: boolean }) {
  return (
    <input autoFocus value={value} onChange={(e) => set(e.target.value)} aria-label={`Correct ${label(f.field)}`}
      onKeyDown={(e) => {
        if (e.key === 'Enter') { e.preventDefault(); e.stopPropagation(); save(value.trim()) }
        if (e.key === 'Escape') { e.preventDefault(); e.stopPropagation(); set(null) }
      }}
      className={`w-full max-w-[360px] rounded border border-line border-b-2 border-b-accent bg-panel px-2 outline-none ${big ? 'mt-1 h-10 text-[20px]' : 'h-7 text-[13px]'}`} />
  )
}

/** One fact per row: field, value (or the correction box), status, page, and why it needs review. */
export function FactLine({ f, edit, set, save }: { f: Fact; edit: string | null; set: (v: string | null) => void; save: (v: string) => void }) {
  return (
    <div className="min-w-0 flex-1">
      <div className="flex items-center gap-3">
        <span className="w-36 shrink-0 truncate text-muted">{label(f.field)}</span>
        <span className="tnum min-w-0 flex-1 truncate">{edit === null ? show(f) : <EditBox f={f} value={edit} set={set} save={save} />}</span>
        <Status f={f} />
      </div>
      {f.reason && f.status === 'needs_review' && <div className="line-clamp-2 pl-[156px] text-[12px] text-warn">{label(f.reason)}</div>}
    </div>
  )
}

/** One flagged fact at a time: A accepts, C corrects, S skips. */
export function ReviewCard({ item, left, edit, setEdit, error, accept, correct, skip }: {
  item: ReviewItem; left: number; edit: string | null; setEdit: (v: string | null) => void; error: string
  accept: () => void; correct: (v: string) => void; skip: () => void
}) {
  const f = item.fact
  const I = fileIcon(item.document)
  return (
    <div className="px-6 py-4" role="group" aria-label={`Check ${label(f.field)} in ${item.document}`}>
      <div className="flex items-center gap-2 text-[12px] text-muted">
        <I size={14} aria-hidden /> <span className="truncate">{item.document}</span>
        {item.doc_type && <Tag>{label(item.doc_type)}</Tag>}
        <span className="ml-auto tnum">{left} left to check</span>
      </div>
      <div className="mt-5 text-[13px] text-fg2">{label(f.field)}</div>
      {edit === null
        ? <div className="tnum text-[28px] font-semibold leading-tight">{show(f)}</div>
        : <EditBox f={f} value={edit} set={setEdit} save={correct} big />}
      {error && <p className="mt-1 text-[12px] text-danger">{error}</p>}
      {f.reason && <p className="mt-2 flex items-center gap-1.5 text-[13px] text-warn"><TriangleAlert size={14} aria-hidden /> {label(f.reason)}</p>}
      <p className="mt-1 text-[12px] text-muted">On page {f.page}. Press Enter to open the page and look.</p>
      {f.source_text && <p className="mt-2 line-clamp-2 border-l border-line pl-3 text-[12px] text-fg2">{f.source_text}</p>}
      <div className="mt-5 flex gap-2">
        {edit === null ? (
          <>
            <Btn k="A" primary onClick={accept}><Check size={14} aria-hidden /> Accept</Btn>
            <Btn k="C" onClick={() => setEdit(f.value)}>Correct</Btn>
            <Btn k="S" onClick={skip}>Skip</Btn>
          </>
        ) : (
          <>
            <Btn k="Enter" primary onClick={() => correct(edit.trim())}><Check size={14} aria-hidden /> Save</Btn>
            <Btn k="Esc" onClick={() => setEdit(null)}><X size={14} aria-hidden /> Cancel</Btn>
          </>
        )}
      </div>
    </div>
  )
}

const Btn = ({ k, primary, onClick, children }: { k: string; primary?: boolean; onClick: () => void; children: ReactNode }) => (
  <button type="button" onClick={onClick} aria-keyshortcuts={k}
    className={`inline-flex h-8 items-center gap-1.5 rounded border px-3 text-[13px] ${primary ? 'border-transparent bg-accent text-bg hover:opacity-90' : 'border-line bg-panel hover:bg-hover'}`}>
    {children} <kbd className={primary ? 'border-bg/30! text-bg!' : ''}>{k}</kbd>
  </button>
)

/** A Windows 11 toggle switch, drawn only: the row it sits in is what the keyboard and screen readers use. */
export const Toggle = ({ on }: { on: boolean }) => (
  <span aria-hidden className={`relative inline-block h-5 w-10 rounded-full border ${on ? 'border-accent bg-accent' : 'border-muted'}`}>
    <span className={`absolute top-1/2 size-3 -translate-y-1/2 rounded-full transition-[left] ${on ? 'left-[22px] bg-bg' : 'left-[3px] bg-muted'}`} />
  </span>
)

/** Ctrl+K: actions for the selected row. An action with `confirm` asks first and shows what will happen. */
export function Menu({ row, close }: { row: Row; close: () => void }) {
  const [i, setI] = useState(0)
  const [askName, setAskName] = useState<string | null>(null)
  const asking = row.actions.find((a) => a.name === askName) // looked up each render, so the preview updates once the document loads
  const box = useRef<HTMLDivElement>(null)
  useEffect(() => box.current?.focus(), [askName])
  const items: Action[] = asking
    ? [{ name: asking.name, icon: Check, keys: 'Enter', run: asking.run }, { name: 'Cancel', icon: X, keys: 'Esc', run: () => {} }]
    : row.actions
  const pick = (a: Action) => {
    if (a.confirm && !asking) { setAskName(a.name); setI(0); return }
    close()
    a.run()
  }
  return (
    <>
      <div className="absolute inset-0" onMouseDown={close} />
      <div ref={box} role="menu" tabIndex={-1} aria-label={asking ? `Confirm: ${asking.name}` : `Actions for ${row.title}`} aria-activedescendant={`act-${i}`}
        onKeyDown={(e) => {
          e.stopPropagation()
          const k = e.key
          if (k === 'ArrowDown' || k === 'ArrowUp') { e.preventDefault(); setI((i + (k === 'ArrowDown' ? 1 : -1) + items.length) % items.length) }
          else if (k === 'Enter') { e.preventDefault(); pick(items[i]) }
          else if (k === 'Escape' || ((e.ctrlKey || e.metaKey) && k.toLowerCase() === 'k')) { e.preventDefault(); close() }
        }}
        className="absolute bottom-10 right-2 w-[340px] rounded-lg border border-line bg-panel p-1 shadow-flyout outline-none">
        {asking
          ? <div className="px-2.5 pb-2 pt-1.5 text-[12.5px] leading-snug text-fg2">{asking.confirm}</div>
          : (
            <div className="px-2.5 pb-1 pt-1.5 text-[12px]">
              <div className="truncate text-muted">{row.title}</div>
              {row.note && <div className="mt-0.5 flex items-center gap-1 text-warn"><TriangleAlert size={12} aria-hidden /> {row.note}</div>}
            </div>
          )}
        {items.map((a, n) => (
          <div key={a.name} id={`act-${n}`} role="menuitem" onMouseMove={() => setI(n)} onMouseDown={(e) => e.preventDefault()} onClick={() => pick(a)}
            className={`relative flex h-8 cursor-default items-center gap-2.5 rounded px-2.5 text-[13px] ${n === i ? 'bg-sel' : ''}`}>
            {n === i && <Pill />}
            <a.icon size={15} className="shrink-0 text-fg2" aria-hidden />
            <span className="flex-1 truncate">{a.name}</span>
            {a.keys && <kbd>{a.keys}</kbd>}
          </div>
        ))}
      </div>
    </>
  )
}
