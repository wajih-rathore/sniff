import { useEffect, useState } from 'react'
import { CalendarClock, ClipboardCheck, Library, Search, Settings } from 'lucide-react'
import SearchView from './views/Search'
import LibraryView from './views/Library'
import UpcomingView from './views/Upcoming'
import SettingsView from './views/Settings'
import ReviewView from './views/Review'
import type { Nav } from './ui'

const TABS = [
  { id: 'search', name: 'Search', icon: Search },
  { id: 'review', name: 'Review', icon: ClipboardCheck },
  { id: 'library', name: 'Library', icon: Library },
  { id: 'upcoming', name: 'Upcoming', icon: CalendarClock },
  { id: 'settings', name: 'Settings', icon: Settings },
] as const

export default function App() {
  const [nav, go] = useState<Nav>({ view: 'search' })
  const view = nav.view
  const [focus, setFocus] = useState(0) // bumped on Ctrl+K so the search bar focuses itself

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'k') {
        e.preventDefault()
        go({ view: 'search' })
        setFocus((n) => n + 1)
      }
    }
    const onGo = (e: Event) => go({ view: (e as CustomEvent<Nav['view']>).detail }) // the desktop tray's Settings item
    window.addEventListener('keydown', onKey)
    window.addEventListener('sniff:go', onGo)
    return () => {
      window.removeEventListener('keydown', onKey)
      window.removeEventListener('sniff:go', onGo)
    }
  }, [])

  return (
    <div className="flex h-full">
      <nav className="flex w-44 shrink-0 flex-col gap-0.5 border-r border-line p-3">
        <div className="px-2 pb-3 pt-1 text-[13px] font-semibold tracking-wide">Sniff</div>
        {TABS.map(({ id, name, icon: Icon }) => (
          <button
            key={id}
            onClick={() => go({ view: id })}
            className={`flex items-center gap-2 rounded-md px-2 py-1.5 text-left text-[13px] ${
              view === id ? 'bg-accent-soft text-accent' : 'text-muted hover:bg-hover hover:text-fg'
            }`}
          >
            <Icon size={15} /> {name}
          </button>
        ))}
        <div className="mt-auto px-2 text-[11px] text-muted">Ctrl+K to search</div>
      </nav>
      <main className="min-w-0 flex-1 overflow-y-auto">
        <div hidden={view !== 'search'}><SearchView focus={focus} go={go} /></div>
        {view === 'review' && <ReviewView tab={nav.tab ?? 'queue'} go={go} />}
        {view === 'library' && <LibraryView open={nav.doc} go={go} />}
        {view === 'upcoming' && <UpcomingView />}
        {view === 'settings' && <SettingsView />}
      </main>
    </div>
  )
}
