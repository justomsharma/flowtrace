import { useEffect, useState } from 'react'
import { BrowserRouter, NavLink, Route, Routes } from 'react-router-dom'
import { Waypoints } from 'lucide-react'
import { Pill } from './components/ui'
import { getJSON, type Health } from './lib/api'
import CaseView from './pages/CaseView'
import Dashboard from './pages/Dashboard'
import Investigate from './pages/Investigate'

function Header() {
  const [health, setHealth] = useState<Health | null>(null)
  useEffect(() => {
    getJSON<Health>('/api/health').then(setHealth).catch(() => setHealth({ api: false, graph: false, llm: false }))
  }, [])
  const link = ({ isActive }: { isActive: boolean }) =>
    `px-3 py-1.5 rounded-md text-sm ${isActive ? 'font-semibold' : ''}`
  return (
    <header className="flex flex-wrap items-center gap-4 px-4 md:px-6 py-3" style={{ borderBottom: '1px solid var(--border)' }}>
      <div className="flex items-center gap-2">
        <Waypoints size={20} style={{ color: 'var(--s1)' }} aria-hidden />
        <span className="font-semibold">FlowTrace</span>
        <span className="text-xs hidden sm:inline" style={{ color: 'var(--muted)' }}>graph-powered AML investigation</span>
      </div>
      <nav className="flex gap-1">
        <NavLink to="/" end className={link} style={({ isActive }) => ({ background: isActive ? 'var(--surface-2)' : 'transparent' })}>Dashboard</NavLink>
        <NavLink to="/investigate" className={link} style={({ isActive }) => ({ background: isActive ? 'var(--surface-2)' : 'transparent' })}>Investigate</NavLink>
      </nav>
      {health && (
        <div className="ml-auto flex gap-2">
          <Pill ok={health.api} label="API" />
          <Pill ok={health.graph} label="FalkorDB" />
          <Pill ok={health.llm} label="Claude" />
        </div>
      )}
    </header>
  )
}

export default function App() {
  return (
    <BrowserRouter>
      <Header />
      <main className="px-4 md:px-6 py-4 max-w-[1500px] mx-auto">
        <Routes>
          <Route path="/" element={<Dashboard />} />
          <Route path="/investigate" element={<Investigate />} />
          <Route path="/case/:caseId" element={<CaseView />} />
        </Routes>
      </main>
    </BrowserRouter>
  )
}
