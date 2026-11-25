import type { ReactNode } from 'react'
import { AlertTriangle, Check } from 'lucide-react'
import type { Fact } from './api'

export const label = (s: string) => s.replace(/_/g, ' ').replace(/^./, (c) => c.toUpperCase())

// 20480.5 -> 20,480.50 and 14820 -> 14,820, like the back end's totals. A currency written before the number stays in front.
export const money = (v: string) => {
  const m = v.trim().match(/^([A-Za-z]{1,3}\.?|[$€£])?\s*(-?\d+(?:\.\d+)?)$/)
  if (!m) return v
  const n = Number(m[2])
  const s = n.toLocaleString('en-US', Number.isInteger(n) ? {} : { minimumFractionDigits: 2, maximumFractionDigits: 2 })
  return m[1] ? `${m[1]} ${s}` : s
}
export const show = (f: Pick<Fact, 'value' | 'value_type'>) => (f.value_type === 'money' ? money(f.value) : f.value)

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
