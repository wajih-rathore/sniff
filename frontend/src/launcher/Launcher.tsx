// The launcher: one bar that finds files and facts, answers questions, and opens a few screens, all from the keyboard.
// Layout, copy and motion follow the approved mockup (sniff-agents/design-mockup.html v4).
import { Fragment, useEffect, useLayoutEffect, useRef, useState, type CSSProperties, type KeyboardEvent, type ReactNode } from 'react'
import {
  Ban, Bell, Bot, CalendarClock, CalendarPlus, Check, ChevronDown, ChevronRight, ClipboardCheck, Copy, ExternalLink, Eye, Files, Folder, FolderOpen, History,
  Keyboard, LayoutGrid, ListX, Pencil, Plus, Power, Scale, Search, SlidersHorizontal, Sparkles, TriangleAlert, Wrench, X, type LucideIcon,
} from 'lucide-react'
import {
  api, type Answer, type Document, type Fact, type Hit, type McpLogEntry, type McpSettings, type Overview, type ReminderPreview, type ReviewItem, type Source, type UpcomingItem,
  type WatchInfo,
} from '../api'
import { label, money, plural, show, typeName } from '../ui'
import { currency, fmtDay, headline } from './answer'
import { Act, Foot, I, Keys, St, Tile, Tog, type Toast } from './parts'
import { inDesktop, native } from './native'

type Screen = 'search' | 'answer' | 'review' | 'overview' | 'settings'
type Level = 'top' | 'agent' | 'advanced' | 'log'
type Src = { file_id: number; path: string; name: string; page: number }
type Item = { id: string; kind: 'file' | 'fold' | 'cmd' | 'add'; title: ReactNode; type?: string | null; icon?: LucideIcon; acc?: string; why: string; src?: Src; hit?: Hit; run?: () => void }
type PAct = { icon: LucideIcon; name: string; keys?: string[]; run: () => void }
type SRow = { id: string; head?: string; icon?: LucideIcon; tile?: ReactNode; t: string; m?: string; right?: ReactNode; chev?: boolean; add?: boolean
  ctx?: string; act?: string; run?: () => void; toggle?: () => void; on?: boolean }

