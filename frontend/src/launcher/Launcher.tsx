// The launcher: one bar that searches, answers, and runs a few commands, all from the keyboard.
import { Fragment, useEffect, useRef, useState, type KeyboardEvent } from 'react'
import {
  Ban, Bell, Bot, CalendarClock, CalendarPlus, Check, ChevronRight, ClipboardCheck, Copy, Cpu, ExternalLink, FileText, Files, Folder,
  FolderOpen, FolderPlus, FolderSync, Keyboard, LayoutList, ListChecks, LoaderCircle, Pencil, Power, ScrollText, Search, Settings, TriangleAlert, X,
  type LucideIcon,
} from 'lucide-react'
import {
  api, type Answer, type Document, type Fact, type Hit, type McpLogEntry, type McpSettings, type Overview, type ReminderPreview, type ReviewItem,
  type UpcomingItem, type WatchInfo,
} from '../api'
import { Highlight, Status, Tag, label, show } from '../ui'
import { AnswerCard, FactLine, Menu, Pill, ReviewCard, Toggle, fileIcon, plural, type Action, type Row } from './parts'
import { inDesktop, native } from './native'

type Mode = 'root' | 'overview' | 'upcoming' | 'review' | 'watch' | 'settings'
// A list opened from a row with → (Esc or ← goes back to where it was opened, `back` is that row).
type Sub = ({ kind: 'doc'; id: number; name: string } | { kind: 'fields'; type: string } | { kind: 'log' }) & { back: number }
type Src = { file_id: number; path: string; name: string; page: number }

const COMMANDS: { id: Exclude<Mode, 'root'>; name: string; desc: string; icon: LucideIcon }[] = [
  { id: 'overview', name: 'Overview', desc: 'What is indexed, what needs checking, what expires', icon: LayoutList },
  { id: 'upcoming', name: 'Upcoming', desc: 'Dates in the next 60 days', icon: CalendarClock },
  { id: 'review', name: 'Review', desc: 'Facts that need checking, one at a time', icon: ClipboardCheck },
  { id: 'watch', name: 'Watch', desc: 'Folders Sniff keeps up to date', icon: FolderSync },
  { id: 'settings', name: 'Settings', desc: 'Hotkey, start with Windows, models, agent access', icon: Settings },
]
const PLACEHOLDER: Record<Mode, string> = {
  root: 'Search your documents, or ask a question',
  overview: 'Filter', upcoming: 'Filter dates', review: '', settings: 'Filter settings',
  watch: 'Type a folder path to watch, e.g. C:\\Users\\you\\Documents',
}
const DATES = ['expiry_date', 'due_date', 'warranty_until', 'end_date'] // ACTION_DATE_FIELDS in schemas.py: dates a reminder can be made for

