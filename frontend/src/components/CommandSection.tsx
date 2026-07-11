import { useEffect, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  fetchCommandRun,
  fetchCommandRuns,
  runCommand,
  type CommandRunSummary,
} from '../api/client'
import { since } from '../lib/format'
import CommandBadge from './CommandBadge'

const TERMINAL = new Set(['succeeded', 'failed'])

export default function CommandSection({
  projectId,
  isLive,
}: {
  projectId: string
  isLive: boolean
}) {
  const queryClient = useQueryClient()
  const [command, setCommand] = useState('')
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [actionError, setActionError] = useState<string | null>(null)

  const { data: runs } = useQuery({
    queryKey: ['commands', projectId],
    queryFn: () => fetchCommandRuns(projectId),
    refetchInterval: 5000,
  })

  // Default the output pane to the most recent run.
  useEffect(() => {
    if (selectedId === null && runs && runs.length > 0) setSelectedId(runs[0].id)
  }, [runs, selectedId])

  const run = useMutation({
    mutationFn: (cmd: string) => runCommand(projectId, cmd),
    onSuccess: (data) => {
      setActionError(null)
      setCommand('')
      setSelectedId(data.run_id)
      queryClient.invalidateQueries({ queryKey: ['commands', projectId] })
    },
    onError: (err: Error) => setActionError(err.message),
  })

  function submit() {
    const cmd = command.trim()
    if (cmd) run.mutate(cmd)
  }

  if (!isLive) {
    return (
      <p className="font-mono text-xs text-faint">
        deploy the app to run commands. commands exec inside the running container.
      </p>
    )
  }

  return (
    <div className="flex flex-col gap-4">
      <div className="flex max-w-3xl items-center gap-2">
        <span className="font-mono text-xs text-muted">$</span>
        <input
          value={command}
          onChange={(e) => setCommand(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter') submit()
          }}
          placeholder="a shell command, e.g. ls -la"
          spellCheck={false}
          className="flex-1 rounded-field border border-base-300 bg-base-100 px-3 py-1.5 font-mono text-xs outline-none focus:border-primary/50"
        />
        <button
          type="button"
          disabled={run.isPending || !command.trim()}
          onClick={submit}
          className="rounded-field border border-base-300 px-3 py-1.5 font-mono text-xs text-muted transition-colors duration-150 hover:border-primary/50 hover:text-primary disabled:opacity-40"
        >
          {run.isPending ? 'running…' : 'run'}
        </button>
      </div>
      <p className="font-mono text-xs text-faint">
        one-shot and non-interactive. to answer a prompt or poke around, {' '}
        <Link
          to={`/projects/${projectId}/terminal`}
          className="text-accent hover:underline"
        >
          open a terminal &#8599;
        </Link>
      </p>
      {actionError && <p className="font-mono text-xs text-error">{actionError}</p>}

      {selectedId && <RunOutput projectId={projectId} runId={selectedId} />}

      {runs && runs.length > 0 && (
        <table className="w-full max-w-3xl font-mono text-xs">
          <tbody>
            {runs.map((r) => (
              <RunRow
                key={r.id}
                run={r}
                selected={r.id === selectedId}
                onSelect={() => setSelectedId(r.id)}
              />
            ))}
          </tbody>
        </table>
      )}
    </div>
  )
}

function RunRow({
  run,
  selected,
  onSelect,
}: {
  run: CommandRunSummary
  selected: boolean
  onSelect: () => void
}) {
  return (
    <tr
      onClick={onSelect}
      className={`cursor-pointer border-b border-base-300/40 last:border-none ${
        selected ? 'text-base-content' : 'text-muted hover:text-base-content'
      }`}
    >
      <td className="w-40 py-2 pr-4">
        <CommandBadge status={run.status} exitCode={run.exit_code} />
      </td>
      <td className="max-w-0 truncate py-2 pr-4" title={run.command}>
        {run.command}
      </td>
      <td className="w-20 whitespace-nowrap py-2 text-right text-muted">
        {since(run.created_at)}
      </td>
    </tr>
  )
}

function RunOutput({ projectId, runId }: { projectId: string; runId: string }) {
  const { data: run } = useQuery({
    queryKey: ['command', runId],
    queryFn: () => fetchCommandRun(projectId, runId),
    // Poll fast while running, stop once the run settles.
    refetchInterval: (query) =>
      query.state.data && TERMINAL.has(query.state.data.status) ? false : 2000,
  })

  const pre = useRef<HTMLPreElement>(null)
  const stickToBottom = useRef(true)
  useEffect(() => {
    const el = pre.current
    if (el && stickToBottom.current) el.scrollTop = el.scrollHeight
  }, [run?.output])

  function onScroll() {
    const el = pre.current
    if (!el) return
    stickToBottom.current = el.scrollTop + el.clientHeight >= el.scrollHeight - 16
  }

  if (!run) return <div className="skeleton h-40 w-full max-w-3xl rounded-box" />

  return (
    <div className="flex max-w-3xl flex-col gap-2">
      <div className="flex items-center gap-3">
        <CommandBadge status={run.status} exitCode={run.exit_code} />
        <code className="truncate font-mono text-xs text-muted" title={run.command}>
          {run.command}
        </code>
      </div>
      {run.failure_reason && run.status === 'failed' && run.exit_code === null && (
        <p className="rounded-box border border-error/40 px-3 py-2 font-mono text-xs text-error">
          {run.failure_reason}
        </p>
      )}
      <pre
        ref={pre}
        onScroll={onScroll}
        className="max-h-96 overflow-auto rounded-box border border-base-300/60 bg-neutral p-4 font-mono text-xs leading-relaxed text-neutral-content"
      >
        {run.output || (
          <span className="text-faint">
            {run.status === 'running' ? '(waiting for output…)' : '(no output)'}
          </span>
        )}
      </pre>
    </div>
  )
}