const EXPIRY = ['expiry_date', 'warranty_until', 'end_date']
const ACTION_DATES = [...EXPIRY, 'due_date'] // ACTION_DATE_FIELDS in schemas.py: dates a reminder can be made for
const msg = (e: unknown) => (e as Error)?.message ?? String(e)
const plain = (v: string) => (/^-?\d+(\.\d+)?$/.test(v) ? money(v) : /^\d{4}-\d{2}-\d{2}$/.test(v) ? fmtDay(v) : v) // a stored value as people read it
const words = (q: string) => q.toLowerCase().split(/\W+/).filter((w) => w.length > 1)
const shortTitle = (s: Pick<Source, 'title' | 'name'>, type?: string | null) => (s.title || s.name).replace(type ? `, ${typeName(type)}` : /, (utility bill|receipt|bank statement|fee voucher)(?=,)/, '')
const dayDiff = (iso: string) => Math.round((new Date(new Date().toDateString()).getTime() - new Date(new Date(iso).toDateString()).getTime()) / 86_400_000)
const when = (iso: string | null) => {
  if (!iso) return ''
  const d = dayDiff(iso)
  return d === 0 ? 'Today' : d === 1 ? 'Yesterday' : fmtDay(iso.slice(0, 10)).replace(` ${new Date().getFullYear()}`, '')
}
const ago = (iso: string) => {
  const m = Math.max(0, Math.round((Date.now() - new Date(iso).getTime()) / 60_000))
  return m < 1 ? 'just now' : m < 60 ? `${plural(m, 'minute', 'minutes')} ago` : m < 1440 ? `${plural(Math.round(m / 60), 'hour', 'hours')} ago` : `${plural(Math.round(m / 1440), 'day', 'days')} ago`
}
/** The snippet from just before the first word of the query, so the match is what fits on the line. */
const around = (text: string, q: string) => {
  const i = Math.min(...words(q).map((w) => text.toLowerCase().indexOf(w)).filter((x) => x >= 0), Infinity)
  return i > 12 && i < Infinity ? '…' + text.slice(text.lastIndexOf(' ', i - 2) + 1) : text
}
/** The query's words in bold inside a title, as the mockup marks "Metro Gas". */
function bold(text: string, q: string) {
  const w = words(q).map((x) => x.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'))
  if (!w.length) return text
  return text.split(new RegExp(`\\b(${w.join('|')})`, 'gi')).map((p, i) => (i % 2 ? <b key={i}>{p}</b> : p))
}

/* ---------- window fitting, ported from the mockup: the window grows with its content up to --maxh; past that only
   the screen's list scrolls, cut on a row boundary so no row shows half. ---------- */
function offTop(el: HTMLElement, root: HTMLElement) { let t = 0; for (let n: HTMLElement | null = el; n && n !== root; n = n.offsetParent as HTMLElement | null) t += n.offsetTop; return t }
function fitWin(win: HTMLElement) {
  const area = [...win.querySelectorAll<HTMLElement>('[data-scroll]')].find((a) => a.offsetParent)
  if (!area) return
  area.style.maxHeight = ''
  if (area.scrollHeight <= area.clientHeight) return
  const room = area.clientHeight, padB = parseFloat(getComputedStyle(area).paddingBottom)
  const rows = [...area.children].flatMap((c) => (c.matches('.xbody') ? [...c.children] : [c])).filter((c) => (c as HTMLElement).offsetParent) as HTMLElement[]
  const ends = rows.map((c) => offTop(c, area) + c.offsetHeight + padB)
  area.style.maxHeight = Math.max(0, ...ends.filter((b) => b <= room)) + 'px'
}
function keepInView(el: HTMLElement | null) {
  const a = el?.closest<HTMLElement>('[data-scroll]')
  if (!el || !a || a.scrollHeight <= a.clientHeight) return
  const cs = getComputedStyle(a), y = offTop(el, a)
  const top = y - parseFloat(cs.paddingTop), bottom = y + el.offsetHeight + parseFloat(cs.paddingBottom) - a.clientHeight
  if (a.scrollTop > top) a.scrollTop = top
  else if (a.scrollTop < bottom) a.scrollTop = bottom
}
// The Ctrl+K panel never covers the selected row: it sits bottom-right above the footer, or above the row, or else below
// it with the row moved to the top of the list; a short window grows (within its cap) to make that room.
function placePanel(sec: HTMLElement, p: HTMLElement) {
  const list = sec.querySelector<HTMLElement>('[data-scroll]'), r0 = list?.querySelector<HTMLElement>('[aria-selected=true]')
  const bar = sec.querySelector<HTMLElement>('.bar')!.offsetHeight, foot = sec.querySelector<HTMLElement>('.foot')!, h = p.offsetHeight
  let top = foot.offsetTop - 8 - h
  if (list && r0) {
    const row = () => { const y = offTop(r0, sec) - list.scrollTop; return [y, y + r0.offsetHeight] }
    let [t, b] = row()
    if (top < b + 8) top = t - 8 - h
    if (top < bar + 8) {
      const cs = getComputedStyle(list), want = list.scrollTop + t - offTop(list, sec) - parseFloat(cs.paddingTop)
      const short = want - (list.scrollHeight - list.clientHeight)
      if (short > 0) list.style.paddingBottom = parseFloat(cs.paddingBottom) + short + 'px'
      list.scrollTop = want;
      [t, b] = row(); top = b + 8
      sec.style.minHeight = Math.max(sec.offsetHeight, top + h + 8 + foot.offsetHeight) + 'px'
    }
  } else if (top < bar + 8) sec.style.minHeight = bar + 16 + h + foot.offsetHeight + 'px'
  p.style.top = Math.max(bar + 8, top) + 'px'
}

export default function Launcher() {
  const [screen, setScreen] = useState<Screen>('search')
  const [q, setQ] = useState('')
  const [sel, setSel] = useState(0)
  const [listMode, setListMode] = useState(false) // arrowed into the list since the last keystroke: Space previews
  const [hits, setHits] = useState<Hit[] | null>(null)
  const [busy, setBusy] = useState(false)
  const [more, setMore] = useState(false)
  const [preview, setPreview] = useState(false)
  const [paneAr, setPaneAr] = useState<number | null>(null) // the preview pane's free height over its width, for the page crop
  const [pvSel, setPvSel] = useState(0) // the row shown in the preview: follows the selection after a 120 ms dwell
  const [recent, setRecent] = useState<Document[] | null>(null)
  const [panel, setPanel] = useState(false)
  const [pSel, setPSel] = useState(0)
  const [pq, setPq] = useState('')
  const [toast, setToast] = useState<Toast>(null)
  const [err, setErr] = useState('')
  // answer
  const [question, setQuestion] = useState('')
  const [ans, setAns] = useState<Answer | null>(null)
  const [stream, setStream] = useState('')
  const [asking, setAsking] = useState(false)
  const [starting, setStarting] = useState(false) // the chat model is loading: the first written answer waits for it
  const [details, setDetails] = useState(false)
  const [xOpen, setXOpen] = useState<Record<string, boolean>>({})
  // review
  const [queue, setQueue] = useState<ReviewItem[] | null>(null)
  const [total, setTotal] = useState(0)
  const [edit, setEdit] = useState<string | null>(null)
  const [editErr, setEditErr] = useState('')
  // overview and settings
  const [ov, setOv] = useState<Overview | null>(null)
  const [up, setUp] = useState<UpcomingItem[]>([])
  const [queued, setQueued] = useState(0)
  const [watch, setWatch] = useState<WatchInfo | null>(null)
  const [level, setLevel] = useState<Level>('top')
  const [mcp, setMcp] = useState<McpSettings | null>(null)
  const [log, setLog] = useState<McpLogEntry[] | null>(null)
  const [hotkey, setHotkey] = useState('Alt+Shift+Space') // the desktop app's saved one replaces this on load
  const [capturing, setCapturing] = useState(false)
  const [hotkeyNote, setHotkeyNote] = useState<string | null>(null)
  const [autostart, setAutostart] = useState(false)
  const [adding, setAdding] = useState(false) // typing a folder path in Settings
  const [google, setGoogle] = useState(false) // Google Calendar connected
  const [confirm, setConfirm] = useState<{ p: ReminderPreview; f: Fact } | null>(null) // what Google would receive, shown before sending

  const input = useRef<HTMLInputElement>(null)
  const pInput = useRef<HTMLInputElement>(null)
  const win = useRef<HTMLDivElement>(null)
  const sec = useRef<HTMLElement>(null)
  const panelEl = useRef<HTMLDivElement>(null)
  const seq = useRef(0) // drops search and ask responses that belong to an older query
  const lastH = useRef(0)
  const hAnim = useRef<Animation | null>(null)
  const toastT = useRef(0)

  const say = (text: string, icon: LucideIcon = Check) => {
    setErr('')
    setToast((t) => ({ text, icon, n: (t?.n ?? 0) + 1 }))
    clearTimeout(toastT.current)
    toastT.current = window.setTimeout(() => setToast(null), 2400)
  }
  const fail = (what: string) => (e: unknown) => setErr(`Could not ${what}: ${msg(e)}`)
  const attempt = (p: Promise<unknown>, done: string, icon: LucideIcon, what: string) => p.then(() => say(done, icon), fail(what))
  const open = (s: Src) => attempt(native.openFile(s.file_id, s.path, s.page).then(native.hide), `Opening ${s.name} at page ${s.page}`, ExternalLink, 'open the file')
  const reveal = (path: string) => attempt(native.reveal(path), `Showing ${path.split(/[\\/]/).pop()} in File Explorer`, Check, 'show it in File Explorer')
  const copy = (text: string, done = `Copied ${text}`) => attempt(navigator.clipboard.writeText(text), done, Check, 'copy')
  const loadRecent = () => api.recent(3).then(setRecent, (e) => { setRecent([]); fail('reach Sniff')(e) })

  useEffect(() => {
    loadRecent()
    native.autostart().then(setAutostart, () => {})
    api.calendarStatus().then((s) => setGoogle(s.connected), () => setGoogle(false)) // no answer: offer only the .ics file
    native.hotkey().then(setHotkey, () => {})
    native.hotkeyNote().then(setHotkeyNote, () => {})
    native.backdrop().then((b) => { document.documentElement.dataset.backdrop = b }) // 'solid': no see-through backdrop here
    const sizes = () => { // in the desktop app the window is sized from the monitor's work area, which the webview calls screen.avail*
      if (inDesktop) document.documentElement.style.setProperty('--maxh', `${Math.min(540, Math.floor(window.screen.availHeight * 0.8))}px`)
    }
    document.documentElement.classList.toggle('desktop', inDesktop)
    sizes()
    const onGo = (e: Event) => go((e as CustomEvent<Screen>).detail) // the tray's Settings item
    const onFocus = () => { sizes(); input.current?.focus(); loadRecent() } // the hotkey shows the window: be ready to type
    window.addEventListener('sniff:go', onGo)
    window.addEventListener('focus', onFocus)
    return () => { window.removeEventListener('sniff:go', onGo); window.removeEventListener('focus', onFocus) }
  }, [])

  // Search as you type, 120 ms after the last key. Tab asks.
  useEffect(() => {
    if (screen !== 'search') return
    const id = ++seq.current
    setSel(0); setMore(false); setListMode(false); setErr('')
    const query = q.trim()
    if (!query || query.startsWith('/')) { setHits(null); setBusy(false); return }
    const t = setTimeout(async () => {
      setBusy(true)
      try {
        const h = await api.search(query)
        if (id === seq.current) setHits(h)
      } catch (e) {
        if (id === seq.current) { setHits([]); fail('search')(e) }
      }
      if (id === seq.current) setBusy(false)
    }, 120)
    return () => clearTimeout(t)
  }, [q, screen])

  useEffect(() => { const t = setTimeout(() => setPvSel(sel), preview ? 120 : 0); return () => clearTimeout(t) }, [sel, preview]) // the dwell keeps arrowing instant

  /* ---------- screens ---------- */
  const go = (s: Screen) => {
    setScreen(s); setSel(0); setQ(''); setPanel(false); setPreview(false); setErr(''); setEdit(null); setEditErr(''); setCapturing(false); setAdding(false)
    if (s === 'search') loadRecent()
    if (s === 'review') { setQueue(null); api.review().then((l) => { setQueue(l); setTotal(l.length) }, fail('load the facts to review')) }
    if (s === 'overview') {
      setOv(null)
      api.overview().then((o) => { setOv(o); setSel(o.conflicts ? 5 : 4) }, fail('load the overview')) // Facts to review, as in the mockup
      api.upcoming().then(setUp, () => setUp([]))
      api.indexStatus().then((x) => setQueued(x.queued), () => setQueued(0))
      api.watched().then(setWatch, () => {})
      api.review().then(setQueue, () => {})
    }
    if (s === 'settings') {
      setLevel('top')
      api.watched().then(setWatch, fail('load the folders'))
      api.mcpSettings().then(setMcp, fail('load agent access'))
      api.mcpLog().then(setLog, () => setLog([]))
    }
    input.current?.focus()
  }
  const ask = (text: string) => {
    const id = ++seq.current
    setScreen('answer'); setQuestion(text); setAns(null); setStream(''); setAsking(true); setStarting(false); setDetails(false); setXOpen({}); setErr(''); setPanel(false); setPreview(false); setSel(0)
    // Nothing arrives while the chat model loads (seconds on a cold start), so ask the back end whether it is loading.
    const poll = window.setInterval(() => api.model().then((m) => id === seq.current && setStarting(m.starting), () => {}), 1500)
    api.askStream(text, (t) => { if (id === seq.current) { clearInterval(poll); setStarting(false); setStream((s) => s + t) } })
      .then((a) => { if (id === seq.current) setAns(a) }, (e) => {
        if (id !== seq.current) return
        // the openai client's message when nothing answers on the chat model's port
        if (/APIConnectionError|Connection error/.test(msg(e))) setErr('The chat model is not running, so Sniff cannot answer this. Search works without it')
        else fail('answer')(e)
      })
      .finally(() => { clearInterval(poll); if (id === seq.current) { setAsking(false); setStarting(false) } })
  }

  /* ---------- search and empty state ---------- */
  const query = q.trim()
  const COMMANDS: [string, string, LucideIcon, Screen][] = [
    ['Overview', 'What is indexed, what expires, what needs checking', LayoutGrid, 'overview'],
    ['Review facts', 'One fact at a time, with its page', ClipboardCheck, 'review'],
    ['Settings', 'Hotkey, folders and agent access', SlidersHorizontal, 'settings'],
  ]
  const fileItem = (h: Hit): Item => ({
    id: `h${h.file_id}`, kind: 'file', title: bold(h.title || h.name, query), type: h.doc_type, hit: h,
    acc: h.date ? fmtDay(h.date) : h.reading ? 'Reading…' : `p. ${h.page}`, why: h.match_reason, src: h,
  })
  const strong = (hits ?? []).filter((h) => h.match !== 'meaning'), weak = (hits ?? []).filter((h) => h.match === 'meaning')
  const fold = strong.length > 0 && weak.length > 0 && !more
  let items: Item[] = []
  if (screen === 'search') {
    if (query.startsWith('/'))
      items = COMMANDS.filter(([n]) => n.toLowerCase().startsWith(query.slice(1).toLowerCase().trim()))
        .map(([n, d, icon, s]) => ({ id: `c${s}`, kind: 'cmd', title: n, icon, why: d, run: () => go(s) }))
    else if (query) {
      items = [...strong.map(fileItem), ...(fold
        ? [{ id: 'fold', kind: 'fold', title: plural(weak.length, 'weaker match', 'weaker matches'), icon: ChevronDown, why: 'Other files, matched by meaning rather than by your words' } as Item]
        : weak.map(fileItem))]
    } else if (recent?.length)
      items = recent.map((d) => ({ id: `r${d.id}`, kind: 'file', title: d.title || d.name, type: d.doc_type, acc: when(d.indexed_at), why: d.path,
        src: { file_id: d.id, path: d.path, name: d.name, page: 1 } }))
    else if (recent)
      items = [{ id: 'add', kind: 'add', title: 'Add a folder…', icon: Plus, why: 'Sniff reads the documents in the folders you choose', run: () => { go('settings'); setAdding(true) } }]
  }
  const at = Math.min(sel, Math.max(items.length - 1, 0))
  const cur = items[at] as Item | undefined
  const pv = items[Math.min(pvSel, items.length - 1)] as Item | undefined
  const canPreview = !!cur && (cur.kind === 'file' || cur.kind === 'fold')
  const showPane = screen === 'search' && preview && canPreview
  const enterItem = (it?: Item) => {
    if (!it) return
    if (it.kind === 'fold') { setMore(true); setSel(strong.length); return }
    if (it.src) open(it.src)
    else it.run?.()
  }
  const searchNote = screen !== 'search' ? ''
    : query && !query.startsWith('/') && hits && !hits.length && !busy ? 'No files match. Press Tab to ask instead.'
    : query.startsWith('/') && !items.length ? 'No such command. Try /overview, /review or /settings.'
    : ''

  /* ---------- answer ---------- */
  const h = ans ? headline(ans) : null
  const alts = ans?.alternatives ?? []
  const choices = alts.length
    ? alts.map((x, i) => { const v = ans!.conflicts.flatMap((c) => c.values).find((v) => v.fact_id === x.fact_id); return { key: x.key, fact_id: x.fact_id, src: x.source, value: v ? plain(v.value) : h?.alts?.[i] ?? '' } })
    : (ans?.conflicts[0]?.values ?? []).map((v) => ({ key: ans!.conflicts[0].key, fact_id: v.fact_id, src: v.source, value: plain(v.value) }))
  const disputed = new Set(alts.map((x) => x.fact_id))
  const conflictFiles = new Set(ans?.conflicts.flatMap((c) => c.values.map((v) => v.source.file_id)) ?? [])
  const facts = (ans?.facts ?? []).filter((f) => !disputed.has(f.id))
  const sourceOf = (file_id: number) => [...(ans?.sources ?? []), ...(ans?.counted ?? []), ...alts.map((x) => x.source)].find((s) => s.file_id === file_id)
  type DRow = { id: string; kind: 'fact' | 'x'; run: () => void }
  const dRows: DRow[] = ans ? [
    ...facts.map((f) => ({ id: `f${f.id}`, kind: 'fact' as const, run: () => { const s = sourceOf(f.file_id); if (s) open({ ...s, page: f.page }) } })),
    ...(alts.length ? [{ id: 'disputed', kind: 'fact' as const, run: () => setXOpen((o) => ({ ...o, conflict: true })) }] : []),
    ...(ans.skipped_duplicates.length ? [{ id: 'skip', kind: 'x' as const, run: () => setXOpen((o) => ({ ...o, skip: !o.skip })) }] : []),
    ...(choices.length ? [{ id: 'conflict', kind: 'x' as const, run: () => setXOpen((o) => ({ ...o, conflict: !o.conflict })) }] : []),
  ] : []
  const hasDetails = dRows.length > 0
  const conflictOpen = xOpen.conflict ?? alts.length > 0
  const pick = (i: number) => {
    const c = choices[i]
    if (!c) return
    api.pick(c.key, c.fact_id).then(() => api.ask(question)).then((a) => {
      setAns(a); setDetails(false); setXOpen({})
      say(`Using ${c.value} from ${c.src.name}`)
    }, fail('save your choice'))
  }
  const showDetails = (on: boolean) => {
    if (!hasDetails && on) return
    setDetails(on)
    setSel(on ? Math.max(0, dRows.findIndex((r) => r.id === 'conflict' || r.id === 'disputed')) : 0)
  }
  const answerPrimary = () => {
    if (!ans) return
    if (choices.length && alts.length) return showDetails(true)
    copy(h?.value ?? ans.answer, `Copied ${h?.value ?? 'the answer'}`)
  }

  /* ---------- review ---------- */
  const item = queue?.[0]
  const doneN = total - (queue?.length ?? 0)
  const next = (done: string) => { setQueue((l) => l && l.slice(1)); setEdit(null); setEditErr(''); say(done); input.current?.focus() }
  const accept = () => item && api.approve(item.fact.id).then((f) => next(`Saved ${show(f)} as the ${label(f.field).toLowerCase()}`), fail('save'))
  const correct = (value: string) => item && api.correct(item.fact.id, value).then(
    (f) => next(`Saved ${show(f)} as the ${label(f.field).toLowerCase()}`), (e) => setEditErr(msg(e)))
  const skip = () => { if (!item) return; setQueue((l) => l && l.length > 1 ? [...l.slice(1), l[0]] : l); setEdit(null); say('Skipped. It stays in the review list') }
  const reviewSrc = item && { file_id: item.fact.file_id, path: item.path, name: item.document, page: item.fact.page }

  /* ---------- overview ---------- */
  const reviewDocs = new Set((queue ?? []).map((r) => r.fact.file_id)).size
  const expiring = up.filter((u) => EXPIRY.includes(u.fact.field) && u.days_left >= 0)
  const oRows: SRow[] = ov ? [
    { id: 'files', tile: <Tile icon={Files} tone="neutral" />, t: 'Files indexed',
      m: `${plural(ov.total_files, 'file', 'files')}${watch?.folders.length ? ` in ${plural(watch.folders.length, 'folder', 'folders')}` : ''}, ${plural(ov.documents, 'document', 'documents')}`,
      right: ov.errors ? <St kind="warn">{ov.errors} not read</St> : queued ? <St kind="dim">Reading {queued}</St> : <St kind="ok">Up to date</St>,
      ctx: 'Folders and their status are in Settings', act: 'Open Settings', run: () => go('settings') },
    { id: 'types', tile: <Tile icon={LayoutGrid} tone="neutral" />, t: 'Documents by type',
      m: Object.entries(ov.by_type).sort((a, b) => b[1] - a[1]).slice(0, 4).map(([t, n]) => `${n} ${typeName(t, n)}`).join(' · ')
        + (Object.keys(ov.by_type).length > 4 ? `, and ${plural(Object.keys(ov.by_type).length - 4, 'more type', 'more types')}` : '') || 'None yet', ctx: 'Copies of a file are counted once' },
    { id: 'expiry', tile: <Tile icon={CalendarClock} tone="neutral" />, t: 'Upcoming expiries',
      m: expiring.length ? expiring.slice(0, 2).map((u) => `${label(typeName(u.doc_type))}, ${fmtDay(u.fact.value)}`).join(' · ') : 'Nothing expires in the next 90 days',
      right: expiring[0] && <span className="acc">{expiring[0].days_left === 0 ? 'today' : `next in ${plural(expiring[0].days_left, 'day', 'days')}`}</span>,
      ctx: expiring[0] ? `Opens ${expiring[0].document} at page ${expiring[0].fact.page}` : 'Warranties, ID cards, passports, policies and tenancies',
      act: expiring[0] && 'Open', run: expiring[0] && (() => open({ file_id: expiring[0].fact.file_id, path: expiring[0].path, name: expiring[0].document, page: expiring[0].fact.page })) },
    { id: 'missing', tile: <Tile icon={ListX} tone="neutral" />, t: 'Missing fields',
      m: ov.missing_required.length
        ? `${ov.missing_required[0].name} has no ${ov.missing_required[0].fields.map((f) => label(f).toLowerCase()).join(', ')}${ov.missing_required.length > 1 ? ` · ${ov.missing_required.length - 1} more` : ''}`
        : 'Every document has the fields its type needs', ctx: 'Fields a document of its type should have',
      act: ov.missing_required[0] && 'Open', run: ov.missing_required[0] && (() => api.document(ov.missing_required[0].file_id).then((d) => open({ file_id: d.id, path: d.path, name: d.name, page: 1 }), fail('open it'))) },
    ...(ov.conflicts ? [{ id: 'conflicts', tile: <Tile icon={Scale} tone="warn" />, t: 'Conflicts', m: `${plural(ov.conflicts, 'record has', 'records have')} copies that disagree`,
      ctx: 'Ask about the record to pick the right value' }] : []),
    { id: 'review', tile: <Tile icon={ClipboardCheck} tone={ov.needs_review ? 'warn' : 'neutral'} />, t: 'Facts to review',
      m: ov.needs_review ? `${plural(ov.needs_review, 'fact', 'facts')} in ${plural(reviewDocs || 1, 'document', 'documents')}` : 'Nothing to review',
      ctx: 'Shows one fact at a time, with its page', act: 'Start review', run: () => go('review') },
  ] : []

  /* ---------- settings ---------- */
  const types = mcp ? Object.keys(mcp.fields).filter((t) => t !== 'other') : []
  const allowed = (t: string) => (mcp?.allow[t] ?? []).length > 0
  const allowText = (t: string) => {
    const a = mcp?.allow[t] ?? [], n = mcp?.fields[t]?.length ?? 0
    return !a.length ? 'Hidden' : a.includes('*') ? 'Every field and the page text' : a.length >= n ? `All ${n} fields` : `${a.length} of ${n} fields`
  }
  const saveMcp = (enabled: boolean, allow: McpSettings['allow']) => api.saveMcp(enabled, allow).then(setMcp, fail('save'))
  const flipType = (t: string) => { if (!mcp) return; const allow = { ...mcp.allow }; if (allowed(t)) delete allow[t]; else allow[t] = mcp.fields[t]; saveMcp(mcp.enabled, allow) }
  const toggleAutostart = () => { const on = !autostart; attempt(native.setAutostart(on).then(() => setAutostart(on)), on ? 'Sniff starts with Windows' : 'Sniff no longer starts with Windows', Check, 'change this') }
  const folderStatus = (f: string) => {
    const c = watch?.counts?.[f]
    return !c ? null : c.errors ? <St kind="warn">{c.errors} not read</St> : c.waiting ? <St kind="dim">Reading {c.waiting}</St> : <St kind="ok">All read</St>
  }
  const sub = (l: Level) => { setLevel(l); setSel(0); setQ(''); setErr('') }
  const sRows: SRow[] = level === 'top' ? [
    { id: 'hotkey', icon: Keyboard, t: 'Hotkey', m: capturing ? 'Press the new shortcut, or Esc to keep this one' : hotkeyNote ?? 'Opens Sniff from any app',
      right: <span className="keys"><Keys k={hotkey.split('+')} /></span>, act: 'Change', run: () => setCapturing(true) },
    ...(watch?.folders ?? []).map((f, i): SRow => ({ id: `w${f}`, head: i ? undefined : 'Folders', icon: Folder, t: f,
      m: watch?.counts?.[f] ? plural(watch.counts[f].files, 'file', 'files') : undefined, right: folderStatus(f), act: 'Show in File Explorer', run: () => reveal(f) })),
    { id: 'add', head: watch?.folders.length ? undefined : 'Folders', icon: Plus, t: 'Add folder…', add: true, act: 'Add', run: () => { setAdding(true); setQ('') } },
    { id: 'agent', head: 'More', icon: Bot, t: 'Agent access', m: mcp ? (mcp.enabled ? `On for ${plural(types.filter(allowed).length, 'document type', 'document types')}` : 'Off') : '', chev: true, act: 'Open', run: () => sub('agent') },
    { id: 'advanced', icon: Wrench, t: 'Advanced', m: `Start with Windows: ${autostart ? 'on' : 'off'}`, chev: true, act: 'Open', run: () => sub('advanced') },
  ] : level === 'agent' ? [
    { id: 'mcp', icon: Bot, t: 'Let AI agents read Sniff',
      m: mcp?.env_override ? 'Set by SNIFF_MCP_ALLOW, so this switch does nothing' : mcp?.enabled ? 'Each request is logged' : 'Every request is refused and logged',
      on: !!mcp?.enabled, toggle: () => mcp && saveMcp(!mcp.enabled, mcp.allow) },
    ...types.map((t, i): SRow => ({ id: `t${t}`, head: i ? undefined : 'What agents may read', tile: <Tile type={t} size="xs" />, t: label(typeName(t, 2)), m: allowText(t),
      on: allowed(t), toggle: () => flipType(t) })),
    { id: 'log', icon: History, t: 'Access log', m: log?.length ? `Last request ${ago(log[0].time)}` : 'No agent has asked anything yet', chev: true, act: 'Open', run: () => sub('log') },
  ] : level === 'advanced' ? [
    { id: 'autostart', icon: Power, t: 'Start with Windows', m: autostart ? 'Sniff starts in the tray when you sign in' : 'Off', on: autostart, toggle: toggleAutostart },
  ] : (log ?? []).map((l, i): SRow => ({ id: `l${i}`, icon: l.refused ? Ban : Bot, t: `${l.tool}: ${Object.values(l.args).map(String).join(', ')}`,
    m: l.refused ? `Refused, ${ago(l.time)}` : `${plural(l.ids.length, 'document', 'documents')}, ${ago(l.time)}` }))
  const listRows = screen === 'overview' ? oRows : screen === 'settings' ? sRows : []
  const filtered = (screen === 'overview' || (screen === 'settings' && level === 'top')) && query && !adding
    ? listRows.filter((r) => `${r.t} ${r.m ?? ''}`.toLowerCase().includes(query.toLowerCase())).map((r) => ({ ...r, head: undefined }))
    : listRows
  const rAt = Math.min(sel, Math.max(filtered.length - 1, 0))
  const rCur = filtered[rAt]

  /* ---------- Ctrl+K actions ---------- */
  // Reminders for a date: a local .ics file always; Google Calendar only when connected, after showing what it receives.
  const remindActs = (f?: Fact | null): PAct[] => !f || !ACTION_DATES.includes(f.field) ? [] : [
    { icon: CalendarPlus, name: 'Add to calendar (.ics)', run: () => attempt(native.openIcs(api.icsPath(f.file_id, f.field)),
      inDesktop ? 'Opened the reminder in your calendar app' : 'Downloaded the reminder as an .ics file', Check, 'make the calendar file') },
    ...(google ? [{ icon: Bell, name: 'Add to Google Calendar…', run: () => api.reminderPreview(f.file_id, f.field).then((p) => { setConfirm({ p, f }); setPanel(true); setPSel(0) }, fail('ask Google Calendar')) }] : []),
  ]
  const goActs: PAct[] = COMMANDS.filter(([, , , s]) => s !== screen).map(([n, , icon, s]) => ({ icon, name: n, run: () => go(s) }))
  const fileActs = (s: Src): PAct[] => [
    { icon: ExternalLink, name: `Open at page ${s.page}`, keys: ['Enter'], run: () => open(s) },
    { icon: FolderOpen, name: 'Show in File Explorer', keys: ['Ctrl', 'Enter'], run: () => reveal(s.path) },
    { icon: Copy, name: 'Copy file path', keys: ['Ctrl', 'Shift', 'C'], run: () => copy(s.path) },
    ...(screen === 'search' ? [{ icon: Eye, name: 'Preview page', keys: ['Space'], run: () => setPreview(true) }] : []),
  ]
  const ctxActs: PAct[] = [
    ...(screen === 'search' && cur?.src ? [...fileActs(cur.src), ...remindActs(cur.hit?.fact)] : []),
    ...(screen === 'answer' && ans ? [
      { icon: Copy, name: 'Copy answer', keys: alts.length ? undefined : ['Enter'], run: () => copy(h?.value ?? ans.answer, 'Copied the answer') },
      ...ans.sources.slice(0, 4).map((s) => ({ icon: ExternalLink, name: `Open ${shortTitle(s)}`, run: () => open(s) })),
      ...(ans.unreviewed.length ? [{ icon: ClipboardCheck, name: 'Review the unchecked values', run: () => go('review') }] : []),
      ...remindActs(ans.facts.filter((f) => ACTION_DATES.includes(f.field)).length === 1 ? ans.facts.find((f) => ACTION_DATES.includes(f.field)) : null),
    ] : []),
    ...(screen === 'review' && reviewSrc ? [{ icon: ExternalLink, name: `Open page ${reviewSrc.page}`, keys: ['Enter'], run: () => open(reviewSrc) }] : []),
    ...(screen === 'overview' && rCur?.id === 'expiry' ? remindActs(expiring[0]?.fact) : []),
    ...(screen === 'settings' && level === 'top' && rCur?.id.startsWith('w') ? [
      { icon: FolderOpen, name: 'Show in File Explorer', keys: ['Enter'], run: () => reveal(rCur.t) },
      { icon: ListX, name: 'Stop watching this folder', run: () => attempt(api.unwatch(rCur.t).then(setWatch), `Stopped watching ${rCur.t}`, Check, 'stop watching') },
    ] : []),
  ]
  // Screens (Overview, Review, Settings) are listed when nothing is selected, and found by typing their name.
  const acts = [...ctxActs, ...(ctxActs.length && !pq.trim() ? [] : goActs)].filter((a) => a.name.toLowerCase().includes(pq.trim().toLowerCase()))
  const sendActs: PAct[] = confirm ? [
    { icon: Check, name: 'Add to Google Calendar', keys: ['Enter'], run: () => attempt(api.remind(confirm.f.id), `Added to Google Calendar for ${fmtDay(confirm.p.date)}`, Check, 'add it to Google Calendar') },
    { icon: X, name: 'Cancel', keys: ['Esc'], run: () => {} },
  ] : []
  const pActs = confirm ? sendActs : acts
  const openPanel = () => { setPreview(false); setPanel(true); setPSel(0); setPq(''); setConfirm(null) }
  const closePanel = () => { setPanel(false); setConfirm(null); input.current?.focus() }
  const runAct = (a?: PAct) => { if (!a) return; closePanel(); a.run() }

  /* ---------- layout after every render: fit the window, keep the selection in view, place the panel ---------- */
  useLayoutEffect(() => {
    const w = win.current, s = sec.current
    if (!w || !s) return
    if (!panel) { s.style.minHeight = ''; s.querySelectorAll<HTMLElement>('[data-scroll]').forEach((l) => { l.style.paddingBottom = '' }) }
    fitWin(w)
    keepInView(s.querySelector<HTMLElement>('[data-scroll] [aria-selected=true]'))
    const pane = s.querySelector<HTMLElement>('.pane') // the crop fills the pane: 16 px padding, 12 px gap, then the field line
    if (pane) { const ar = Math.floor((pane.clientHeight - 44 - (pane.querySelector<HTMLElement>('.pv-field')?.offsetHeight ?? 20)) / (pane.clientWidth - 32) * 100) / 100; if (ar > 0 && ar !== paneAr) setPaneAr(ar) }
    if (panel && panelEl.current) placePanel(s, panelEl.current)
    // offsetHeight is a frame of a running height animation: note it, stop the animation, then measure what the content needs
    const from = w.offsetHeight
    hAnim.current?.cancel()
    const h1 = w.offsetHeight
    if (h1 !== lastH.current && inDesktop) native.fitWindow(h1).catch(() => {})
    lastH.current = h1
    if (from !== h1 && !inDesktop && !matchMedia('(prefers-reduced-motion: reduce)').matches)
      hAnim.current = w.animate([{ height: from + 'px' }, { height: h1 + 'px' }], { duration: 167, easing: 'cubic-bezier(0,0,0,1)' })
  })
  useLayoutEffect(() => { // each screen slides in, as in the mockup
    const s = sec.current
    if (!s) return
    s.classList.remove('anim-in'); void s.offsetWidth; s.classList.add('anim-in')
  }, [screen, level])
  useEffect(() => { if (panel) pInput.current?.focus() }, [panel])

  /* ---------- keys ---------- */
  const captureHotkey = (e: KeyboardEvent) => {
    e.preventDefault()
    if (e.key === 'Escape') return setCapturing(false)
    if (['Control', 'Alt', 'Shift', 'Meta'].includes(e.key)) return
    if (!e.ctrlKey && !e.altKey && !e.metaKey) return setErr('Use Ctrl, Alt or Win together with a key')
    const combo = [e.ctrlKey && 'Ctrl', e.altKey && 'Alt', e.shiftKey && 'Shift', e.metaKey && 'Super', e.key === ' ' ? 'Space' : e.key.length === 1 ? e.key.toUpperCase() : e.key].filter(Boolean).join('+')
    setCapturing(false)
    attempt(native.setHotkey(combo).then(() => setHotkey(combo)), `The hotkey is now ${combo}`, Check, `use ${combo}`)
  }
  const atEnd = () => { const el = input.current; return !el || el.readOnly || el.selectionStart === el.value.length }
  const move = (e: KeyboardEvent, n: number, i: number, set: (i: number) => void) => {
    if (e.key !== 'ArrowDown' && e.key !== 'ArrowUp') return false
    e.preventDefault()
    if (n) set(Math.max(0, Math.min(n - 1, i + (e.key === 'ArrowDown' ? 1 : -1))))
    return true
  }
  const escape = () => {
    if (panel) return closePanel()
    if (screen === 'search') { if (preview) setPreview(false); else if (q) setQ(''); else native.hide() }
    else if (screen === 'answer') { if (details) showDetails(false); else { setScreen('search'); setQ(question) } }
    else if (screen === 'settings' && adding) { setAdding(false); setQ('') }
    else if (screen === 'settings' && level !== 'top') sub(level === 'log' ? 'agent' : 'top')
    else go('search')
  }
  const onKey = (e: KeyboardEvent<HTMLDivElement>) => {
    const k = e.key, ctrl = e.ctrlKey || e.metaKey, ctrlK = ctrl && k.toLowerCase() === 'k'
    if (capturing) return captureHotkey(e)
    if (panel) {
      if (k === 'ArrowDown' || k === 'ArrowUp') { e.preventDefault(); if (pActs.length) setPSel((pSel + (k === 'ArrowDown' ? 1 : -1) + pActs.length) % pActs.length) }
      else if (k === 'Enter') { e.preventDefault(); runAct(pActs[pSel]) }
      else if (k === 'Escape' || ctrlK) { e.preventDefault(); closePanel() }
      return
    }
    if (k === 'Escape') { e.preventDefault(); return escape() }
    if (ctrlK) { e.preventDefault(); return openPanel() }
    if (edit !== null) return // the correction box has its own keys
    if (screen === 'search') {
      if (move(e, items.length, at, (i) => { setSel(i); setListMode(true) })) return
      if (k === 'Enter') { e.preventDefault(); if (ctrl && cur?.src) reveal(cur.src.path); else enterItem(cur) }
      else if (ctrl && e.shiftKey && k.toLowerCase() === 'c' && cur?.src) { e.preventDefault(); copy(cur.src.path) }
      else if (k === 'Tab') { e.preventDefault(); if (query && !query.startsWith('/')) ask(query) }
      else if (k === ' ' && (listMode || preview) && canPreview) { e.preventDefault(); setPreview(!preview) }
      else if (k === 'ArrowRight' && atEnd() && !preview && canPreview) { e.preventDefault(); setPreview(true) }
      else if (k === 'ArrowLeft' && preview) { e.preventDefault(); setPreview(false) }
    } else if (screen === 'answer') {
      if (k === 'Tab' || (k === 'ArrowRight' && !details && atEnd()) || (k === 'ArrowLeft' && details)) { e.preventDefault(); showDetails(!details); return }
      if (details) {
        if (move(e, dRows.length, sel, setSel)) return
        if (/^[1-9]$/.test(k) && choices[+k - 1]) { e.preventDefault(); pick(+k - 1) }
        else if (k === 'Enter') { e.preventDefault(); dRows[sel]?.run() }
      } else if (k === 'Enter') { e.preventDefault(); answerPrimary() }
    } else if (screen === 'review') {
      if (!item || ctrl) return
      const key = k.toLowerCase()
      if (key === 'a') { e.preventDefault(); accept() }
      else if (key === 'c') { e.preventDefault(); setEdit(show(item.fact).replace(/\u00a0/g, ' ')) }
      else if (key === 's') { e.preventDefault(); skip() }
      else if (k === 'Enter' && reviewSrc) { e.preventDefault(); open(reviewSrc) }
    } else {
      if (adding) {
        if (k === 'Enter' && query) { e.preventDefault(); attempt(api.watch(query).then((w) => { setWatch(w); setAdding(false); setQ('') }), `Watching ${query}`, Check, 'watch this folder') }
        return
      }
      if (move(e, filtered.length, rAt, setSel)) return
      if (k === 'Enter') { e.preventDefault(); (rCur?.run ?? rCur?.toggle)?.() }
      else if (k === ' ' && rCur?.toggle && (level !== 'top' || !query)) { e.preventDefault(); rCur.toggle() }
      else if (k === 'Backspace' && screen === 'settings' && level !== 'top') { e.preventDefault(); sub(level === 'log' ? 'agent' : 'top') }
    }
  }

  /* ---------- render ---------- */
  const readOnly = screen === 'review' || (screen === 'settings' && level !== 'top') || capturing
  const chip: [LucideIcon, string] | null = screen === 'review' ? [ClipboardCheck, 'Review'] : screen === 'overview' ? [LayoutGrid, 'Overview']
    : screen === 'settings' ? (adding ? [Plus, 'Add folder'] : level === 'agent' ? [Bot, 'Agent access'] : level === 'advanced' ? [Wrench, 'Advanced'] : level === 'log' ? [History, 'Access log'] : [SlidersHorizontal, 'Settings'])
    : null
  const placeholder = screen === 'search' ? 'Search files or ask a question…' : screen === 'overview' ? 'Filter…'
    : screen === 'settings' && adding ? 'Type a folder path, then press Enter' : screen === 'settings' && level === 'top' ? 'Filter settings…' : ''
  const loading = (screen === 'search' && busy) || (screen === 'answer' && asking)
  const listId = `list-${screen}`
  const activeId = screen === 'search' && cur ? `o-${cur.id}` : (screen === 'overview' || screen === 'settings') && rCur ? `o-${rCur.id}` : undefined

  const bar = (
    <div className="bar">
      {chip ? <span className="chip"><I i={chip[0]} />{chip[1]}</span> : <I i={Search} />}
      <input ref={input} className="q" autoFocus spellCheck={false} autoComplete="off" value={screen === 'review' ? '' : q} readOnly={readOnly}
        placeholder={placeholder} onChange={(e) => { setQ(e.target.value); setListMode(false); if (screen === 'answer') setScreen('search') }}
        role="combobox" aria-expanded={screen !== 'answer' && screen !== 'review'} aria-controls={listId} aria-activedescendant={activeId}
        aria-label={chip ? chip[1] : 'Search files or ask a question'} />
      {screen === 'answer' && <span className="chip"><I i={Sparkles} />Ask Sniff</span>}
      {screen === 'review' && item && (
        <div className="prog" aria-label={`${doneN + 1} of ${total}`}><span>{Math.min(doneN + 1, total)} of {total}</span>
          {total <= 6 ? Array.from({ length: total }, (_, i) => <i key={i} className={i <= doneN ? 'on' : ''} />)
            : <b style={{ '--p': `${Math.round(((doneN + 1) / total) * 100)}%` } as CSSProperties} />}
        </div>
      )}
      <button type="button" tabIndex={-1} onClick={escape} aria-label="Escape"><kbd>Esc</kbd></button>
      <div className={`load${loading ? ' on' : ''}`} />
    </div>
  )

  let body: ReactNode = null, foot: ReactNode = null, live = ''
  const errFoot = (base: ReactNode, children: ReactNode, warn = false) =>
    <Foot ctx={err || base} warn={!!err || warn} toast={toast}>{children}</Foot>
  const actK = <Act k={['Ctrl', 'K']} on={panel} run={() => (panel ? closePanel() : openPanel())}>Actions</Act>

  if (screen === 'search') {
    const empty = !query
    const strongN = strong.length
    body = (
      <div className={`main${showPane ? ' split' : ''}`}>
        <div className="list" data-scroll role="listbox" id={listId} aria-label={empty ? 'Recent files' : 'Results'}>
          {empty && recent?.length ? <div className="sh">Recent</div> : null}
          {items.map((it, i) => (
            <Fragment key={it.id}>
              {more && i === strongN && strongN > 0 && weak.length > 0 && <div className="sh weak" role="presentation">Weaker matches</div>}
              <div className={`row${it.kind === 'fold' ? ' fold' : ''}`} role="option" id={`o-${it.id}`} aria-selected={i === at}
                onMouseMove={() => i !== at && setSel(i)} onClick={() => setSel(i)} onDoubleClick={() => enterItem(it)}>
                {it.icon ? <Tile icon={it.icon} tone="neutral" /> : <Tile type={it.type} />}
                <span className="t">{it.title}</span>
                {it.acc && <span className="acc">{it.acc}</span>}
              </div>
            </Fragment>
          ))}
          {searchNote && <div className="note">{searchNote}</div>}
        </div>
        {showPane && pv && (
          <div className="pane fade">{/* kept mounted while arrowing: the old page stays up until the next one loads, no blank flash */}
            {pv.kind === 'fold' ? <p className="pv-note">Press Enter to show the {plural(weak.length, 'weaker match', 'weaker matches')}.</p> : pv.src && (
              <>
                {paneAr && <img className="crop" alt={`Page ${pv.hit?.fact?.page ?? pv.src.page} of ${pv.src.name}`} style={{ aspectRatio: `auto 1 / ${paneAr}` }}
                  src={api.cropUrl(pv.src.file_id, { ...(pv.hit?.fact ? { fact_id: pv.hit.fact.id } : { page: pv.src.page, q: pv.hit ? query : '' }), ar: paneAr })} />}
                <div className="pv-field">
                  <span className="l">{pv.hit?.fact ? label(pv.hit.fact.field) : pv.hit ? 'Text' : label(typeName(pv.type))}</span>
                  <span className="v">{pv.hit?.fact ? show(pv.hit.fact) : pv.hit ? around(pv.hit.snippet, query) : (pv.title as string)}</span>
                  <span className="src">{pv.src.name}, page {pv.hit?.fact?.page ?? pv.src.page}</span>
                </div>
              </>
            )}
          </div>
        )}
      </div>
    )
    live = query && hits ? (fold ? `${plural(strongN, 'result', 'results')}, ${weak.length} weaker folded` : plural(hits.length, 'result', 'results')) : ''
    foot = errFoot(
      empty && !query ? (recent && !recent.length ? 'Sniff has not read any files yet. Add a folder to start.' : 'Type to search files and facts. Press Tab to ask a question.') : cur?.why ?? '',
      <>
        {canPreview && query && <Act k={['Space']} run={() => setPreview(!preview)} opt>{preview ? 'Close preview' : 'Preview'}</Act>}
        {cur && <Act k={['Enter']} run={() => enterItem(cur)}>{cur.kind === 'fold' ? 'Show them' : cur.kind === 'file' ? 'Open' : 'Go'}</Act>}
        {query && !query.startsWith('/') && !cur && <Act k={['Tab']} run={() => ask(query)}>Ask</Act>}
        {actK}
      </>)
  }

  if (screen === 'answer') {
    const streaming = !ans && !!stream
    const sources = ans?.sources ?? []
    const failed = !asking && !ans && !stream && !!err // no answer came, e.g. the chat model is not running
    body = (
      <div className="main">
        {!details ? (
          <div className="ans" data-scroll style={{ flex: 1 }}>
            {starting && !stream && <p className="sent lead">Starting the model…</p>}
            {failed && <p className="sent lead">{err}</p>}
            {(h?.value || h?.alts || (asking && !stream && !ans && !starting)) && (
              <div className="fig">
                {h?.alts ? <span className="two">{h.alts.map((v, i) => <Fragment key={i}>{i > 0 && ' or '}<b>{v}</b></Fragment>)}: two files disagree on {shortTitle(alts[0].source)}.</span>
                  : h?.value ?? <span className="skel" style={{ width: 184, height: 28, verticalAlign: 'middle' }} />}
              </div>
            )}
            {!failed && <p className={`sent${streaming ? ' streaming lead' : ''}${h && !h.value && !h.alts ? ' lead' : ''}`}>{ans ? h?.sentence : stream}</p>}
            {sources.length > 0 && (
              <div className="cites">
                {sources.slice(0, 4).map((s, i) => (
                  <button key={i} type="button" tabIndex={-1} className="cite in" onClick={() => open(s)}
                    aria-label={conflictFiles.has(s.file_id) ? `Source ${i + 1}, two files disagree` : undefined}>
                    <span className="num">{i + 1}</span><span>{sources.some((x) => x !== s && x.title === s.title) ? s.name : shortTitle(s)}</span><span className="p">p. {s.page}</span>
                    {conflictFiles.has(s.file_id) && <I i={TriangleAlert} />}
                  </button>
                ))}
                {sources.length > 4 && <button type="button" tabIndex={-1} className="cite in" onClick={() => showDetails(true)}><span className="num">+</span><span>{sources.length - 4} more</span></button>}
              </div>
            )}
          </div>
        ) : (
          <div className="det" data-scroll style={{ flex: 1 }} role="listbox" id={listId} aria-label="How the answer was found">
            <div className="dg dh"><span /><span>Counted</span><span className="f" /><span className="r">{ans && currency(ans)}</span><span /></div>
            {facts.map((f, i) => {
              const s = sourceOf(f.file_id)
              return (
                <div key={f.id} className="dg drow" aria-selected={sel === i} onMouseMove={() => setSel(i)} onClick={() => dRows[i].run()}>
                  <span className="num">{i + 1}</span><span className="t">{s ? s.title || s.name : `Document ${f.file_id}`}</span>
                  <span className="f">{label(f.field)}, p. {f.page}</span><span className="r">{f.value_type === 'money' ? money(f.value) : plain(f.value)}</span>
                  {f.status === 'needs_review' ? <St kind="warn">Needs review</St> : f.status === 'verified' ? <St kind="ok">Verified</St> : <St kind="ok">{label(f.status)}</St>}
                </div>
              )
            })}
            {alts.length > 0 && (
              <div className="dg drow" aria-selected={sel === facts.length} onMouseMove={() => setSel(facts.length)} onClick={() => setXOpen((o) => ({ ...o, conflict: true }))}>
                <span className="num">{facts.length + 1}</span><span className="t">{alts[0].source.title || alts[0].source.name}</span>
                <span className="f" /><span className="r muted">–</span><St kind="warn">Two values</St>
              </div>
            )}
            {h?.total && (
              <div className="dg dtot"><span /><span>Total</span><span className="f" />
                <span className={`r${alts.length ? ' muted' : ''}`}>{alts.length ? '–' : h.value?.split('\u00a0').pop()}</span>{alts.length ? <St kind="warn">Not final</St> : <span />}</div>
            )}
            {dRows.map((r, i) => r.kind === 'x' && (
              <Fragment key={r.id}>
                <div className="xrow" role="button" aria-expanded={!!(r.id === 'skip' ? xOpen.skip : conflictOpen)} aria-selected={sel === i}
                  onMouseMove={() => setSel(i)} onClick={() => r.run()}>
                  <I i={ChevronRight} className="chev" />
                  {r.id === 'skip' ? <><I i={Copy} /><span>{plural(ans!.skipped_duplicates.length, 'copy', 'copies')} skipped</span>
                    <span className="f">{ans!.skipped_duplicates.map((s) => s.name).join(', ')}</span></>
                    : <><I i={Scale} /><span>{plural(ans!.conflicts.length || 1, 'conflict', 'conflicts')}</span>
                      <span className="f">{alts.length ? 'Two files disagree. Pick the right value.' : `${label(ans!.conflicts[0].field)} differs between copies. Pick the right one.`}</span></>}
                </div>
                {r.id === 'skip' && xOpen.skip && <div className="xbody">{ans!.skipped_duplicates.map((s) => <p key={s.file_id}>{s.name}: a copy of a document already counted, so it was counted once.</p>)}</div>}
                {r.id === 'conflict' && conflictOpen && (
                  <div className="xbody">
                    {choices.map((c, n) => (
                      <button key={c.fact_id} type="button" tabIndex={-1} className="choice" onClick={() => pick(n)}>
                        <kbd>{n + 1}</kbd><span className="cv">{c.value}</span><span className="muted">{c.src.name}, page {c.src.page}</span>
                      </button>
                    ))}
                  </div>
                )}
              </Fragment>
            ))}
          </div>
        )}
      </div>
    )
    const n = ans?.counted.length || ans?.sources.length || 0
    live = !ans ? '' : h?.alts ? `Not final: ${h.alts.join(' or ')}. Two files disagree.` : `${h?.value ? h.value + '. ' : ''}${h?.sentence ?? ''}`
    // the reason is in the body, so the footer only says how to go back
    if (failed) foot = <Foot ctx="Esc goes back to your search" toast={toast}>{actK}</Foot>
    else foot = errFoot(
      asking ? (stream ? 'Writing the answer' : starting ? 'The model loads only when an answer needs it. The first one can take about 40 seconds' : 'Reading your documents')
      : details ? 'Esc returns to the answer'
      : !ans ? ''
      : alts.length ? 'The total is not final. Sniff does not choose between the two files.'
      : n ? `Answered from ${plural(n, 'document', 'documents')} on this laptop${ans.unreviewed.length ? `. ${plural(ans.unreviewed.length, 'value is', 'values are')} not reviewed yet` : ''}`
      : 'Nothing in your documents answers this',
      <>
        {ans && !details && <Act k={['Enter']} run={answerPrimary}>{alts.length ? 'Pick the right value' : 'Copy'}</Act>}
        {details && choices.length > 0 && <Act k={choices.slice(0, 2).map((_, i) => String(i + 1))} run={() => setXOpen((o) => ({ ...o, conflict: true }))}>Pick a value</Act>}
        {details && !choices.length && dRows[sel]?.kind === 'fact' && <Act k={['Enter']} run={() => dRows[sel].run()}>Open source</Act>}
        {hasDetails && <Act k={['Tab']} run={() => showDetails(!details)} opt>{details ? 'Answer' : 'Details'}</Act>}
        {actK}
      </>, !!(ans && alts.length && !details))
  }

  if (screen === 'review') {
    const f = item?.fact
    body = !queue ? <div className="done">Loading…</div> : !item || !f ? <div className="done">Nothing to review. Every fact is verified, corrected or approved.</div> : (
      <div className="rev" data-scroll>
        <img className="crop" key={f.id} src={api.cropUrl(f.file_id, { fact_id: f.id })} alt={`Page ${f.page} of ${item.document}, around the ${label(f.field).toLowerCase()}`} />
        <div className="rv">
          <div className="rv-src"><Tile type={item.doc_type} size="xs" />{item.title || item.document}<span>{item.document}, page {f.page}</span></div>
          <div className="l">{label(f.field)}</div>
          {edit === null ? (
            <div className="valrow"><div className="fig">{show(f)}</div><St kind="warn">Needs review</St></div>
          ) : (
            <input className="edit" autoFocus value={edit} aria-label={`Correct ${label(f.field)}`} onChange={(e) => setEdit(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter') { e.preventDefault(); e.stopPropagation(); correct(edit.trim()) }
                if (e.key === 'Escape') { e.preventDefault(); e.stopPropagation(); setEdit(null); setEditErr(''); input.current?.focus() }
              }} />
          )}
          {editErr && <p className="err">{editErr}</p>}
          {f.reason && <p className="why">{label(f.reason).replace(/([^.])$/, '$1.')}</p>}
          <div className="btns">
            {edit === null ? (
              <>
                <button className="btn" type="button" onClick={accept}><I i={Check} />Accept <kbd>A</kbd></button>
                <button className="btn" type="button" onClick={() => setEdit(show(f).replace(/\u00a0/g, ' '))}><I i={Pencil} />Correct <kbd>C</kbd></button>
                <button className="btn" type="button" onClick={skip}>Skip <kbd>S</kbd></button>
              </>
            ) : (
              <>
                <button className="btn" type="button" onClick={() => correct(edit.trim())}><I i={Check} />Save <kbd>Enter</kbd></button>
                <button className="btn" type="button" onClick={() => { setEdit(null); setEditErr('') }}>Cancel <kbd>Esc</kbd></button>
              </>
            )}
          </div>
        </div>
      </div>
    )
    foot = errFoot(edit !== null ? 'Type the right value, then press Enter' : 'Nothing is saved until you choose',
      <>{reviewSrc && <Act k={['Enter']} run={() => open(reviewSrc)}>Open page {reviewSrc.page}</Act>}{actK}</>)
  }

  if (screen === 'overview' || screen === 'settings') {
    const orow = screen === 'overview'
    body = (
      <div className="list" data-scroll role="listbox" id={listId} aria-label={chip?.[1]}>
        {filtered.map((r, i) => (
          <Fragment key={r.id}>
            {r.head && <div className="sh">{r.head}</div>}
            <div className={`${orow ? 'orow' : 'srow'}${r.add ? ' add' : ''}`} role="option" id={`o-${r.id}`} aria-selected={i === rAt}
              onMouseMove={() => i !== rAt && setSel(i)} onClick={() => { setSel(i); if (!orow) (r.run ?? r.toggle)?.() }} onDoubleClick={() => orow && r.run?.()}>
              {r.tile ?? (r.icon && <I i={r.icon} />)}
              {orow ? <><span className="l">{r.t}</span><span className="v">{r.m}</span>{r.right ?? <span />}</>
                : <><span className="t">{r.t}</span>{r.m && <span className="m">{r.m}</span>}{r.right}
                  {r.toggle && <Tog on={!!r.on} label={r.t} onClick={r.toggle} />}{r.chev && <I i={ChevronRight} />}</>}
            </div>
          </Fragment>
        ))}
        {!filtered.length && <div className="note">{(orow ? ov : true) ? (query ? 'Nothing matches.' : level === 'log' ? 'No agent has asked anything yet.' : 'Loading…') : 'Loading…'}</div>}
      </div>
    )
    const ctx = orow ? rCur?.ctx ?? '' : adding ? 'Sniff reads the folder now, then keeps it up to date'
      : level === 'top' ? 'Changes save as you make them' : level === 'log' ? 'The last 50 agent requests. Backspace goes back' : 'Backspace goes back to Settings'
    foot = errFoot(ctx, <>
      {adding ? <Act k={['Enter']} run={() => query && attempt(api.watch(query).then((w) => { setWatch(w); setAdding(false); setQ('') }), `Watching ${query}`, Check, 'watch this folder')}>Add</Act>
        : rCur?.toggle ? <Act k={['Space']} run={rCur.toggle}>Turn {rCur.on ? 'off' : 'on'}</Act>
        : rCur?.run && rCur.act ? <Act k={['Enter']} run={rCur.run}>{rCur.act}</Act> : null}
      {actK}
    </>)
  }

  return (
    // A click on empty space would move focus to the page and the keys would stop working; keep it in the input.
    <div className="stage" onMouseDown={(e) => { if (!(e.target as HTMLElement).closest('input')) e.preventDefault() }}>
      <div className="win show" ref={win} onKeyDown={onKey}>
        <section ref={sec} className={`scr${panel ? ' dim' : ''}`} aria-label={`Sniff, ${chip?.[1] ?? (screen === 'answer' ? 'answer' : 'search')}`}>
          {bar}
          {body}
          {panel && <div className="scrim" onClick={closePanel} />}
          {foot}
          {panel && (
            <div className="panel" ref={panelEl} role="dialog" aria-label="Actions">
              {confirm && (
                <p className="p-info">Google Calendar will receive exactly this: <b>{confirm.p.title}</b>, {fmtDay(confirm.p.date)}, all day, with pop-ups a week and a day before. Note: {confirm.p.description} Nothing else is sent.
                  {!confirm.p.reviewed && ' This date is not reviewed yet.'}</p>
              )}
              {pActs.map((a, n) => (
                <div key={a.name} className="prow" role="option" aria-selected={n === pSel} onMouseMove={() => setPSel(n)} onClick={() => runAct(a)}>
                  <I i={a.icon} /><span>{a.name}</span>{a.keys && <span className="k"><Keys k={a.keys} /></span>}
                </div>
              ))}
              {!pActs.length && <div className="p-none">No actions match</div>}
              {!confirm && <div className="p-search"><I i={Search} /><input ref={pInput} value={pq} onChange={(e) => { setPq(e.target.value); setPSel(0) }} placeholder="Search actions…" aria-label="Search actions" /></div>}
            </div>
          )}
          <div className="sr" aria-live="polite">{live}</div>
        </section>
      </div>
    </div>
  )
}
