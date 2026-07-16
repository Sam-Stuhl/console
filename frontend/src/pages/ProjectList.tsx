import { useQuery } from '@tanstack/react-query'
import { Link, useNavigate } from 'react-router-dom'
import { fetchProjects } from '../api/client'
import { localDay } from '../lib/format'
import ProjectIcon from '../components/ProjectIcon'

export default function ProjectList() {
  const navigate = useNavigate()
  const { data: projects, isError } = useQuery({
    queryKey: ['projects'],
    queryFn: fetchProjects,
  })

  if (isError) {
    return (
      <div className="rounded-box border border-error/40 px-4 py-3 font-mono text-sm">
        <span className="text-error">api unreachable</span>
        <span className="text-muted"> is uvicorn running on :8000?</span>
      </div>
    )
  }

  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h1 className="text-base font-semibold">Projects</h1>
        <Link to="/projects/new" className="btn btn-primary btn-sm font-mono">
          new project
        </Link>
      </div>

      {projects && projects.length === 0 && (
        <div className="border-t border-base-300 py-10 text-center">
          <p className="font-mono text-sm text-muted">no projects yet</p>
          <p className="mx-auto mt-2 max-w-md font-mono text-xs text-faint">
            a project registers a repo so the deploy pipeline can find it. the
            repo needs a console.toml and the deploy workflow stub, then every
            push to main deploys here.
          </p>
        </div>
      )}

      {projects && projects.length > 0 && (
        <div className="overflow-x-auto">
          <table className="w-full min-w-xl text-sm">
            <thead>
              <tr className="border-b border-base-300 text-left font-mono text-xs text-muted">
                <th className="py-2 pr-4 font-normal">name</th>
                <th className="py-2 pr-4 font-normal">repo</th>
                <th className="py-2 pr-4 font-normal">subdomain</th>
                <th className="py-2 pr-4 font-normal">branch</th>
                <th className="py-2 text-right font-normal">created</th>
              </tr>
            </thead>
            <tbody>
              {projects.map((p) => (
                <tr
                  key={p.id}
                  onClick={() => navigate(`/projects/${p.id}`)}
                  className="cursor-pointer border-b border-base-300/50 transition-colors duration-150 hover:bg-base-100"
                >
                  <td className="py-2.5 pr-4">
                    <Link
                      to={`/projects/${p.id}`}
                      className="flex items-center gap-2.5 font-mono text-sm font-medium hover:text-primary"
                      onClick={(e) => e.stopPropagation()}
                    >
                      <ProjectIcon project={p} size={22} rounded="rounded-md" />
                      {p.name}
                    </Link>
                  </td>
                  <td className="py-2.5 pr-4 font-mono text-xs text-muted">{p.repo}</td>
                  <td className="py-2.5 pr-4 font-mono text-xs text-muted">{p.subdomain}</td>
                  <td className="py-2.5 pr-4 font-mono text-xs text-muted">{p.branch}</td>
                  <td className="py-2.5 text-right font-mono text-xs tabular-nums text-muted">
                    {localDay(p.created_at)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {!projects && !isError && (
        <div className="flex flex-col gap-3 border-t border-base-300 pt-3">
          <span className="skeleton h-3 w-64" />
          <span className="skeleton h-3 w-52" />
        </div>
      )}
    </div>
  )
}