const msg = (e: unknown) => (e as Error)?.message ?? String(e)
const days = (d: number) => (d < 0 ? `${plural(-d, 'day', 'days')} ago` : d === 0 ? 'today' : d === 1 ? 'tomorrow' : `in ${d} days`)
// The back end cuts the snippet around the hit, but the hit can still sit past the end of one line. Start near it.
const around = (text: string, q: string) => {
  const words = q.split(/\s+/).filter((w) => w.length > 1).map((w) => w.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'))
  const i = words.length ? text.search(new RegExp(`\\b(${words.join('|')})`, 'i')) : -1 // word starts, as Highlight marks them
  return i > 40 ? '…' + text.slice(text.lastIndexOf(' ', i - 20) + 1) : text
}
const summary = (o: Overview) =>
  [plural(o.total_files, 'file', 'files'), o.needs_review && `${o.needs_review} to review`,
    o.expiring['30'] && `${o.expiring['30']} ${o.expiring['30'] === 1 ? 'expires' : 'expire'} within 30 days`,
    o.conflicts && plural(o.conflicts, 'conflict', 'conflicts'), o.errors && `${o.errors} could not be read`].filter(Boolean).join(' · ')

export default function Launcher() {
  const [mode, setMode] = useState<Mode>('root')
  const [sub, setSub] = useState<Sub | null>(null)
  const [q, setQ] = useState('')
  const [sel, setSel] = useState(0)
  const [hits, setHits] = useState<Hit[]>([])
  const [ans, setAns] = useState<Answer | null>(null)
  const [asking, setAsking] = useState(false)
  const [busy, setBusy] = useState(false)
  const [err, setErr] = useState('')
  const [details, setDetails] = useState(false)
  const [menu, setMenu] = useState<string | null>(null) // id of the row whose actions are open
  const [status, setStatus] = useState('')
  const [edit, setEdit] = useState<{ id: number; value: string } | null>(null) // a fact being corrected
  const [editErr, setEditErr] = useState('')
  const [ov, setOv] = useState<Overview | null>(null)
  const [up, setUp] = useState<UpcomingItem[] | null>(null)
  const [queue, setQueue] = useState<ReviewItem[] | null>(null)
  const [watch, setWatch] = useState<WatchInfo | null>(null)
  const [docs, setDocs] = useState<Record<number, Document>>({})
  const [mcp, setMcp] = useState<McpSettings | null>(null)
  const [log, setLog] = useState<McpLogEntry[] | null>(null)
  const [google, setGoogle] = useState(false)
  const [previews, setPreviews] = useState<Record<number, ReminderPreview>>({}) // by fact id
  const [hotkey, setHotkey] = useState('Alt+Shift+Space') // the desktop app's saved one replaces this on load
  const [capturing, setCapturing] = useState(false)
  const [hotkeyNote, setHotkeyNote] = useState<string | null>(null)
  const [autostart, setAutostart] = useState(false)
  const input = useRef<HTMLInputElement>(null)
  const seq = useRef(0) // drops responses that belong to an older query

  useEffect(() => {
    api.overview().then(setOv, () => {}) // only feeds the summary line; a dead back end shows up as soon as you search
    api.calendarStatus().then((s) => setGoogle(s.connected), () => setGoogle(false)) // no answer: offer only the .ics file
    native.autostart().then(setAutostart, () => {})
    native.hotkey().then(setHotkey, () => {})
    native.hotkeyNote().then(setHotkeyNote, () => {})
    const onGo = (e: Event) => go((e as CustomEvent<Mode>).detail) // the tray's Settings item
    const onFocus = () => input.current?.focus() // the hotkey shows the window: be ready to type
    window.addEventListener('sniff:go', onGo)
    window.addEventListener('focus', onFocus)
    return () => { window.removeEventListener('sniff:go', onGo); window.removeEventListener('focus', onFocus) }
  }, [])

  useEffect(() => {
    if (mode !== 'root') return
    const id = ++seq.current
    setAns(null); setErr(''); setDetails(false); setAsking(false)
    const query = q.trim()
    if (!query || query.startsWith('/')) { setHits([]); setBusy(false); return }
    const t = setTimeout(async () => {
      setBusy(true)
      try {
        const h = await api.search(query)
        if (id === seq.current) setHits(h)
      } catch (e) {
        if (id === seq.current) setErr(`Search failed: ${msg(e)}`)
      }
      if (id === seq.current) setBusy(false)
      if (query.endsWith('?')) ask(query, id)
    }, 120)
    return () => clearTimeout(t)
  }, [q, mode])

  useEffect(() => setSel(0), [q, mode, hits, ans])
  useEffect(() => { document.getElementById(`opt-${sel}`)?.scrollIntoView({ block: 'nearest' }) }, [sel, details])

  const ask = async (question: string, id = seq.current) => {
    setAsking(true)
    try {
      const a = await api.ask(question)
      if (id === seq.current) setAns(a)
    } catch (e) {
      if (id === seq.current) setErr(`Could not answer: ${msg(e)}`)
    }
    if (id === seq.current) setAsking(false)
  }

  const attempt = (p: Promise<unknown>, done: string, what: string) =>
    p.then(() => setStatus(done), (e) => setStatus(`Could not ${what}: ${msg(e)}`))
  const open = (s: Src) => attempt(native.openFile(s.file_id, s.path, s.page).then(native.hide), `Opened ${s.name} at page ${s.page}`, 'open the file')
  const reveal = (path: string) => attempt(native.reveal(path), 'Shown in Explorer', 'show it in Explorer')
  const copy = (text: string) => attempt(navigator.clipboard.writeText(text), `Copied ${text.length > 40 ? 'to the clipboard' : text}`, 'copy')
  const fail = (e: unknown) => setErr(`Could not reach Sniff: ${msg(e)}`)
  const loadDoc = (id: number) => api.document(id).then((d) => setDocs((m) => ({ ...m, [id]: d })), fail)

  const go = (m: Mode, start?: number) => {
    setMode(m); setSub(null); setQ(''); setMenu(null); setErr(''); setStatus(''); setEdit(null)
    if (m === 'overview') api.overview().then(setOv, fail)
    if (m === 'upcoming') { setUp(null); api.upcoming().then(setUp, fail) }
    if (m === 'review') { setQueue(null); api.review().then((l) => setQueue([...l.filter((x) => x.fact.id === start), ...l.filter((x) => x.fact.id !== start)]), fail) }
    if (m === 'watch') api.watched().then(setWatch, fail)
    if (m === 'settings') api.mcpSettings().then(setMcp, fail)
    input.current?.focus()
  }
  const openSub = (s: Sub) => {
    setSub(s); setSel(0); setMenu(null); setEdit(null)
    if (s.kind === 'doc') loadDoc(s.id)
    if (s.kind === 'log') { setLog(null); api.mcpLog().then(setLog, fail) }
  }
  const closeSub = () => { if (sub) setSel(sub.back); setSub(null); setEdit(null) }
  const openMenu = (r: Row) => {
    setMenu(r.id)
    const f = r.remind
    // Google Calendar is offered only once the back end has said exactly what it would send.
    if (f && google && !previews[f.id]) api.reminderPreview(f.file_id, f.field).then((p) => setPreviews((m) => ({ ...m, [f.id]: p })), () => {})
  }
  const closeMenu = () => { setMenu(null); input.current?.focus() }

  // A fact changed: update every list that holds it.
  const saved = (f: Fact, done: string) => {
    setDocs((m) => (m[f.file_id] ? { ...m, [f.file_id]: { ...m[f.file_id], facts: m[f.file_id].facts.map((x) => (x.id === f.id ? f : x)) } } : m))
    setQueue((l) => l && l.filter((x) => x.fact.id !== f.id))
    setEdit(null); setEditErr(''); setStatus(done)
    input.current?.focus()
  }
  const approve = (f: Fact) =>
    api.approve(f.id).then((n) => saved(n, `Approved ${label(f.field).toLowerCase()}`), (e) => setStatus(`Could not approve: ${msg(e)}`))
  const correct = (f: Fact, value: string) =>
    api.correct(f.id, value).then((n) => saved(n, `Saved ${label(f.field).toLowerCase()} as ${n.value}`), (e) => { setEditErr(msg(e)); setStatus(`Could not save: ${msg(e)}`) })
  const editing = (f: Fact) => ({
    edit: edit?.id === f.id ? edit.value : null,
    set: (v: string | null) => {
      setEdit(v === null ? null : { id: f.id, value: v })
      if (v === null) input.current?.focus() // the correction box is going away; keep the keys working
    },
  })

  const saveMcp = (enabled: boolean, allow: McpSettings['allow']) => api.saveMcp(enabled, allow).then(setMcp, (e) => setStatus(`Could not save: ${msg(e)}`))
  const setAllow = (type: string, fields: string[]) => {
    const allow = { ...mcp!.allow }
    if (fields.length) allow[type] = fields
    else delete allow[type]
    saveMcp(mcp!.enabled, allow)
  }
  const toggleAutostart = () => {
    const on = !autostart
    attempt(native.setAutostart(on).then(() => setAutostart(on)), on ? 'Sniff starts with Windows' : 'Sniff no longer starts with Windows', 'change this')
  }

  // Reminders for a date: a local .ics file always, Google Calendar only when connected, after showing what it receives.
  const remindActions = (f: Fact): Action[] => {
    if (!DATES.includes(f.field)) return []
    const p = previews[f.id]
    return [
      { name: 'Add to calendar (.ics)', icon: CalendarPlus,
        run: () => attempt(native.openIcs(api.icsPath(f.file_id, f.field)), inDesktop ? 'Opened the reminder in your calendar app' : 'Downloaded the reminder as an .ics file', 'make the calendar file') },
      ...(google && p ? [{
        name: 'Add to Google Calendar', icon: Bell,
        run: () => attempt(api.remind(f.id), `Added to Google Calendar for ${f.value}`, 'add it to Google Calendar'),
        confirm: (
          <>
            <p className="mb-1.5 font-semibold text-fg">Google Calendar will receive exactly this:</p>
            <dl className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-0.5">
              <dt className="text-muted">Title</dt><dd>{p.title}</dd>
              <dt className="text-muted">Date</dt><dd className="tnum">{p.date}, all day, pop-ups a week and a day before</dd>
              <dt className="text-muted">Note</dt><dd>{p.description}</dd>
            </dl>
            <p className="mt-1.5">Nothing else from your documents is sent.</p>
            {!p.reviewed && <p className="mt-1 text-warn">This date is not reviewed yet: {label(f.reason ?? 'check it first')}.</p>}
          </>
        ),
      }] : []),
    ]
  }
  const fileActions = (s: Src): Action[] => [
    { name: 'Open', icon: ExternalLink, keys: 'Enter', run: () => open(s) },
    { name: 'Show in Explorer', icon: FolderOpen, keys: 'Ctrl+Enter', run: () => reveal(s.path) },
    { name: 'Show extracted facts', icon: ListChecks, keys: '→', run: () => openSub({ kind: 'doc', id: s.file_id, name: s.name, back: sel }) },
    { name: 'Copy path', icon: Copy, run: () => copy(s.path) },
  ]
  const unreviewed = (f: Fact) => (f.status === 'needs_review' ? `Not reviewed yet: ${label(f.reason ?? 'check this value')}` : undefined)
  const cmdRows = (t: string) => COMMANDS.filter((c) => c.id.startsWith(t.toLowerCase()))
    .map((c): Row => ({ id: `cmd-${c.id}`, icon: c.icon, title: c.name, tag: 'Command', detail: c.desc, enter: () => go(c.id), actions: [] }))

  const query = q.trim()
  const build = (): Row[] => {
    if (sub?.kind === 'doc') {
      const d = docs[sub.id]
      if (!d) return []
      return d.facts.map((f): Row => {
        const s = { file_id: d.id, path: d.path, name: d.name, page: f.page }
        const e = editing(f)
        return {
          id: `fact-${f.id}`, title: `${label(f.field)} ${show(f)}`, right: `p. ${f.page}`, enter: () => open(s), path: d.path, note: unreviewed(f), remind: f,
          body: <FactLine f={f} edit={e.edit} set={e.set} save={(v) => correct(f, v)} />,
          actions: [
            ...(f.status !== 'approved' ? [{ name: 'Approve', icon: Check, keys: 'A', run: () => approve(f) }] : []),
            { name: 'Correct', icon: Pencil, keys: 'C', run: () => setEdit({ id: f.id, value: f.value }) },
            { name: 'Copy value', icon: Copy, run: () => copy(f.value) },
            ...remindActions(f),
            { name: 'Open at this page', icon: ExternalLink, keys: 'Enter', run: () => open(s) },
          ],
        }
      })
    }
    if (sub?.kind === 'fields' && mcp) {
      const cur = mcp.allow[sub.type] ?? []
      const all = mcp.fields[sub.type] ?? []
      const star = cur.includes('*')
      return [
        { id: 'star', title: 'Every field and the page text', detail: star ? 'On. Page text is only given out when this is on' : 'Off: agents get only the fields below',
          right: <Toggle on={star} />, enter: () => setAllow(sub.type, star ? all : ['*']), actions: [] },
        ...all.map((fl): Row => {
          const on = star || cur.includes(fl)
          return { id: `f-${fl}`, title: label(fl), detail: on ? 'Agents may read it' : 'Hidden from agents', right: <Toggle on={on} />, actions: [],
            enter: () => setAllow(sub.type, on ? (star ? all : cur).filter((x) => x !== fl) : [...cur, fl]) }
        }),
      ]
    }
    if (sub?.kind === 'log') {
      return (log ?? []).map((l, i): Row => ({
        id: `log-${i}`, icon: l.refused ? Ban : Bot, title: `${l.tool}: ${Object.values(l.args).map(String).join(', ')}`,
        detail: l.refused ? 'Refused: agent access was off' : `${plural(l.ids.length, 'document', 'documents')} returned${l.ids.length ? ` (ids ${l.ids.join(', ')})` : ''}`,
        right: l.time.replace('T', ' ').slice(0, 16), actions: [],
      }))
    }
    if (mode === 'root') {
      if (query.startsWith('/')) return cmdRows(query.slice(1))
      if (!query) return []
      const words = query.replace(/\?/g, '').split(/\s+/).filter((w) => w.length > 2).join(' ') // "in", "my" would light up half the snippet
      const rows: Row[] = []
      if (ans) {
        const s = ans.sources[0]
        const dates = ans.facts.filter((f) => DATES.includes(f.field))
        const date = dates.length === 1 ? dates[0] : null // a reminder only makes sense for one date
        rows.push({
          id: 'answer', title: ans.answer, answer: ans, enter: s && (() => open(s)), path: s?.path, remind: date ?? undefined,
          note: ans.unreviewed.length ? `${plural(ans.unreviewed.length, 'value', 'values')} not reviewed yet` : undefined,
          actions: [
            { name: 'Copy answer', icon: Copy, run: () => copy(ans.answer) },
            ...(date ? remindActions(date) : []),
            ...(ans.unreviewed.length ? [{ name: 'Review the unchecked values', icon: ClipboardCheck, run: () => go('review', ans.unreviewed[0].id) }] : []),
            ...ans.sources.map((x) => ({ name: `Open ${x.name}, p. ${x.page}`, icon: ExternalLink, run: () => open(x) })),
          ],
        })
      }
      if (/^[a-z]+$/i.test(query)) rows.push(...cmdRows(query))
      return rows.concat(hits.map((h, i): Row => ({
        id: `hit-${i}`, icon: fileIcon(h.name), title: h.name, tag: h.doc_type && label(h.doc_type),
        detail: <Highlight text={around(h.snippet, words)} q={words} />, right: `p. ${h.page}`,
        enter: () => open(h), path: h.path, more: () => openSub({ kind: 'doc', id: h.file_id, name: h.name, back: sel }), actions: fileActions(h),
      })))
    }
    if (mode === 'overview') {
      if (!ov) return []
      const n = (v: number) => <span className="text-[14px] text-fg">{v}</span>
      return [
        { id: 'files', head: 'Library', icon: Files, title: 'Files indexed', detail: `${plural(ov.documents, 'unique document', 'unique documents')} · ${ov.errors} could not be read`, right: n(ov.total_files), actions: [] },
        { id: 'dupes', icon: Copy, title: 'Duplicates', detail: `${plural(ov.exact_copies, 'exact copy', 'exact copies')} · ${plural(ov.near_duplicates, 'near duplicate', 'near duplicates')}`, right: n(ov.exact_copies + ov.near_duplicates), actions: [] },
        { id: 'conflicts', icon: TriangleAlert, title: 'Conflicts', detail: 'Copies of one record that disagree', right: n(ov.conflicts), actions: [] },
        { id: 'review', head: 'Needs checking', icon: ClipboardCheck, title: 'Facts to review', detail: 'Values Sniff could not confirm on the page. Enter starts the review', right: n(ov.needs_review), enter: () => go('review'), actions: [] },
        ...(ov.missing_required.length
          ? ov.missing_required.map((m): Row => ({
            id: `miss-${m.file_id}`, icon: fileIcon(m.name), title: m.name, tag: label(m.doc_type), detail: `Missing ${m.fields.map((x) => label(x).toLowerCase()).join(', ')}`,
            enter: () => openSub({ kind: 'doc', id: m.file_id, name: m.name, back: sel }), actions: [],
          }))
          : [{ id: 'miss', icon: Check, title: 'Missing required fields', detail: 'None: every document has the fields its type needs', right: n(0), actions: [] }]),
        ...(['30', '60', '90'] as const).map((d, i): Row => ({
          id: `exp-${d}`, head: i ? undefined : 'Expiring', icon: CalendarClock, title: `Within ${d} days`, right: n(ov.expiring[d]), enter: () => go('upcoming'), actions: [],
        })),
        ...Object.entries(ov.by_type).sort((a, b) => b[1] - a[1]).map(([t, c], i): Row => ({
          id: `type-${t}`, head: i ? undefined : 'Documents by type', icon: FileText, title: label(t), right: n(c), actions: [],
        })),
      ]
    }
    if (mode === 'upcoming') {
      return (up ?? []).map((u): Row => {
        const f = u.fact
        const s = { file_id: f.file_id, path: u.path, name: u.document, page: f.page }
        return {
          id: `up-${f.id}`, icon: fileIcon(u.document), title: u.document, tag: u.doc_type && label(u.doc_type),
          detail: (
            <span className="flex items-center gap-2">
              <span className="tnum">{label(f.field)} {show(f)}</span>
              {f.status === 'needs_review' && <Status f={f} />}
              {u.conflict && <span className="text-danger">conflict: {u.conflict.values.map((v) => v.value).join(' or ')}</span>}
              {u.reminder_status === 'created' && <span>reminder added</span>}
            </span>
          ),
          right: <span className={u.days_left < 0 ? 'text-danger' : ''}>{days(u.days_left)}</span>,
          enter: () => open(s), path: u.path, remind: f, note: unreviewed(f),
          more: () => openSub({ kind: 'doc', id: f.file_id, name: u.document, back: sel }),
          actions: [
            ...remindActions(f),
            { name: 'Copy date', icon: Copy, run: () => copy(f.value) },
            ...(f.status === 'needs_review' ? [{ name: 'Review this date', icon: ClipboardCheck, run: () => go('review', f.id) }] : []),
            ...fileActions(s),
          ],
        }
      })
    }
    if (mode === 'watch') {
      const last = watch?.last
      return [
        ...(query ? [{ id: 'add', icon: FolderPlus, title: `Watch ${query}`, detail: 'Index this folder now, then keep it up to date', actions: [],
          enter: () => attempt(api.watch(query).then((w) => { setWatch(w); setQ('') }), `Watching ${query}`, 'watch this folder') }] : []),
        ...(watch?.folders ?? []).map((f): Row => ({
          id: `w-${f}`, icon: Folder, title: f, path: f, enter: () => reveal(f),
          detail: last?.folder === f ? `Last change at ${last.at.slice(11, 16)}: ${last.indexed} indexed, ${last.renamed} renamed, ${last.removed} removed` : 'Watching for changes',
          actions: [
            { name: 'Show in Explorer', icon: FolderOpen, keys: 'Enter', run: () => reveal(f) },
            { name: 'Stop watching', icon: X, run: () => attempt(api.unwatch(f).then(setWatch), `Stopped watching ${f}`, 'stop watching') },
          ],
        })),
      ]
    }
    if (mode === 'settings') {
      return [
        { id: 'hotkey', head: 'General', icon: Keyboard, title: 'Hotkey', detail: capturing ? 'Press the new shortcut now, or Esc to keep this one' : hotkeyNote ?? 'Opens Sniff from any app',
          right: <kbd>{hotkey}</kbd>, enter: () => setCapturing(true), actions: [] },
        { id: 'autostart', icon: Power, title: 'Start with Windows', detail: autostart ? 'On: Sniff starts when you sign in' : 'Off', right: <Toggle on={autostart} />, enter: toggleAutostart, actions: [] },
        // shortcut: fixed text; phase B reads the models and ports from the desktop app.
        { id: 'chat', head: 'Models, on this laptop', icon: Cpu, title: 'Chat model', detail: 'Qwen3-1.7B, Q4_K_M, in llama.cpp', right: 'port 8080', actions: [] },
        { id: 'embed', icon: Cpu, title: 'Embedding model', detail: 'all-MiniLM-L6-v2, in llama.cpp', right: 'port 8081', actions: [] },
        ...(mcp ? [
          { id: 'mcp', head: 'Agent access (MCP)', icon: Bot, title: 'Let AI agents read Sniff', right: <Toggle on={mcp.enabled} />, actions: [],
            detail: mcp.env_override ? 'Set by SNIFF_MCP_ALLOW, so these switches do nothing' : mcp.enabled ? 'On: agents you start on this laptop can read the types below' : 'Off: every agent request is refused and logged',
            enter: () => saveMcp(!mcp.enabled, mcp.allow) },
          ...Object.keys(mcp.fields).map((t): Row => {
            const a = mcp.allow[t]
            return {
              id: `mcp-${t}`, icon: FileText, title: label(t), right: <Toggle on={!!a} />, actions: [],
              detail: !a ? 'Hidden from agents' : a.includes('*') ? 'Every field and the page text' : `${a.length} of ${mcp.fields[t]?.length ?? 0} fields: ${a.map((x) => label(x).toLowerCase()).join(', ')}`,
              enter: () => setAllow(t, a ? [] : ['*']), more: () => openSub({ kind: 'fields', type: t, back: sel }),
            }
          }),
          { id: 'mcp-log', icon: ScrollText, title: 'Access log', detail: 'The last 50 agent requests', actions: [], enter: () => openSub({ kind: 'log', back: sel }), more: () => openSub({ kind: 'log', back: sel }) },
        ] : []),
      ]
    }
    return []
  }

  let rows = build()
  const t = query.toLowerCase()
  if (t && !sub && (mode === 'overview' || mode === 'upcoming' || mode === 'settings'))
    rows = rows.filter((r) => `${r.title} ${r.tag ?? ''}`.toLowerCase().includes(t)).map((r) => ({ ...r, head: undefined }))
  const at = Math.min(sel, Math.max(rows.length - 1, 0))
  const head = queue?.[0]
  const skip = () => setQueue((l) => (l && l.length > 1 ? [...l.slice(1), l[0]] : l))
  const menuRow = menu ? rows.find((r) => r.id === menu) : undefined
  const atEnd = () => { const el = input.current; return !el || el.readOnly || el.selectionStart === el.value.length }

  const captureHotkey = (e: KeyboardEvent) => {
    e.preventDefault()
    if (e.key === 'Escape') return setCapturing(false)
    if (['Control', 'Alt', 'Shift', 'Meta'].includes(e.key)) return
    if (!e.ctrlKey && !e.altKey && !e.metaKey) return setStatus('Use Ctrl, Alt or Win together with a key')
    const combo = [e.ctrlKey && 'Ctrl', e.altKey && 'Alt', e.shiftKey && 'Shift', e.metaKey && 'Super', e.key === ' ' ? 'Space' : e.key.length === 1 ? e.key.toUpperCase() : e.key].filter(Boolean).join('+')
    setCapturing(false)
    attempt(native.setHotkey(combo).then(() => setHotkey(combo)), `The hotkey is now ${combo}`, `use ${combo}`)
  }

  const onKey = (e: KeyboardEvent<HTMLDivElement>) => {
    const k = e.key
    const ctrl = e.ctrlKey || e.metaKey
    if (capturing) return captureHotkey(e)
    if (k === 'Escape') {
      e.preventDefault()
      if (sub) closeSub()
      else if (q) setQ('')
      else if (mode !== 'root') go('root')
      else native.hide()
      return
    }
    if (edit) return // the correction box handles its own keys
    if (mode === 'review' && !sub) {
      if (!head || ctrl) return
      const key = k.toLowerCase()
      if (key === 'a') { e.preventDefault(); approve(head.fact) }
      else if (key === 'c') { e.preventDefault(); setEdit({ id: head.fact.id, value: head.fact.value }) }
      else if (key === 's') { e.preventDefault(); skip() }
      else if (k === 'Enter') { e.preventDefault(); open({ file_id: head.fact.file_id, path: head.path, name: head.document, page: head.fact.page }) }
      return
    }
    const row = rows[at]
    if (sub?.kind === 'doc' && !ctrl && row) {
      const f = docs[sub.id]?.facts[at]
      if (f && k.toLowerCase() === 'a' && f.status !== 'approved') { e.preventDefault(); approve(f); return }
      if (f && k.toLowerCase() === 'c') { e.preventDefault(); setEdit({ id: f.id, value: f.value }); return }
    }
    if (k === 'ArrowDown' || k === 'ArrowUp') {
      e.preventDefault()
      if (rows.length) setSel((at + (k === 'ArrowDown' ? 1 : -1) + rows.length) % rows.length)
    } else if (k === 'Enter' && row) {
      e.preventDefault()
      if (!ctrl) row.enter?.()
      else if (row.path) reveal(row.path)
    } else if (ctrl && k.toLowerCase() === 'k') {
      e.preventDefault()
      if (row?.actions.length) openMenu(row)
    } else if (k === 'Tab' && mode === 'root' && query && !query.startsWith('/')) {
      e.preventDefault()
      if (!ans) ask(query)
    } else if ((k === 'ArrowRight' && atEnd()) || (ctrl && k.toLowerCase() === 'i')) {
      if (row?.answer) { e.preventDefault(); setDetails((d) => k === 'ArrowRight' || !d) }
      else if (row?.more) { e.preventDefault(); row.more() }
    } else if (k === 'ArrowLeft' && atEnd() && (sub || (details && row?.answer))) {
      e.preventDefault()
      if (sub) closeSub()
      else setDetails(false)
    }
  }

  const cmd = COMMANDS.find((c) => c.id === mode)
  const crumb = sub && (sub.kind === 'doc' ? sub.name : sub.kind === 'fields' ? `${label(sub.type)} fields` : 'Access log')
  const doc = sub?.kind === 'doc' ? docs[sub.id] : undefined
  const hints: [string, string][] =
    menuRow ? [['↑↓', 'move'], ['Enter', 'run'], ['Esc', 'close']]
    : capturing ? [['Esc', 'cancel']]
    : edit ? [['Enter', 'save'], ['Esc', 'cancel']]
    : sub?.kind === 'doc' ? [['A', 'approve'], ['C', 'correct'], ['Enter', 'open page'], ['Ctrl+K', 'actions'], ['Esc', 'back']]
    : sub?.kind === 'fields' ? [['Enter', 'switch'], ['Esc', 'back']]
    : sub ? [['Esc', 'back']]
    : mode === 'review' ? [['A', 'accept'], ['C', 'correct'], ['S', 'skip'], ['Enter', 'open page'], ['Esc', 'back']]
    : mode === 'root' && !query ? [['', 'Type to search'], ['?', 'at the end asks'], ['/', 'commands'], ['Esc', 'hide']]
    : mode === 'root' ? [['↑↓', 'move'], ['Enter', 'open'], ['Ctrl+Enter', 'Explorer'], ['→', rows[at]?.answer ? 'details' : 'facts'], ...(ans ? [] : [['Tab', 'ask']] as [string, string][]), ['Ctrl+K', 'actions']]
    : mode === 'settings' ? [['Enter', 'change'], ['→', 'fields'], ['Esc', 'back']]
    : mode === 'watch' ? [['Enter', query ? 'watch' : 'show'], ['Ctrl+K', 'actions'], ['Esc', 'back']]
    : mode === 'upcoming' ? [['Enter', 'open'], ['→', 'facts'], ['Ctrl+K', 'actions'], ['Esc', 'back']]
    : [['Enter', 'open'], ['Esc', 'back']]
  const empty =
    sub?.kind === 'doc' ? (doc ? 'Sniff found no facts in this document.' : 'Loading…')
    : sub?.kind === 'log' ? (log ? 'No agent has asked anything yet.' : 'Loading…')
    : sub ? ''
    : mode === 'root' ? (query && !query.startsWith('/') && !busy && !asking && !err ? 'No files match. End with ? to ask instead, or add a folder with /watch.' : '')
    : mode === 'upcoming' ? (up ? 'Nothing falls due or expires in the next 60 days.' : 'Loading…')
    : mode === 'watch' ? (watch ? (query ? '' : 'No folders yet. Type a folder path and press Enter.') : 'Loading…')
    : mode === 'overview' || mode === 'settings' ? (rows.length || t ? 'Nothing matches.' : 'Loading…')
    : ''

  return (
    // A click on empty space would move focus to the page and the keys would stop working; keep it in the input.
    <div className="relative flex h-full flex-col bg-bg text-fg" onKeyDown={onKey}
      onMouseDown={(e) => { if (!(e.target as HTMLElement).closest('input, button')) e.preventDefault() }}>
      <header className="flex h-14 shrink-0 items-center gap-2 border-b border-line px-4">
        {cmd
          ? <span className="flex shrink-0 items-center gap-1.5 rounded bg-accent-soft px-2 py-1 text-[12px] font-semibold text-accent"><cmd.icon size={13} aria-hidden /> {cmd.name}</span>
          : <Search size={18} className="mr-1 shrink-0 text-muted" aria-hidden />}
        {crumb && (
          <>
            <ChevronRight size={14} className="shrink-0 text-muted" aria-hidden />
            <span className="max-w-[280px] shrink-0 truncate rounded bg-accent-soft px-2 py-1 text-[12px] font-semibold text-accent">{crumb}</span>
          </>
        )}
        <input
          ref={input} autoFocus spellCheck={false} autoComplete="off"
          value={sub ? '' : q} onChange={(e) => setQ(e.target.value)} readOnly={!!sub || mode === 'review' || capturing}
          placeholder={sub ? '' : PLACEHOLDER[mode]}
          role="combobox" aria-expanded={rows.length > 0} aria-controls="results" aria-autocomplete="list"
          aria-activedescendant={rows.length && !(mode === 'review' && !sub) ? `opt-${at}` : undefined}
          aria-label={crumb ?? (cmd ? `${cmd.name}: type to filter` : 'Search your documents, or ask a question')}
          className="h-full min-w-0 flex-1 bg-transparent px-1 text-[18px] outline-none placeholder:text-muted"
        />
        {(busy || asking) && <LoaderCircle size={16} className="shrink-0 animate-spin text-muted motion-reduce:animate-none" aria-label="Working" />}
      </header>

      <main className="scroll min-h-0 flex-1 overflow-y-auto py-1.5">
        {mode === 'root' && !query && ov && <p className="px-4 py-1.5 text-[12px] text-muted">{summary(ov)}</p>}
        {asking && !ans && (
          <div className="mx-1.5 mb-1.5 flex items-center gap-2 rounded-lg border border-line px-4 py-3 text-[13px] text-muted">
            <LoaderCircle size={14} className="animate-spin motion-reduce:animate-none" aria-hidden /> Reading your documents…
          </div>
        )}
        {doc && <p className="truncate px-4 pb-1.5 text-[12px] text-muted">{label(doc.doc_type ?? 'other')} · {plural(doc.facts.length, 'fact', 'facts')} · {doc.path}</p>}
        {mode === 'review' && !sub ? (
          !queue ? <Msg>Loading…</Msg>
          : head ? (
            <ReviewCard item={head} left={queue.length} edit={editing(head.fact).edit} setEdit={editing(head.fact).set} error={editErr}
              accept={() => approve(head.fact)} correct={(v) => correct(head.fact, v)} skip={skip} />
          ) : <Msg>Nothing to review. Every fact is verified, corrected or approved.</Msg>
        ) : (
          <div id="results" role="listbox" aria-label={crumb ?? cmd?.name ?? 'Results'}>
            {rows.map((r, i) => (
              <Fragment key={r.id}>
                {r.head && <div aria-hidden className="px-4 pb-1 pt-2.5 text-[12px] font-semibold text-fg2">{r.head}</div>}
                {r.answer ? (
                  <AnswerCard a={r.answer} id={`opt-${i}`} selected={at === i} details={details} open={open} hover={() => at !== i && setSel(i)} />
                ) : (
                  <div id={`opt-${i}`} role="option" aria-selected={at === i} onMouseMove={() => at !== i && setSel(i)} onMouseDown={(e) => e.preventDefault()} onClick={() => r.enter?.()}
                    className={`relative mx-1.5 flex items-center gap-3 rounded px-3 ${r.detail ? 'min-h-[52px] py-1.5' : 'min-h-10 py-1'} ${at === i ? 'bg-sel' : 'hover:bg-hover'}`}>
                    {at === i && <Pill />}
                    {r.icon && <r.icon size={18} className="shrink-0 text-fg2" aria-hidden />}
                    {r.body ?? (
                      <div className="min-w-0 flex-1">
                        <div className="flex items-center gap-2"><span className="truncate">{r.title}</span>{r.tag && <Tag>{r.tag}</Tag>}</div>
                        {r.detail && <div className="truncate text-[12px] text-muted">{r.detail}</div>}
                      </div>
                    )}
                    {r.right !== undefined && <div className="tnum shrink-0 text-[12px] text-muted">{r.right}</div>}
                  </div>
                )}
              </Fragment>
            ))}
          </div>
        )}
        {!rows.length && empty && !(mode === 'review' && !sub) && <Msg>{empty}</Msg>}
        {(err || watch?.error) && <p className="px-4 py-2 text-[12px] text-danger">{err || `The watcher stopped: ${watch?.error}`}</p>}
      </main>

      <footer className="flex h-8 shrink-0 items-center gap-4 border-t border-line px-4 text-[12px] text-muted">
        {hints.map(([k, text]) => <span key={text} className="whitespace-nowrap">{k && <kbd>{k}</kbd>} {text}</span>)}
        <span role="status" className="ml-auto truncate text-fg2">{status}</span>
      </footer>

      {menuRow && <Menu row={menuRow} close={closeMenu} />}
    </div>
  )
}

const Msg = ({ children }: { children: string }) => <p className="px-4 py-6 text-center text-[13px] text-muted">{children}</p>
