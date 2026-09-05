import { useMutation, useQueryClient } from '@tanstack/react-query'
import { requestBuild } from '../api/client'

/**
 * Build the tracked branch on the server and deploy what comes out. The
 * console does this on its own when the branch moves; the button is for a
 * retry, or for a branch head that was pushed before the project was set to
 * build on push.
 */
export default function BuildNowButton({
  projectId,
  branch,
}: {
  projectId: string
  branch: string
}) {
  const queryClient = useQueryClient()
  const build = useMutation({
    mutationFn: () => requestBuild(projectId, branch),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['deployments', projectId] })
      queryClient.invalidateQueries({ queryKey: ['project', projectId] })
    },
  })
  return (
    <div className="flex flex-wrap items-center gap-3 font-mono text-xs">
      <button
        type="button"
        disabled={build.isPending}
        onClick={() => build.mutate()}
        className="text-accent transition-colors duration-150 hover:underline disabled:opacity-40"
      >
        {build.isPending ? 'starting…' : `build ${branch} now`}
      </button>
      <span className="text-faint">builds the branch head on the server and deploys it</span>
      {build.isError && (
        <span className="text-error">{(build.error as Error).message}</span>
      )}
    </div>
  )
}
