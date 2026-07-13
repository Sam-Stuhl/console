import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { controlApp, fetchProjectContainer, type ControlAction } from '../api/client'
import { ControlButton, RestartIcon, StartIcon, StopIcon } from './ControlButton'

const LABEL: Record<string, string> = {
  running: 'running',
  exited: 'stopped',
  created: 'created',
  restarting: 'restarting',
  paused: 'paused',
  absent: 'no container',
}

const DOT: Record<string, string> = {
  running: 'bg-success',
  restarting: 'bg-warning motion-safe:animate-pulse',
  exited: 'bg-warning',
  paused: 'bg-warning',
}

export default function ControlsSection({ projectId }: { projectId: string }) {
  const queryClient = useQueryClient()
  const [confirmingStop, setConfirmingStop] = useState(false)
  const [actionError, setActionError] = useState<string | null>(null)

  const { data } = useQuery({
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

  const state = data?.state
  if (!state) return null // keep the header clean while the state loads

  const pending = control.isPending
  const startable = state === 'exited' || state === 'created' || state === 'paused'

  return (
    <div className="flex flex-col items-end gap-1.5">
      <div className="flex flex-wrap items-center justify-end gap-2.5">
        <span className="inline-flex items-center gap-1.5 font-mono text-xs">
          <span className={`h-1.5 w-1.5 rounded-full ${DOT[state] ?? 'bg-base-300'}`} />
          <span className={state === 'running' ? '' : 'text-muted'}>
            {LABEL[state] ?? state}
          </span>
        </span>

        {state === 'running' && (
          <>
            <ControlButton
              tone="accent"
              icon={RestartIcon}
              label={pending ? '…' : 'restart'}
              disabled={pending}
              onClick={() => control.mutate('restart')}
            />
            {confirmingStop ? (
              <span className="inline-flex items-center gap-2">
                <ControlButton
                  tone="error"
                  icon={StopIcon}
                  label="confirm"
                  onClick={() => control.mutate('stop')}
                />
                <button
                  type="button"
                  className="font-mono text-xs text-muted transition-colors duration-150 hover:text-base-content"
                  onClick={() => setConfirmingStop(false)}
                >
                  keep
                </button>
              </span>
            ) : (
              <ControlButton
                tone="error"
                icon={StopIcon}
                label="stop"
                disabled={pending}
                onClick={() => setConfirmingStop(true)}
              />
            )}
          </>
        )}

        {startable && (
          <ControlButton
            tone="success"
            icon={StartIcon}
            label={pending ? '…' : 'start'}
            disabled={pending}
            onClick={() => control.mutate('start')}
          />
        )}
      </div>
      {confirmingStop && (
        <span className="font-mono text-[11px] text-faint">stopping takes the site offline</span>
      )}
      {actionError && <p className="font-mono text-xs text-error">{actionError}</p>}
    </div>
  )
}
