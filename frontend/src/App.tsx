import { lazy, Suspense } from 'react'
import { NavLink, Route, Routes } from 'react-router-dom'
import { ConsoleMark } from './components/Logo'
import ContainerList from './pages/ContainerList'
import ContainerDetail from './pages/ContainerDetail'
import ProjectList from './pages/ProjectList'
import ProjectNew from './pages/ProjectNew'
import ProjectDetail from './pages/ProjectDetail'
import DeploymentDetail from './pages/DeploymentDetail'
import ValidateToml from './pages/ValidateToml'
import Settings from './pages/Settings'

// xterm is heavy and only this route needs it; keep it out of the main bundle.
const ProjectTerminal = lazy(() => import('./pages/ProjectTerminal'))

function navClass({ isActive }: { isActive: boolean }) {
  return `font-mono text-sm transition-colors duration-150 ${
    isActive ? 'text-base-content' : 'text-muted hover:text-base-content'
  }`
}

function App() {
  return (
    <div className="min-h-screen bg-base-200 text-base-content">
      <header className="border-b border-base-300">
        <div className="mx-auto flex h-12 w-full max-w-6xl items-center gap-6 px-4 sm:px-6">
          <NavLink to="/" className="flex items-center gap-2 font-mono text-sm font-medium">
            <ConsoleMark className="size-5 text-primary" />
            console
          </NavLink>
          <nav className="flex items-center gap-4">
            <NavLink to="/" end className={navClass}>
              projects
            </NavLink>
            <NavLink to="/containers" end className={navClass}>
              containers
            </NavLink>
            <NavLink to="/validate" end className={navClass}>
              toml check
            </NavLink>
            <NavLink to="/settings" end className={navClass}>
              settings
            </NavLink>
          </nav>
        </div>
      </header>
      <main className="mx-auto w-full max-w-6xl px-4 py-6 sm:px-6">
        <Routes>
          <Route path="/" element={<ProjectList />} />
          <Route path="/projects/new" element={<ProjectNew />} />
          <Route path="/projects/:id" element={<ProjectDetail />} />
          <Route
            path="/projects/:id/terminal"
            element={
              <Suspense
                fallback={<div className="skeleton h-[70vh] w-full rounded-box" />}
              >
                <ProjectTerminal />
              </Suspense>
            }
          />
          <Route
            path="/projects/:id/deployments/:deploymentId"
            element={<DeploymentDetail />}
          />
          <Route path="/validate" element={<ValidateToml />} />
          <Route path="/settings" element={<Settings />} />
          <Route path="/containers" element={<ContainerList />} />
          <Route path="/containers/:id" element={<ContainerDetail />} />
        </Routes>
      </main>
    </div>
  )
}

export default App
