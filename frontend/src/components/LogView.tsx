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

  return (
    <pre
      ref={pre}
      onScroll={onScroll}
      className="max-h-96 overflow-auto rounded-box bg-neutral p-4 text-xs leading-relaxed text-neutral-content"
    >
      {logs === undefined ? 'Loading logs…' : logs || '(no output)'}
    </pre>
  )
}
