import { api, type EvalResult } from '../api'
import { Async, label, useFetch } from '../ui'

// Each eval/results/*.json has its own extra keys. Known ones get their own layout; any other key is shown
// generically (a number, a table, or raw JSON), so a new result file shows up without a code change.
const KNOWN = new Set(['component', 'rows', 'latency_ms', 'failures', 'updated', 'confusion', 'n'])
const LOWER_IS_BETTER = /error|cer|wer|latency|loss|false|_ms$|_MB$|needs_review/i
const NOT_A_SCORE = /^(n|calls|fields|docs_with_fields)$/
type Cell = string | number | boolean | null | undefined
const fmt = (v: Cell) => (typeof v === 'number' ? (Number.isInteger(v) ? String(v) : v.toFixed(3)) : v == null ? '' : String(v))

export default function Evaluation() {
  const res = useFetch(api.eval, [])
  return (
    <div className="mx-auto grid max-w-[960px] gap-4 p-6">
      <Async s={res} empty="No evaluation results yet. Run the scripts in the eval folder; they write eval/results/*.json.">
        {(list) => list.map((r) => <Card key={r.component} r={r} />)}
      </Async>
    </div>
  )
}

function Card({ r }: { r: EvalResult }) {
  const rows = r.rows ?? []
  // bold the best value per column among rival systems in the same test set; system.json rows are parts of one system
  const compare = r.component !== 'system' && rows.length > 1 && rows.every((row) => 'system' in row)
  const extras = Object.entries(r).filter(([k]) => !KNOWN.has(k))
  return (
    <section className="rounded-lg border border-line bg-panel p-4">
      <div className="flex items-baseline gap-3">
        <h2 className="text-[14px] font-semibold">{label(r.component)}</h2>
        {r.n !== undefined && <span className="text-[12px] text-muted">{r.n} examples</span>}
        {r.updated && <span className="ml-auto text-[11px] text-muted">updated {r.updated.replace('T', ' ').slice(0, 16)}</span>}
      </div>
      {rows.length > 0 && <Table rows={rows} compare={compare} />}
      <div className="mt-3 flex flex-wrap gap-x-6 gap-y-1 text-[12px] text-muted">
        {r.latency_ms && <span>Latency p50 {r.latency_ms.p50} ms, p95 {r.latency_ms.p95} ms</span>}
        {r.failures !== undefined && <span>{r.failures} failures</span>}
        {extras.filter(([, v]) => v === null || typeof v !== 'object').map(([k, v]) => <span key={k}>{label(k)}: {fmt(v as Cell)}</span>)}
      </div>
      {r.confusion && <Confusion {...r.confusion} />}
      {extras.filter(([, v]) => v !== null && typeof v === 'object').map(([k, v]) => <Extra key={k} name={k} v={v as object} />)}
    </section>
  )
}

function Table({ rows, compare }: { rows: Record<string, Cell>[]; compare: boolean }) {
  const cols = [...new Set(rows.flatMap((row) => Object.keys(row)))]
  const best = (row: Record<string, Cell>, c: string) => {
    const v = row[c]
    if (!compare || typeof v !== 'number' || NOT_A_SCORE.test(c)) return false
    const rivals = rows.filter((x) => x.set === row.set).map((x) => x[c]).filter((x): x is number => typeof x === 'number')
    return rivals.length > 1 && v === (LOWER_IS_BETTER.test(c) ? Math.min : Math.max)(...rivals)
  }
  return (
    <div className="mt-3 overflow-x-auto">
      <table className="w-full text-[13px]">
        <thead>
          <tr className="border-b border-line text-left text-[11px] text-muted">
            {cols.map((c) => <th key={c} className="py-1 pr-4 font-medium">{c === 'system' ? 'System' : c}</th>)}
          </tr>
        </thead>
        <tbody>
          {rows.map((row, i) => (
            <tr key={i} className="border-b border-line last:border-0">
              {cols.map((c) => <td key={c} className={`py-1.5 pr-4 tabular-nums ${best(row, c) ? 'font-bold' : ''}`}>{fmt(row[c])}</td>)}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

/** Any other object: {name: {col: value}} becomes a table, {key: value} a row of pairs, anything else raw JSON. */
function Extra({ name, v }: { name: string; v: object }) {
  const entries = Object.entries(v)
  const nested = !Array.isArray(v) && entries.length > 0 && entries.every(([, x]) => x && typeof x === 'object' && !Array.isArray(x)
    && Object.values(x).every((y) => y === null || typeof y !== 'object'))
  const flat = !Array.isArray(v) && entries.every(([, x]) => x === null || typeof x !== 'object')
  return (
    <details className="mt-3 text-[12px]" open={nested}>
      <summary className="cursor-pointer text-muted">{label(name)}{Array.isArray(v) ? ` (${v.length} items)` : ''}</summary>
      {nested ? (
        <Table rows={entries.map(([k, x]) => ({ '': k, ...(x as Record<string, Cell>) }))} compare={false} />
      ) : flat ? (
        <div className="mt-1 flex flex-wrap gap-x-6 gap-y-1">{entries.map(([k, x]) => <span key={k}>{k}: {fmt(x as Cell)}</span>)}</div>
      ) : (
        <pre className="mt-1 max-h-60 overflow-auto rounded bg-hover p-2 text-[11px]">{JSON.stringify(v, null, 1)}</pre>
      )}
    </details>
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
                  title={`${label(labels[i])} read as ${label(labels[j])}: ${v}`}
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
