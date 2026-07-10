import { useQuery } from '@tanstack/react-query'
import { Link, useParams } from 'react-router-dom'
import { fetchContainer } from '../api/client'
import { duration, localTime } from '../lib/format'
import StateBadge from '../components/StateBadge'
import EnvTable from '../components/EnvTable'
import LogView from '../components/LogView'

export default function ContainerDetail() {
  const { id } = useParams<{ id: string }>()
  const { data: c, isError } = useQuery({
    queryKey: ['container', id],
    queryFn: () => fetchContainer(id!),
    refetchInterval: 5000,
    enabled: Boolean(id),
  })

  if (isError) {
    return (
      <div className="alert alert-error">
        Container not found. <Link to="/" className="link">Back to list</Link>
      </div>
    )
  }
  if (!c) {
    return <span className="loading loading-spinner" />
  }

  return (
    <div className="flex flex-col gap-4">
      <div className="breadcrumbs text-sm">
        <ul>
          <li><Link to="/">Containers</Link></li>
          <li>{c.name}</li>
        </ul>
      </div>

      <div className="card bg-base-100 shadow-sm">
        <div className="card-body">
          <div className="flex flex-wrap items-center gap-3">
            <h1 className="card-title font-mono">{c.name}</h1>
            <StateBadge state={c.state} exitCode={c.exit_code} />
          </div>
          <div className="grid grid-cols-1 gap-x-8 gap-y-1 text-sm sm:grid-cols-2">
            <Field label="Image" value={c.image} mono />
            <Field label="Id" value={c.id} mono />
            <Field
              label={c.state === 'running' ? 'Up for' : 'Finished at'}
              value={c.state === 'running' ? duration(c.started_at) : localTime(c.finished_at)}
            />
            <Field label="Restart policy" value={c.restart_policy || 'none'} />
            <Field label="Networks" value={c.networks.join(', ') || 'none'} />
            <Field
              label="Ports"
              value={
                c.ports
                  .map((p) =>
                    p.host_ports.length
                      ? `${p.host_ports.join(',')} -> ${p.container_port}`
                      : p.container_port,
                  )
                  .join('  ') || 'none'
              }
              mono
            />
          </div>
        </div>
      </div>

      <div className="card bg-base-100 shadow-sm">
        <div className="card-body">
          <h2 className="card-title text-base">Environment</h2>
          <EnvTable env={c.env} />
        </div>
      </div>

      <div className="card bg-base-100 shadow-sm">
        <div className="card-body">
          <h2 className="card-title text-base">Logs</h2>
          <LogView id={c.id} />
        </div>
      </div>
    </div>
  )
}

function Field({ label, value, mono = false }: { label: string; value: string; mono?: boolean }) {
  return (
    <div className="flex justify-between gap-4 border-b border-base-200 py-1 sm:justify-start">
      <span className="w-32 shrink-0 text-base-content/60">{label}</span>
      <span className={mono ? 'font-mono' : ''}>{value}</span>
    </div>
  )
}
