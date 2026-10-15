import { useEffect, useState } from 'react'
import { BrowserRouter, Route, Routes } from 'react-router-dom'
import { Waypoints } from 'lucide-react'
import { Pill } from './components/ui'
import { getJSON, type Health } from './lib/api'
import Workspace from './pages/Workspace'

function Header() {
  const [health, setHealth] = useState<Health | null>(null)
  useEffect(() => {
    getJSON<Health>('/api/health').then(setHealth).catch(() => setHealth({ api: false, graph: false, llm: false }))
  }, [])
  return (
    <header className="flex items-center gap-3 px-4 py-2.5" style={{ borderBottom: '1px solid var(--border)' }}>
      <Waypoints size={20} style={{ color: 'var(--s1)' }} aria-hidden />
      <span className="font-semibold">FlowTrace</span>
      {health && (
        <div className="ml-auto flex gap-2">
          <Pill ok={health.graph} label="Graph DB" />
          <Pill ok={health.llm} label="AI investigator" />
        </div>
      )}
    </header>
  )
}

export default function App() {
  return (
    <BrowserRouter>
      <Header />
      <main className="p-3">
        <Routes>
          <Route path="*" element={<Workspace />} />
        </Routes>
      </main>
    </BrowserRouter>
  )
}
