import { useState } from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { ApiError, deployImage } from '../api/client'

/* Did this failure come from reading console.toml, rather than from the image
   ref? 503 (no GitHub connection) and 502 (GitHub unreachable) can only be
   that; a 400 is either, and only the config messages name the file. Getting
   this wrong just means the paste box does or does not open on its own. */
function isConfigFailure(err: unknown): boolean {
  if (!(err instanceof ApiError)) return false
  if (err.status === 503 || err.status === 502) return true
  return err.status === 400 && err.message.includes('console.toml')
}

/**
 * Deploy an image that is already in GHCR, without a build. This is the
 * way in for a project whose CI has never run or is broken: the artifact can be
 * built and pushed and still have no path to the server otherwise.
 *
 * Nothing is built here. console.toml is read from the repo at the given ref,
 * so the file in the repo stays its source of truth; pasting one is the
 * fallback for when GitHub itself cannot be reached, which is the failure that
 * prompted this in the first place.
 */
export default function DeployImageForm({
  projectId,
  imageHint,
  branch,
}: {
  projectId: string
  imageHint: string
  branch: string
}) {
  const queryClient = useQueryClient()
  const [open, setOpen] = useState(false)
  const [image, setImage] = useState(imageHint)
  const [ref, setRef] = useState(branch)
  const [toml, setToml] = useState('')
  const [pasting, setPasting] = useState(false)

  const deploy = useMutation({
    mutationFn: () =>
      deployImage(projectId, {
        image,
        ref,
        console_toml: pasting && toml.trim() ? toml : undefined,
      }),
    onSuccess: () => {
      close()
      queryClient.invalidateQueries({ queryKey: ['deployments', projectId] })
      queryClient.invalidateQueries({ queryKey: ['project', projectId] })
    },
    // Reading console.toml is the part most likely to fail (no connection,
    // GitHub down, no such file), so offer the paste box the moment it does.
    // A bad image ref has nothing to do with the config, and must not.
    onError: (err) => {
      if (isConfigFailure(err)) setPasting(true)
    },
  })

  function close() {
    setOpen(false)
    setPasting(false)
    setToml('')
    setImage(imageHint)
    setRef(branch)
  }

  if (!open) {
    return (
      <button
        type="button"
        onClick={() => setOpen(true)}
        className="self-start font-mono text-xs text-accent transition-colors duration-150 hover:underline"
      >
        deploy an image
      </button>
    )
  }

  const tagged = image.trim() !== imageHint && image.trim().length > 0

  return (
    <form
      className="flex max-w-2xl flex-col gap-3 border-l border-base-300 pl-3 font-mono text-xs"
      onSubmit={(e) => {
        e.preventDefault()
        deploy.mutate()
      }}
    >
      <p className="text-muted">
        deploy an image that is already in the registry. nothing is built here.
      </p>

      <label className="flex flex-col gap-1">
        <span className="text-muted">image</span>
        <input
          value={image}
          onChange={(e) => setImage(e.target.value)}
          placeholder={`${imageHint}abc1234`}
          spellCheck={false}
          autoFocus
          className="input input-sm w-full border-base-300 bg-base-100 font-mono text-sm"
        />
        <span className="text-faint">
          the tag is what shows in history, so a moving tag like :latest makes
          rolling back to it meaningless
        </span>
      </label>

      {!pasting && (
        <label className="flex flex-col gap-1">
          <span className="text-muted">ref</span>
          <input
            value={ref}
            onChange={(e) => setRef(e.target.value)}
            spellCheck={false}
            className="input input-sm w-full border-base-300 bg-base-100 font-mono text-sm"
          />
          <span className="text-faint">
            console.toml is read from the repo at this ref.{' '}
            <button
              type="button"
              className="text-accent hover:underline"
              onClick={() => setPasting(true)}
            >
              paste it instead
            </button>
          </span>
        </label>
      )}

      {pasting && (
        <label className="flex flex-col gap-1">
          <span className="text-muted">console.toml</span>
          <textarea
            value={toml}
            onChange={(e) => setToml(e.target.value)}
            rows={10}
            spellCheck={false}
            placeholder={'[app]\nname = "your-app"\nsubdomain = "your-app"\nport = 8080'}
            className="textarea textarea-sm w-full border-base-300 bg-base-100 font-mono text-xs"
          />
          <span className="text-faint">
            validated the same way a build's is.{' '}
            <button
              type="button"
              className="text-accent hover:underline"
              onClick={() => {
                setPasting(false)
                setToml('')
              }}
            >
              read it from the repo instead
            </button>
          </span>
        </label>
      )}

      {deploy.isError && (
        <p className="text-error">{(deploy.error as Error).message}</p>
      )}

      <div className="flex items-center gap-3">
        <button
          type="submit"
          disabled={deploy.isPending || !tagged || (pasting && !toml.trim())}
          className="btn btn-primary btn-sm font-mono"
        >
          {deploy.isPending ? 'queueing…' : 'deploy'}
        </button>
        <button
          type="button"
          onClick={close}
          className="text-muted transition-colors duration-150 hover:text-base-content hover:underline"
        >
          cancel
        </button>
      </div>
    </form>
  )
}
