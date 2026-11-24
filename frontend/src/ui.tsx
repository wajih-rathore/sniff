import { useCallback, useEffect, useState, type ReactNode } from 'react'
import { AlertTriangle, Check } from 'lucide-react'
import type { Fact } from './api'

export const label = (s: string) => s.replace(/_/g, ' ').replace(/^./, (c) => c.toUpperCase())

export const Tag = ({ children }: { children: ReactNode }) => (
  <span className="whitespace-nowrap rounded border border-line px-1.5 py-px text-[11px] text-muted">{children}</span>
)

// verified: on the page, next to its label, passes the rules. The rest come from the review screen.
const STATUS = {
  verified: ['verified', 'text-ok', 'Found on the page next to its label, and it passes the field rules'],
  needs_review: ['needs review', 'text-warn', 'Check this value'],
  corrected: ['corrected', 'text-accent', 'You changed this value'],
  approved: ['approved', 'text-ok', 'You confirmed this value'],
} as const

export const Status = ({ f }: { f: Fact }) => {
  const [text, color, title] = STATUS[f.status]
  return (
    <span className={`inline-flex items-center gap-0.5 whitespace-nowrap text-[11px] ${color}`} title={f.reason ?? title}>
      {f.status === 'needs_review' ? <AlertTriangle size={12} /> : <Check size={12} />} {text}
    </span>
  )
}

/** Where the app is: the view, plus the document (and fact) to open, or the Review tab. */
export type Nav = { view: string; doc?: { id: number; fact?: number }; tab?: 'queue' | 'conflicts' }
export type Go = (n: Nav) => void

export function Highlight({ text, q }: { text: string; q: string }) {
  const words = q.split(/\s+/).filter((w) => w.length > 1).map((w) => w.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'))
  if (!words.length) return <>{text}</>
  return (
    <>
      {text.split(new RegExp(`\\b(${words.join('|')})`, 'gi')).map((p, i) =>
        i % 2 ? <b key={i} className="font-semibold text-fg">{p}</b> : p,
      )}
    </>
  )
}

export function useFetch<T>(load: () => Promise<T>, deps: unknown[]) {
  const [s, setS] = useState<{ data?: T; error?: string; loading: boolean }>({ loading: true })
  const [n, setN] = useState(0)
  const run = useCallback(load, deps)
  useEffect(() => {
    let live = true
    setS((p) => ({ ...p, loading: true, error: undefined }))
    run().then(
      (data) => live && setS({ data, loading: false }),
      (e) => live && setS({ error: String(e.message ?? e), loading: false }),
    )
    return () => { live = false }
  }, [run, n])
  return { ...s, retry: () => setN((x) => x + 1) }
}

export function Msg({ children, err }: { children: ReactNode; err?: boolean }) {
  return <p className={`py-6 text-center text-[13px] ${err ? 'text-danger' : 'text-muted'}`}>{children}</p>
}

/** Renders the loading, error and empty states of a useFetch result, else the children. */
export function Async<T>({ s, empty, children }: { s: ReturnType<typeof useFetch<T>>; empty: string; children: (d: T) => ReactNode }) {
  if (s.error)
    return (
      <Msg err>
        Could not reach the backend ({s.error}).{' '}
        <button className="underline" onClick={s.retry}>Retry</button>
      </Msg>
    )
  if (s.data === undefined) return <Msg>Loading…</Msg>
  if (Array.isArray(s.data) && !s.data.length) return <Msg>{empty}</Msg>
  return <>{children(s.data)}</>
}
