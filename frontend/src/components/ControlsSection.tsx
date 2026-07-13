import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { controlApp, fetchProjectContainer, type ControlAction } from '../api/client'

const DOT: Record<string, string> = {
  running: 'bg-success',
  exited: 'bg-warning',
  restarting: 'bg-warning motion-safe:animate-pulse',
}

export default function ControlsSection({ projectId }: { projectId: string }) {
  const queryClient = useQueryClient()
  const [confirmingStop, setConfirmingStop] = useState(false)
  const [actionError, setActionError] = useState<string | null>(null)

  const { data, isLoading } = useQuery({
    queryKey: ['project-container', projectId],
    queryFn: () => fetchProjectContainer(projectId),
    refetchInterval: 5000,
  })

  const control = useMutation({
    mutationFn: (action: ControlAction) => controlApp(projectId, action),
    onSuccess: () => {
      setActionError(null)
      setConfirmingStop(false)
      queryClient.invalidateQueries({ queryKey: ['project-container', projectId] })
      queryClient.invalidateQueries({ queryKey: ['deployments', projectId] })
    },
    onError: (err: Error) => setActionError(err.message),
  })

  if (isLoading) return <span className="skeleton h-6 w-64" />

  const state = data?.state ?? 'absent'
  const running = state === 'running'
  const pending = control.isPending

  if (state === 'absent') {
    return (
      <p className="font-mono text-xs text-faint">
        no container yet. it appears once a deploy goes live.
      </p>
    )
  }

  return (
    <div className="flex flex-col gap-2">
      <div className="flex flex-wrap items-center gap-4 font-mono text-xs">
        <span className="inline-flex items-center gap-2">
          <span className={`h-1.5 w-1.5 rounded-full ${DOT[state] ?? 'bg-base-300'}`} />
          <span className={running ? '' : 'text-muted'}>{state}</span>
        </span>

        {running ? (
          <>
            <button
              type="button"
              disabled={pending}
              className="text-muted transition-colors duration-150 hover:text-base-content hover:underline disabled:opacity-40"
              onClick={() => control.mutate('restart')}
            >
              {pending ? 'working…' : 'restart'}
            </button>
            {confirmingStop ? (
              <span className="inline-flex items-center gap-3">
                <button
                  type="button"
                  className="text-error/90 hover:text-error hover:underline"
                  onClick={() => control.mutate('stop')}
                >
                  confirm stop
                </button>
                <button
                  type="button"
                  className="text-muted transition-colors duration-150 hover:text-base-content"
                  onClick={() => setConfirmingStop(false)}
                >
                  keep running
                </button>
              </span>
            ) : (
              <button
                type="button"
                disabled={pending}
                className="text-error/80 transition-colors duration-150 hover:text-error hover:underline disabled:opacity-40"
                onClick={() => setConfirmingStop(true)}
              >
                stop
              </button>
            )}
          </>
        ) : (
          <button
            type="button"
            disabled={pending}
            className="text-muted transition-colors duration-150 hover:text-base-content hover:underline disabled:opacity-40"
            onClick={() => control.mutate('start')}
          >
            {pending ? 'working…' : 'start'}
          </button>
        )}
      </div>
      {running && confirmingStop && (
        <p className="font-mono text-xs text-faint">
          stopping takes the site offline until you start it again.
        </p>
      )}
      {actionError && <p className="font-mono text-xs text-error">{actionError}</p>}
    </div>
  )
}
