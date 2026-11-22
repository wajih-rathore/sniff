import { useEffect, useState } from 'react'
import { BarChart3, CalendarClock, Library, Search, Settings } from 'lucide-react'
import SearchView from './views/Search'
import LibraryView from './views/Library'
import UpcomingView from './views/Upcoming'
import EvaluationView from './views/Evaluation'
import SettingsView from './views/Settings'

const TABS = [
  { id: 'search', name: 'Search', icon: Search },
  { id: 'library', name: 'Library', icon: Library },
  { id: 'upcoming', name: 'Upcoming', icon: CalendarClock },
  { id: 'evaluation', name: 'Evaluation', icon: BarChart3 },
  { id: 'settings', name: 'Settings', icon: Settings },
] as const

export default function App() {
  const [view, setView] = useState<(typeof TABS)[number]['id']>('search')
  const [focus, setFocus] = useState(0) // bumped on Ctrl+K so the search bar focuses itself

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'k') {
        e.preventDefault()
        setView('search')
        setFocus((n) => n + 1)
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [])

  return (
    <div className="flex h-full">
      <nav className="flex w-44 shrink-0 flex-col gap-0.5 border-r border-line p-3">
        <div className="px-2 pb-3 pt-1 text-[13px] font-semibold tracking-wide">Sniff</div>
        {TABS.map(({ id, name, icon: Icon }) => (
          <button
            key={id}
            onClick={() => setView(id)}
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
        <div hidden={view !== 'search'}><SearchView focus={focus} /></div>
        {view === 'library' && <LibraryView />}
        {view === 'upcoming' && <UpcomingView />}
        {view === 'evaluation' && <EvaluationView />}
        {view === 'settings' && <SettingsView />}
      </main>
    </div>
  )
}
