import { Link, Route, Routes } from 'react-router-dom'
import ContainerList from './pages/ContainerList'
import ContainerDetail from './pages/ContainerDetail'

function App() {
  return (
    <div className="min-h-screen bg-base-200 text-base-content">
      <header className="border-b border-base-300">
        <div className="mx-auto flex h-12 w-full max-w-6xl items-center gap-2 px-4 sm:px-6">
          <Link to="/" className="flex items-center gap-2 font-mono text-sm font-medium">
            <span aria-hidden className="inline-block size-2 rounded-xs bg-primary" />
            console
          </Link>
        </div>
      </header>
      <main className="mx-auto w-full max-w-6xl px-4 py-6 sm:px-6">
        <Routes>
          <Route path="/" element={<ContainerList />} />
          <Route path="/containers/:id" element={<ContainerDetail />} />
        </Routes>
      </main>
    </div>
  )
}

export default App
