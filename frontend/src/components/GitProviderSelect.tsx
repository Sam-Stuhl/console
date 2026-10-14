import { useState } from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import type { GitProvider } from '../api/client'
import { updateGitProvider } from '../api/client'

/**
 * Where the console reads and builds this project's repo from: GitHub, or
 * the Forgejo instance saved in Settings. Switching keeps build on push from
 * building anything by itself: the new provider's head is taken as seen.
 */
export default function GitProviderSelect({
  projectId,
  provider,
}: {
  projectId: string
  provider: GitProvider
}) {
  const queryClient = useQueryClient()
  const [error, setError] = useState<string | null>(null)
  const save = useMutation({
    mutationFn: (next: GitProvider) => updateGitProvider(projectId, next),
    onSuccess: () => {
      setError(null)
      queryClient.invalidateQueries({ queryKey: ['project', projectId] })
      queryClient.invalidateQueries({ queryKey: ['projects'] })
    },
    onError: (err: Error) => setError(err.message),
  })
  return (
    <div className="flex flex-wrap items-center gap-3 font-mono text-xs">
      <label className="flex items-center gap-2">
        <span className="whitespace-nowrap">builds from</span>
        <select
          value={provider}
          disabled={save.isPending}
          onChange={(e) => save.mutate(e.target.value as GitProvider)}
          className="select select-xs border-base-300 bg-base-100 font-mono text-xs"
        >
          <option value="github">github</option>
          <option value="forgejo">forgejo</option>
        </select>
      </label>
      {error && <span className="text-error">{error}</span>}
    </div>
  )
}
