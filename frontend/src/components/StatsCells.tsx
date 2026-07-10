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
        <td className="text-base-content/30">–</td>
        <td className="text-base-content/30">–</td>
      </>
    )
  }
  if (!data) {
    return (
      <>
        <td><span className="loading loading-dots loading-xs" /></td>
        <td><span className="loading loading-dots loading-xs" /></td>
      </>
    )
  }
  return (
    <>
      <td className="tabular-nums">{data.cpu_percent.toFixed(1)}%</td>
      <td className="tabular-nums">
        {formatBytes(data.mem_usage)}
        <span className="text-base-content/50"> / {formatBytes(data.mem_limit)}</span>
      </td>
    </>
  )
}
