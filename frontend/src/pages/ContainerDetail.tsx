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
      <div className="rounded-box border border-error/40 px-4 py-3 font-mono text-sm">
        <span className="text-error">container not found </span>
        <Link to="/" className="text-accent hover:underline">
          back to containers
        </Link>
      </div>
    )
  }

  return (
    <div className="flex flex-col gap-8">
      <div className="flex flex-col gap-5">
        <Link to="/" className="self-start font-mono text-xs text-muted hover:text-base-content">
          &larr; containers
        </Link>
        {c ? (
          <>
            <div className="flex flex-wrap items-center gap-4">
              <h1 className="font-mono text-xl font-semibold">{c.name}</h1>
              <StateBadge state={c.state} exitCode={c.exit_code} />
            </div>
            <dl className="grid grid-cols-[auto_1fr] gap-x-6 gap-y-1.5 text-sm sm:grid-cols-[auto_1fr_auto_1fr]">
              <Meta label="image" value={c.image} />
              <Meta label="id" value={c.id} />
              <Meta
                label={c.state === 'running' ? 'up for' : 'finished'}
                value={c.state === 'running' ? duration(c.started_at) : localTime(c.finished_at)}
              />
              <Meta label="restart" value={c.restart_policy || 'no'} />
              <Meta label="networks" value={c.networks.join(', ') || 'none'} />
              <Meta
                label="ports"
                value={
                  c.ports
                    .map((p) =>
                      p.host_ports.length
                        ? `${p.host_ports.join(',')} -> ${p.container_port}`
                        : p.container_port,
                    )
                    .join('  ') || 'none'
                }
              />
            </dl>
          </>
        ) : (
          <div className="flex flex-col gap-3">
            <span className="skeleton h-6 w-64" />
            <span className="skeleton h-24 w-full max-w-2xl" />
          </div>
        )}
      </div>

      <Section title="environment">{c && <EnvTable env={c.env} />}</Section>
      <Section title="logs">{id && <LogView id={id} />}</Section>
    </div>
  )
}

function Meta({ label, value }: { label: string; value: string }) {
  return (
    <>
      <dt className="font-mono text-xs leading-6 text-muted">{label}</dt>
      <dd className="truncate font-mono text-xs leading-6" title={value}>
        {value}
      </dd>
    </>
  )
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="flex flex-col gap-3 border-t border-base-300 pt-4">
      <h2 className="font-mono text-xs text-muted">{title}</h2>
      {children}
    </section>
  )
}
