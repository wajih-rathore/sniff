import type { Fact } from './api'
import { CURRENCY } from './launcher/answer'

export const label = (s: string) => s.replace(/_/g, ' ').replace(/^./, (c) => c.toUpperCase())

// 20480.5 -> 20,480.50 and 14820 -> 14,820, like the back end's totals. A currency written before the number stays in front.
export const money = (v: string) => {
  const m = v.trim().match(/^([A-Za-z]{1,3}\.?|[$€£])?\s*(-?\d+(?:\.\d+)?)$/)
  if (!m) return v
  const n = Number(m[2])
  const s = n.toLocaleString('en-US', Number.isInteger(n) ? {} : { minimumFractionDigits: 2, maximumFractionDigits: 2 })
  return m[1] ? `${m[1]}\u00a0${s}` : s
}
export const show = (f: Pick<Fact, 'value' | 'value_type'> & { source_text?: string | null }) => {
  if (f.value_type !== 'money') return f.value
  const c = /^[\d.-]+$/.test(f.value.trim()) && f.source_text?.match(CURRENCY)?.[0]
  return money(c ? `${c} ${f.value}` : f.value)
}

// Type names as people say them: "ID card", "utility bills".
export const typeName = (t: string | null | undefined, n = 1) =>
  (t === 'id_card' ? 'ID card' : !t || t === 'other' ? 'other file' : t.replace(/_/g, ' ')) + (n === 1 ? '' : 's')
export const plural = (n: number, one: string, many: string) => `${n} ${n === 1 ? one : many}`
