import { useEffect, useRef } from 'react'
import { useQuery } from '@tanstack/react-query'
import { fetchLogs } from '../api/client'

export default function LogView({ id }: { id: string }) {
  const { data: logs } = useQuery({
    queryKey: ['logs', id],
    queryFn: () => fetchLogs(id),
    refetchInterval: 3000,
  })

  const pre = useRef<HTMLPreElement>(null)
  const stickToBottom = useRef(true)

  useEffect(() => {
    const el = pre.current
    if (el && stickToBottom.current) el.scrollTop = el.scrollHeight
  }, [logs])

  function onScroll() {
    const el = pre.current
    if (!el) return
    stickToBottom.current = el.scrollTop + el.clientHeight >= el.scrollHeight - 16
  }

  if (logs === undefined) {
    return <div className="skeleton h-40 w-full rounded-box" />
  }
  return (
    <pre
      ref={pre}
      onScroll={onScroll}
      className="max-h-96 overflow-auto rounded-box border border-base-300/60 bg-neutral p-4 font-mono text-xs leading-relaxed text-neutral-content"
    >
      {logs || <span className="text-faint">(no output)</span>}
    </pre>
  )
}
