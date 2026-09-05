import { useState } from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { updateAutoBuild } from '../api/client'

/**
 * Build on push. When on, the console polls the tracked branch and builds a
 * new head on the server, then deploys it: what GitHub Actions used to do.
 * Turning it on takes the current head as already seen, so nothing pushed
 * earlier gets built by surprise; "build now" is for that.
 */
export default function AutoBuildToggle({
  projectId,
  branch,
  enabled,
}: {
  projectId: string
  branch: string
  enabled: boolean
}) {
  const queryClient = useQueryClient()
  const [error, setError] = useState<string | null>(null)
  const save = useMutation({
    mutationFn: (next: boolean) => updateAutoBuild(projectId, next),
    onSuccess: () => {
      setError(null)
      queryClient.invalidateQueries({ queryKey: ['project', projectId] })
      queryClient.invalidateQueries({ queryKey: ['projects'] })
    },
    onError: (err: Error) => setError(err.message),
  })
  return (
    <div className="flex flex-wrap items-center gap-3 font-mono text-xs">
      <label className="flex cursor-pointer items-center gap-2">
        <input
          type="checkbox"
          className="checkbox checkbox-xs"
          checked={enabled}
          disabled={save.isPending}
          onChange={(e) => save.mutate(e.target.checked)}
        />
        <span>build on push</span>
      </label>
      <span className="text-faint">
        {enabled
          ? `a push to ${branch} is built on the server and deployed`
          : `off: pushes to ${branch} deploy nothing until you build`}
      </span>
      {error && <span className="text-error">{error}</span>}
    </div>
  )
}
