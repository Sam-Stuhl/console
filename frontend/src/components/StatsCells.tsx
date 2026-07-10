import { useQuery } from '@tanstack/react-query'
import { fetchStats } from '../api/client'
import { formatBytes } from '../lib/format'

export default function StatsCells({ id, running }: { id: string; running: boolean }) {
  const { data } = useQuery({
    queryKey: ['stats', id],
    queryFn: () => fetchStats(id),
    refetchInterval: 5000,
    enabled: running,
  })

  if (!running) {
    return (
      <>
        <td className="text-right font-mono text-xs text-faint">--</td>
        <td className="text-right font-mono text-xs text-faint">--</td>
      </>
    )
  }
  if (!data) {
    return (
      <>
        <td className="text-right">
          <span className="skeleton inline-block h-3 w-10" />
        </td>
        <td className="text-right">
          <span className="skeleton inline-block h-3 w-20" />
        </td>
      </>
    )
  }
  return (
    <>
      <td className="text-right font-mono text-xs tabular-nums">
        {data.cpu_percent.toFixed(1)}%
      </td>
      <td className="text-right font-mono text-xs tabular-nums">
        {formatBytes(data.mem_usage)}
        <span className="text-faint"> / {formatBytes(data.mem_limit)}</span>
      </td>
    </>
  )
}
