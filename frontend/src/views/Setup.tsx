import { useEffect, useState, type ReactNode } from 'react'
import { BASE, DESKTOP, MOCK } from '../api'

type Setup = {
  servers: boolean // the embedding server answers (the chat model is optional, in Settings > AI)
  ready: boolean // the embedding model is on disk
  folder: string
  models: { name: string; size: number; have: number }[]
  running: boolean
  error: string | null
}

const mb = (n: number) => Math.round(n / 1e6)

// Desktop app only: on first run, download the search model; on every start, wait until its server answers.
// The dev back end has no /setup route, so in the browser this shows the app straight away.
export default function SetupGate({ children }: { children: ReactNode }) {
  const [s, setS] = useState<Setup | null>(null)
  const [done, setDone] = useState(MOCK)
  const [err, setErr] = useState('')

  useEffect(() => {
    if (done) return
    let live = true
    const tick = () =>
      fetch(`${BASE}/setup`)
        .then(async (r) => {
          if (!r.ok) return live && setDone(true)
          const v: Setup = await r.json()
          if (live) v.servers ? setDone(true) : setS(v)
        })
        .catch(() => live && !DESKTOP && setDone(true)) // the desktop back end needs a few seconds to start
    tick()
    const t = setInterval(tick, 1000)
    return () => { live = false; clearInterval(t) }
  }, [done])

  if (done) return children

  const download = () => fetch(`${BASE}/setup/download`, { method: 'POST' }).catch((e) => setErr(e.message))
  const total = s ? s.models.reduce((a, m) => a + m.size, 0) : 0
  const have = s ? s.models.reduce((a, m) => a + m.have, 0) : 0
  const error = err || s?.error
  return (
    <div className="mx-auto max-w-[520px] px-6 pt-[18vh]">
      <h1 className="mb-2 text-[15px] font-semibold">Sniff</h1>
      {!s && <p className="text-[13px] text-muted">Starting…</p>}
      {s?.ready && <p className="text-[13px] text-muted">Starting the search model. This takes a few seconds.</p>}
      {s && !s.ready && (
        <>
          <p className="text-[13px] text-muted">
            The first time, Sniff downloads its search model ({mb(total)} MB). It runs on this laptop, and your documents never leave
            it. The model is saved in <span className="break-all">{s.folder}</span>.
          </p>
          {s.running || have > 0 ? (
            <div className="mt-5">
              <div className="h-1.5 overflow-hidden rounded bg-line">
                <div className="h-full bg-accent transition-[width]" style={{ width: `${Math.round((have / total) * 100)}%` }} />
              </div>
              <div className="mt-2 text-[12px] text-muted">{mb(have)} of {mb(total)} MB{s.running ? '' : ', paused'}</div>
            </div>
          ) : null}
          {!s.running && (
            <button onClick={download} className="mt-5 h-9 rounded-md bg-accent px-3 text-[13px] text-bg">
              {have > 0 ? 'Resume download' : 'Download'}
            </button>
          )}
        </>
      )}
      {error && <p className="mt-3 text-[13px] text-danger">{error}</p>}
    </div>
  )
}
