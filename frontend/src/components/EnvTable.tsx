import type { EnvVar } from '../api/client'

export default function EnvTable({ env }: { env: EnvVar[] }) {
  if (env.length === 0) {
    return <p className="text-sm text-base-content/50">No environment variables.</p>
  }
  return (
    <table className="table table-sm">
      <thead>
        <tr>
          <th>Key</th>
          <th>Value</th>
        </tr>
      </thead>
      <tbody>
        {env.map((e) => (
          <tr key={e.key}>
            <td className="font-mono">{e.key}</td>
            <td className="font-mono text-base-content/50">{e.value}</td>
          </tr>
        ))}
      </tbody>
    </table>
  )
}
