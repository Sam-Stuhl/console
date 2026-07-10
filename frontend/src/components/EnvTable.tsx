import type { EnvVar } from '../api/client'

export default function EnvTable({ env }: { env: EnvVar[] }) {
  if (env.length === 0) {
    return <p className="font-mono text-sm text-muted">no environment variables</p>
  }
  return (
    <div className="overflow-x-auto">
      <table className="w-full font-mono text-xs">
        <tbody>
          {env.map((e) => (
            <tr key={e.key} className="border-b border-base-300/40 last:border-none">
              <td className="w-1/3 py-1.5 pr-6">{e.key}</td>
              <td className="py-1.5 text-faint">{e.value}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}
