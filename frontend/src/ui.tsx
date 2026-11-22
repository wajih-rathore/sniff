import { useCallback, useEffect, useState, type ReactNode } from 'react'
import { Check } from 'lucide-react'
import type { Fact } from './api'

export const label = (s: string) => s.replace(/_/g, ' ').replace(/^./, (c) => c.toUpperCase())

export const Tag = ({ children }: { children: ReactNode }) => (
  <span className="rounded border border-line px-1.5 py-px text-[11px] text-muted">{children}</span>
)

export const Verified = ({ f }: { f: Fact }) =>
  f.verified ? (
    <span className="inline-flex items-center gap-0.5 text-[11px] text-ok" title="Found word for word on the page">
      <Check size={12} /> verified
    </span>
  ) : (
    <span className="text-[11px] text-muted" title="Not found word for word on the page, check it">
      AI-read
    </span>
  )

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
