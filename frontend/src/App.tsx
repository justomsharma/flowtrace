import { BrowserRouter, Navigate, Route, Routes, useLocation } from 'react-router-dom'
import Sidebar from './components/Sidebar'
import { BatchProvider, useBatch } from './lib/batch'
import { homeTarget } from './lib/home'
import CaseOverview from './pages/CaseOverview'
import Reports from './pages/Reports'
import Results from './pages/Results'
import Today from './pages/Today'
import Workspace from './pages/Workspace'

export default function App() {
  return (
    <BrowserRouter>
      <BatchProvider>
        <div className="flex min-h-screen">
          <Sidebar />
          <main className="flex-1 min-w-0">
            <Routes>
              <Route path="/case" element={<CaseOverview />} />
              <Route path="/today" element={<Today />} />
              <Route path="/results" element={<div className="px-6 py-5"><Results /></div>} />
              <Route path="/reports" element={<Reports />} />
              {/* Home: the newest upload's Today's work; the 3D Live Network with a batch or before any upload. /network is kept for old links. */}
              <Route path="*" element={<Home />} />
            </Routes>
          </main>
        </div>
      </BatchProvider>
    </BrowserRouter>
  )
}

/** "/" opens the newest upload's Today's work. The upload box shows only when nothing was uploaded yet. */
function Home() {
  const { batchId, ready } = useBatch()
  const { pathname, search } = useLocation()
  const frame = 'min-h-screen p-3'
  if (pathname === '/') {
    if (!ready) return <div className={frame} />  // wait for the batch list, so the upload box never flashes
    const to = homeTarget(search, batchId)
    if (to) return <Navigate to={to} replace />
  }
  return <div className={frame}><Workspace /></div>
}
