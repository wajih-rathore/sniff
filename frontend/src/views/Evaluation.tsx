import { api, type EvalResult } from '../api'
import { Async, label, useFetch } from '../ui'

const LOWER_IS_BETTER = /error|cer|wer|latency|loss/i
const fmt = (v: string | number) => (typeof v === 'number' ? v.toFixed(2) : v)

export default function Evaluation() {
  const res = useFetch(api.eval, [])
  return (
    <div className="mx-auto grid max-w-[880px] gap-4 p-6">
      <Async s={res} empty="No evaluation results yet. Run the eval scripts in the eval folder.">
        {(list) => list.map((r) => <Card key={r.component} r={r} />)}
      </Async>
    </div>
  )
}

function Card({ r }: { r: EvalResult }) {
  const cols = [...new Set(r.rows.flatMap((row) => Object.keys(row)))]
  // best value per numeric column: highest, or lowest for error-style metrics
  const best: Record<string, number> = {}
  for (const c of cols) {
    const nums = r.rows.map((row) => row[c]).filter((v): v is number => typeof v === 'number')
    if (nums.length) best[c] = (LOWER_IS_BETTER.test(c) ? Math.min : Math.max)(...nums)
  }
  return (
    <section className="rounded-lg border border-line bg-panel p-4">
      <div className="flex items-baseline gap-3">
        <h2 className="text-[14px] font-semibold">{label(r.component)}</h2>
        <span className="text-[12px] text-muted">{r.n} examples</span>
        <span className="ml-auto text-[11px] text-muted">updated {r.updated.replace('T', ' ').slice(0, 16)}</span>
      </div>
      <table className="mt-3 w-full text-[13px]">
        <thead>
          <tr className="border-b border-line text-left text-[11px] text-muted">
            {cols.map((c) => <th key={c} className="py-1 pr-4 font-medium">{c === 'system' ? 'System' : c}</th>)}
          </tr>
        </thead>
        <tbody>
          {r.rows.map((row, i) => (
            <tr key={i} className="border-b border-line last:border-0">
              {cols.map((c) => (
                <td key={c} className={`py-1.5 pr-4 tabular-nums ${row[c] === best[c] ? 'font-bold' : ''}`}>{row[c] === undefined ? '' : fmt(row[c])}</td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
      <div className="mt-3 flex gap-6 text-[12px] text-muted">
        {r.latency_ms && <span>Latency p50 {r.latency_ms.p50} ms, p95 {r.latency_ms.p95} ms</span>}
        <span>{r.failures} failures</span>
      </div>
      {r.confusion && <Confusion {...r.confusion} />}
    </section>
  )
}

function Confusion({ labels, matrix }: { labels: string[]; matrix: number[][] }) {
  const max = Math.max(1, ...matrix.flat())
  return (
    <div className="mt-4">
      <div className="mb-1 text-[11px] text-muted">Confusion matrix (rows: true, columns: predicted)</div>
      <table className="text-[12px] tabular-nums">
        <thead>
          <tr><th />{labels.map((l) => <th key={l} className="px-2 pb-1 text-[11px] font-normal text-muted">{label(l)}</th>)}</tr>
        </thead>
        <tbody>
          {matrix.map((row, i) => (
            <tr key={i}>
              <th className="pr-2 text-right text-[11px] font-normal text-muted">{label(labels[i])}</th>
              {row.map((v, j) => (
                <td
                  key={j}
                  className="h-7 min-w-14 border border-panel text-center"
                  style={{ background: `color-mix(in srgb, var(--accent) ${Math.round((v / max) * 70)}%, transparent)` }}
                >
                  {v}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}
