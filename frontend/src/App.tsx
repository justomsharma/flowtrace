import { BrowserRouter, Route, Routes } from 'react-router-dom'
import Sidebar from './components/Sidebar'
import { BatchProvider } from './lib/batch'
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
              {/* The 3D screen, unchanged, in its dark console. */}
              <Route path="/network" element={<div className="theme-dark min-h-screen p-3"><Workspace /></div>} />
              <Route path="/results" element={<div className="px-6 py-5"><Results /></div>} />
              <Route path="/reports" element={<Reports />} />
              <Route path="*" element={<Today />} />
            </Routes>
          </main>
        </div>
      </BatchProvider>
    </BrowserRouter>
  )
}
