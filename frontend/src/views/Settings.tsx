import { useEffect, useRef, useState } from 'react'
import { api, type IndexStatus } from '../api'

export default function Settings() {
  const [folder, setFolder] = useState('')
  const [st, setSt] = useState<IndexStatus | null>(null)
  const [err, setErr] = useState('')
  const [polling, setPolling] = useState(true) // one check on mount, in case an index run is already going

  const grace = useRef(0) // ticks that may report "not running" right after Index folder, if the backend is slow to start

  useEffect(() => {
    if (!polling) return
    let live = true
    const tick = () =>
      api.indexStatus().then(
        (s) => { if (live) { setSt(s); if (!s.running && grace.current-- <= 0) setPolling(false) } },
        (e) => { if (live) { setErr(`Could not reach the backend (${e.message})`); setPolling(false) } },
      )
    tick()
    const t = setInterval(tick, 1000)
    return () => { live = false; clearInterval(t) }
  }, [polling])

  const start = async () => {
    setErr('')
    try {
      await api.index(folder.trim())
      grace.current = 2
      setPolling(true)
    } catch (e) {
      setErr(`Could not start indexing (${(e as Error).message})`)
    }
  }

  const pct = st && st.total ? Math.round((st.done / st.total) * 100) : 0
  return (
    <div className="mx-auto max-w-[680px] p-6">
      <h2 className="mb-2 text-[13px] font-medium">Index a folder</h2>
      <form onSubmit={(e) => { e.preventDefault(); start() }} className="flex gap-2">
        <input
          value={folder}
          onChange={(e) => setFolder(e.target.value)}
          placeholder="C:\Users\you\Documents"
          className="h-9 flex-1 rounded-md border border-line bg-panel px-3 outline-none focus:border-accent"
        />
        <button disabled={!folder.trim() || st?.running} className="h-9 rounded-md bg-accent px-3 text-[13px] text-bg disabled:opacity-50">
          Index folder
        </button>
      </form>
      {err && <p className="mt-3 text-[13px] text-danger">{err}</p>}
      {st && (st.running || st.total > 0) && (
        <div className="mt-5">
          <div className="h-1.5 overflow-hidden rounded bg-line">
            <div className="h-full bg-accent transition-[width]" style={{ width: `${pct}%` }} />
          </div>
          <div className="mt-2 flex justify-between text-[12px] text-muted">
            <span>{st.running ? `Indexing ${st.done} of ${st.total}` : `Done: ${st.done} of ${st.total} files`}</span>
            <span>{st.errors} errors</span>
          </div>
          {st.running && st.current && <div className="mt-1 truncate text-[12px] text-muted" title={st.current}>{st.current}</div>}
        </div>
      )}
      {st && !st.running && st.total === 0 && <p className="mt-5 text-[13px] text-muted">No indexing run yet.</p>}
    </div>
  )
}
