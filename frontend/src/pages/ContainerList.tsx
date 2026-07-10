import { useQuery } from '@tanstack/react-query'
import { Link, useNavigate } from 'react-router-dom'
import { fetchContainers } from '../api/client'
import { duration } from '../lib/format'
import StateBadge from '../components/StateBadge'
import StatsCells from '../components/StatsCells'

export default function ContainerList() {
  const navigate = useNavigate()
  const { data: containers, isError } = useQuery({
    queryKey: ['containers'],
    queryFn: fetchContainers,
    refetchInterval: 5000,
  })

  if (isError) {
    return (
      <div className="rounded-box border border-error/40 px-4 py-3 font-mono text-sm">
        <span className="text-error">api unreachable</span>
        <span className="text-muted"> is uvicorn running on :8000?</span>
      </div>
    )
  }

  const running = containers?.filter((c) => c.state === 'running').length ?? 0

  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h1 className="text-base font-semibold">Containers</h1>
        {containers && (
          <p className="font-mono text-xs text-muted">
            {running} running
            {containers.length - running > 0 && ` · ${containers.length - running} stopped`}
          </p>
        )}
      </div>

      <div className="overflow-x-auto">
        <table className="w-full min-w-2xl text-sm">
          <thead>
            <tr className="border-b border-base-300 text-left font-mono text-xs text-muted">
              <th className="py-2 pr-4 font-normal">name</th>
              <th className="py-2 pr-4 font-normal">image</th>
              <th className="py-2 pr-4 font-normal">state</th>
              <th className="py-2 pr-4 text-right font-normal">uptime</th>
              <th className="py-2 pr-4 text-right font-normal">cpu</th>
              <th className="py-2 text-right font-normal">memory</th>
            </tr>
          </thead>
          <tbody>
            {containers
              ? containers.map((c) => (
                  <tr
                    key={c.id}
                    onClick={() => navigate(`/containers/${c.id}`)}
                    className="cursor-pointer border-b border-base-300/50 transition-colors duration-150 hover:bg-base-100"
                  >
                    <td className="py-2.5 pr-4">
                      <Link
                        to={`/containers/${c.id}`}
                        className="font-mono text-sm font-medium hover:text-primary"
                        onClick={(e) => e.stopPropagation()}
                      >
                        {c.name}
                      </Link>
                    </td>
                    <td className="max-w-64 truncate py-2.5 pr-4 font-mono text-xs text-muted">
                      {c.image}
                    </td>
                    <td className="py-2.5 pr-4">
                      <StateBadge state={c.state} exitCode={c.exit_code} />
                    </td>
                    <td className="py-2.5 pr-4 text-right font-mono text-xs tabular-nums text-muted">
                      {duration(c.started_at) || '--'}
                    </td>
                    <StatsCells id={c.id} running={c.state === 'running'} />
                  </tr>
                ))
              : Array.from({ length: 4 }, (_, i) => (
                  <tr key={i} className="border-b border-base-300/50">
                    <td className="py-3 pr-4"><span className="skeleton block h-3 w-32" /></td>
                    <td className="py-3 pr-4"><span className="skeleton block h-3 w-40" /></td>
                    <td className="py-3 pr-4"><span className="skeleton block h-3 w-16" /></td>
                    <td className="py-3 pr-4"><span className="skeleton ml-auto block h-3 w-10" /></td>
                    <td className="py-3 pr-4"><span className="skeleton ml-auto block h-3 w-10" /></td>
                    <td className="py-3"><span className="skeleton ml-auto block h-3 w-20" /></td>
                  </tr>
                ))}
          </tbody>
        </table>
        {containers && containers.length === 0 && (
          <p className="py-8 text-center font-mono text-sm text-muted">
            no containers. anything started with docker run will appear here.
          </p>
        )}
      </div>
    </div>
  )
}
