import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Link } from 'react-router-dom'
import type { DeploymentSummary } from '../api/client'
import { fetchDeployments, redeployDeployment, rollbackDeployment } from '../api/client'
import { since, took } from '../lib/format'
import DeployBadge from './DeployBadge'

export function canRollback(d: DeploymentSummary): boolean {
  // Only builds that actually served traffic; rows superseded straight
  // out of the queue never started a deploy.
  return d.status === 'superseded' && d.deploy_started_at !== null
}

export function canRedeploy(d: DeploymentSummary): boolean {
  // The latest build, once it has an image and is no longer in flight: retry a
  // failed one, or re-run the live one to pick up changed secrets.
  return d.image !== null && (d.status === 'live' || d.status === 'failed')
}

export function deployTook(d: DeploymentSummary): string {
  // For superseded rows finished_at is when they were replaced, not when
  // their deploy ended, so a duration would be nonsense.
  if (d.status !== 'live' && d.status !== 'failed') return ''
  return took(d.deploy_started_at, d.finished_at)
}

export default function DeploymentsSection({
  projectId,
  branch,
}: {
  projectId: string
  branch: string
}) {
  const queryClient = useQueryClient()
  const { data: deployments, isError, error } = useQuery({
    queryKey: ['deployments', projectId],
    queryFn: () => fetchDeployments(projectId),
    refetchInterval: 5000,
  })

  const [confirming, setConfirming] = useState<string | null>(null)
  const [actionError, setActionError] = useState<string | null>(null)

  const rollback = useMutation({
    mutationFn: (deploymentId: string) => rollbackDeployment(projectId, deploymentId),
    onSuccess: () => {
      setActionError(null)
      queryClient.invalidateQueries({ queryKey: ['deployments', projectId] })
    },
    onError: (err: Error) => setActionError(err.message),
  })

  const redeploy = useMutation({
    mutationFn: (deploymentId: string) => redeployDeployment(projectId, deploymentId),
    onSuccess: () => {
      setActionError(null)
      queryClient.invalidateQueries({ queryKey: ['deployments', projectId] })
    },
    onError: (err: Error) => setActionError(err.message),
  })

  if (isError) {
    return <p className="font-mono text-xs text-error">{(error as Error).message}</p>
  }
  if (!deployments) {
    return <span className="skeleton h-8 w-full max-w-2xl" />
  }
  if (deployments.length === 0) {
    return (
      <p className="font-mono text-xs text-faint">
        no deployments yet. push to {branch} and the build webhook will start one.
      </p>
    )
  }

  return (
    <div className="flex flex-col gap-3">
      <table className="w-full font-mono text-xs">
        <tbody>
          {deployments.map((d, i) => (
            <tr key={d.id} className="border-b border-base-300/40 last:border-none">
              <td className="w-40 py-2 pr-4">
                <DeployBadge status={d.status} substate={d.substate} />
              </td>
              <td className="w-20 py-2 pr-4">
                <Link
                  to={`/projects/${projectId}/deployments/${d.id}`}
                  className="text-accent hover:underline"
                >
                  {d.sha.slice(0, 7)}
                </Link>
              </td>
              <td className="max-w-0 truncate py-2 pr-4" title={d.commit_message ?? ''}>
                {d.commit_message || <span className="text-faint">(no message)</span>}
              </td>
              <td className="w-20 whitespace-nowrap py-2 pr-4 text-muted">
                {since(d.created_at)}
              </td>
              <td className="w-16 whitespace-nowrap py-2 pr-4 text-muted">
                {deployTook(d)}
              </td>
              <td className="w-36 py-2 text-right whitespace-nowrap">
                {i === 0 && canRedeploy(d) ? (
                  <button
                    type="button"
                    disabled={redeploy.isPending}
                    className="text-muted transition-colors duration-150 hover:text-base-content hover:underline"
                    onClick={() => redeploy.mutate(d.id)}
                  >
                    {redeploy.isPending ? 'redeploying…' : 'redeploy'}
                  </button>
                ) : canRollback(d) ? (
                  confirming === d.id ? (
                    <span className="inline-flex items-center gap-3">
                      <button
                        type="button"
                        className="text-warning hover:underline"
                        onClick={() => {
                          setConfirming(null)
                          rollback.mutate(d.id)
                        }}
                      >
                        confirm
                      </button>
                      <button
                        type="button"
                        className="text-muted transition-colors duration-150 hover:text-base-content hover:underline"
                        onClick={() => setConfirming(null)}
                      >
                        keep
                      </button>
                    </span>
                  ) : (
                    <button
                      type="button"
                      className="text-muted transition-colors duration-150 hover:text-base-content hover:underline"
                      onClick={() => setConfirming(d.id)}
                    >
                      roll back
                    </button>
                  )
                ) : null}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      {actionError && <p className="font-mono text-xs text-error">{actionError}</p>}
    </div>
  )
}
