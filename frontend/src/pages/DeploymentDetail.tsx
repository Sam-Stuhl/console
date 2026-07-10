import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Link, useNavigate, useParams } from 'react-router-dom'
import { fetchDeployment, rollbackDeployment } from '../api/client'
import { localTime, since, utcDate } from '../lib/format'
import { canRollback, deployTook } from '../components/DeploymentsSection'
import DeployBadge from '../components/DeployBadge'

const TERMINAL = new Set(['live', 'superseded', 'failed'])

export default function DeploymentDetail() {
  const { id, deploymentId } = useParams<{ id: string; deploymentId: string }>()
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const [confirming, setConfirming] = useState(false)
  const [actionError, setActionError] = useState<string | null>(null)

  const { data: d, isError } = useQuery({
    queryKey: ['deployment', deploymentId],
    queryFn: () => fetchDeployment(id!, deploymentId!),
    enabled: Boolean(id && deploymentId),
    // Poll fast while the deploy is moving, stop once it settles
    refetchInterval: (query) =>
      query.state.data && TERMINAL.has(query.state.data.status) ? false : 2000,
  })

  const rollback = useMutation({
    mutationFn: () => rollbackDeployment(id!, deploymentId!),
    onSuccess: (data) => {
      queryClient.invalidateQueries({ queryKey: ['deployments', id] })
      navigate(`/projects/${id}/deployments/${data.deployment_id}`)
    },
    onError: (err: Error) => setActionError(err.message),
  })

  if (isError) {
    return (
      <div className="rounded-box border border-error/40 px-4 py-3 font-mono text-sm">
        <span className="text-error">deployment not found </span>
        <Link to={`/projects/${id}`} className="text-accent hover:underline">
          back to project
        </Link>
      </div>
    )
  }

  return (
    <div className="flex flex-col gap-8">
      <div className="flex flex-col gap-5">
        <Link
          to={`/projects/${id}`}
          className="self-start font-mono text-xs text-muted hover:text-base-content"
        >
          &larr; project
        </Link>
        {d ? (
          <>
            <div className="flex flex-wrap items-center gap-4">
              <h1 className="font-mono text-xl font-semibold">{d.sha.slice(0, 7)}</h1>
              <DeployBadge status={d.status} substate={d.substate} />
              {canRollback(d) &&
                (confirming ? (
                  <span className="inline-flex items-center gap-3 font-mono text-xs">
                    <button
                      type="button"
                      className="text-warning hover:underline"
                      onClick={() => {
                        setConfirming(false)
                        rollback.mutate()
                      }}
                    >
                      really roll back?
                    </button>
                    <button
                      type="button"
                      className="text-muted hover:text-base-content hover:underline"
                      onClick={() => setConfirming(false)}
                    >
                      keep
                    </button>
                  </span>
                ) : (
                  <button
                    type="button"
                    className="font-mono text-xs text-muted transition-colors duration-150 hover:text-base-content hover:underline"
                    onClick={() => setConfirming(true)}
                  >
                    roll back to this build
                  </button>
                ))}
            </div>
            {d.commit_message && (
              <p className="font-mono text-sm text-base-content/80">{d.commit_message}</p>
            )}
            {d.failure_reason && (
              <p className="max-w-3xl rounded-box border border-error/40 px-3 py-2 font-mono text-xs text-error">
                {d.failure_reason}
              </p>
            )}
            {actionError && <p className="font-mono text-xs text-error">{actionError}</p>}
            <dl className="grid grid-cols-[auto_1fr] gap-x-6 gap-y-1.5 text-sm sm:grid-cols-[auto_1fr_auto_1fr]">
              <Meta label="image" value={d.image ?? ''} />
              <Meta label="container" value={d.container_name ?? ''} />
              <Meta label="started" value={`${localTime(isoLocal(d.created_at))} (${since(d.created_at)})`} />
              <Meta label="deploy took" value={deployTook(d)} />
              {d.run_url ? (
                <>
                  <dt className="font-mono text-xs leading-6 text-muted">build run</dt>
                  <dd className="truncate font-mono text-xs leading-6">
                    <a
                      href={d.run_url}
                      target="_blank"
                      rel="noreferrer"
                      className="hover:text-primary"
                    >
                      {d.run_url.replace('https://github.com/', '')}
                    </a>
                  </dd>
                </>
              ) : null}
              <Meta
                label="router priority"
                value={d.router_priority === null ? '' : String(d.router_priority)}
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

      <Section title="deploy log">
        {d ? (
          <pre className="max-h-96 overflow-auto rounded-box border border-base-300/60 bg-neutral p-4 font-mono text-xs leading-relaxed text-neutral-content">
            {d.log || <span className="text-faint">(nothing logged yet)</span>}
          </pre>
        ) : (
          <div className="skeleton h-40 w-full rounded-box" />
        )}
      </Section>

      {d?.config_snapshot && (
        <Section title="config snapshot">
          <pre className="max-h-96 overflow-auto rounded-box border border-base-300/60 bg-base-100 p-4 font-mono text-xs leading-relaxed">
            {formatSnapshot(d.config_snapshot)}
          </pre>
        </Section>
      )}
    </div>
  )
}

function isoLocal(iso: string): string {
  return utcDate(iso).toISOString()
}

function formatSnapshot(snapshot: string): string {
  try {
    return JSON.stringify(JSON.parse(snapshot), null, 2)
  } catch {
    return snapshot
  }
}

function Meta({ label, value }: { label: string; value: string }) {
  if (!value) return null
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
