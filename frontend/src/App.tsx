import { Link, Route, Routes } from 'react-router-dom'
import ContainerList from './pages/ContainerList'
import ContainerDetail from './pages/ContainerDetail'

function App() {
  return (
    <div className="min-h-screen bg-base-200">
      <div className="navbar bg-base-100 shadow-sm">
        <div className="mx-auto flex w-full max-w-5xl items-center">
          <Link to="/" className="btn btn-ghost text-lg font-semibold tracking-tight">
            console
          </Link>
        </div>
      </div>
      <main className="mx-auto max-w-5xl p-4">
        <Routes>
          <Route path="/" element={<ContainerList />} />
          <Route path="/containers/:id" element={<ContainerDetail />} />
        </Routes>
      </main>
    </div>
  )
}

export default App
