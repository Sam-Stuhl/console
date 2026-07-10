import { useQuery } from '@tanstack/react-query'
import { Link } from 'react-router-dom'
import { fetchContainers } from '../api/client'
import { duration } from '../lib/format'
import StateBadge from '../components/StateBadge'
import StatsCells from '../components/StatsCells'

export default function ContainerList() {
  const { data: containers, isError } = useQuery({
    queryKey: ['containers'],
    queryFn: fetchContainers,
    refetchInterval: 5000,
  })

  if (isError) {
    return (
      <div className="alert alert-error">
        Cannot reach the API. Is uvicorn running on :8000?
      </div>
    )
  }
  if (!containers) {
    return <span className="loading loading-spinner" />
  }

  return (
    <div className="card bg-base-100 shadow-sm">
      <div className="card-body overflow-x-auto p-0">
        <table className="table">
          <thead>
            <tr>
              <th>Name</th>
              <th>Image</th>
              <th>State</th>
              <th>Uptime</th>
              <th>CPU</th>
              <th>Memory</th>
            </tr>
          </thead>
          <tbody>
            {containers.map((c) => (
              <tr key={c.id} className="hover:bg-base-200">
                <td>
                  <Link to={`/containers/${c.id}`} className="link-hover font-medium">
                    {c.name}
                  </Link>
                </td>
                <td className="font-mono text-sm text-base-content/70">{c.image}</td>
                <td>
                  <StateBadge state={c.state} exitCode={c.exit_code} />
                </td>
                <td className="tabular-nums">{duration(c.started_at) || '–'}</td>
                <StatsCells id={c.id} running={c.state === 'running'} />
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}
