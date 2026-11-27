// Small pieces every launcher screen uses, drawn as in the approved mockup.
import type { CSSProperties, ReactNode } from 'react'
import {
  Award, BookUser, BriefcaseBusiness, CircleCheck, FileText, Gauge, GraduationCap, House, IdCard, Landmark, Receipt, School, ShieldCheck,
  TriangleAlert, Umbrella, type LucideIcon,
} from 'lucide-react'

export const I = ({ i: Icon, className = '' }: { i: LucideIcon; className?: string }) => <Icon className={`ic ${className}`} strokeWidth={1.75} aria-hidden />

// One icon and hue per document type, for the tile in front of a row.
const TYPES: Record<string, [LucideIcon, number]> = {
  utility_bill: [Gauge, 50], receipt: [Receipt, 335], id_card: [IdCard, 215], passport: [BookUser, 255], certificate: [Award, 150],
  transcript: [GraduationCap, 285], bank_statement: [Landmark, 180], warranty_card: [ShieldCheck, 120], offer_letter: [BriefcaseBusiness, 30],
  insurance_policy: [Umbrella, 230], tenancy_agreement: [House, 90], fee_voucher: [School, 310],
}
export function Tile({ type, icon, tone, size = 'sm' }: { type?: string | null; icon?: LucideIcon; tone?: 'neutral' | 'warn'; size?: 'sm' | 'xs' }) {
  const t = type ? TYPES[type] : undefined
  const Icon = icon ?? t?.[0] ?? FileText
  return (
    <span className={`tile ${size} ${tone ?? (t ? '' : 'neutral')}`} style={t && !tone ? ({ '--h': t[1] } as CSSProperties) : undefined}>
      <I i={Icon} />
    </span>
  )
}

export const Keys = ({ k }: { k: string[] }) => <>{k.map((x) => <kbd key={x}>{x}</kbd>)}</>

export const St = ({ kind, children }: { kind: 'ok' | 'warn' | 'dim'; children: ReactNode }) => (
  <span className={`st ${kind}`}>{kind !== 'dim' && <I i={kind === 'ok' ? CircleCheck : TriangleAlert} />}{children}</span>
)

/** A switch. The row it sits in also toggles it with Space, so it is skipped by Tab. */
export const Tog = ({ on, label, onClick }: { on: boolean; label: string; onClick?: () => void }) => (
  <button className={`tog${on ? ' on' : ''}`} type="button" role="switch" aria-checked={on} aria-label={label} tabIndex={-1}
    onClick={(e) => { e.stopPropagation(); onClick?.() }} />
)

/** A footer action: what Enter (or another key) does, clickable too. */
export const Act = ({ k, children, run, on, opt }: { k: string[]; children: ReactNode; run?: () => void; on?: boolean; opt?: boolean }) => (
  <button type="button" className={`act${on ? ' on' : ''}${opt ? ' opt' : ''}`} onClick={run} tabIndex={-1}>{children} <Keys k={k} /></button>
)

export type Toast = { text: string; icon: LucideIcon; n: number } | null

/** The footer: one line of context (or a short-lived toast in its place) and the actions for the selection. */
export function Foot({ ctx, warn, toast, children }: { ctx: ReactNode; warn?: boolean; toast: Toast; children: ReactNode }) {
  return (
    <div className="foot">
      <div className={`ctx${toast ? ' toasting' : ''}`}>
        <span className="base">{warn && <span className="warn" style={{ display: 'flex' }}><I i={TriangleAlert} /></span>}<span>{ctx}</span></span>
        <span className="toast" role="status">{toast && <><I i={toast.icon} /><span key={toast.n}>{toast.text}</span></>}</span>
      </div>
      <div className="acts">{children}</div>
    </div>
  )
}
