import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { fetchStarters } from '../api/client'
import { copyText } from '../lib/clipboard'

/* Commented starter files for the app repo, prefilled with this project's
   name and subdomain. Copy or download, drop them in the repo root. */
export default function StartersSection({ projectId }: { projectId: string }) {
  const { data: starters } = useQuery({
    queryKey: ['starters', projectId],
    queryFn: () => fetchStarters(projectId),
  })
  const [copiedFile, setCopiedFile] = useState<string | null>(null)

  if (!starters) {
    return <span className="skeleton h-8 w-full max-w-md" />
  }

  const files = [
    {
      name: 'console.toml',
      content: starters.console_toml,
      hint: 'prefilled for this project; set the port and add your secrets',
    },
    {
      name: 'Dockerfile',
      content: starters.dockerfile,
      hint: 'python example; swap the base image and commands for your stack',
    },
  ]

  async function copy(name: string, content: string) {
    await copyText(content)
    setCopiedFile(name)
    window.setTimeout(() => setCopiedFile(null), 2000)
  }

  function download(name: string, content: string) {
    const url = URL.createObjectURL(new Blob([content], { type: 'text/plain' }))
    const a = document.createElement('a')
    a.href = url
    a.download = name
    a.click()
    URL.revokeObjectURL(url)
  }

  return (
    <div className="flex max-w-3xl flex-col gap-1">
      <p className="pb-2 font-mono text-xs text-faint">
        commented templates for the app repo root, filled in with this
        project&apos;s details.
      </p>
      <table className="w-full font-mono text-xs">
        <tbody>
          {files.map((f) => (
            <tr key={f.name} className="border-b border-base-300/40 last:border-none">
              <td className="w-40 py-2 pr-4">{f.name}</td>
              <td className="py-2 pr-4 text-muted">{f.hint}</td>
              <td className="w-36 py-2 text-right whitespace-nowrap">
                <span className="inline-flex items-center gap-3">
                  {copiedFile === f.name ? (
                    <span className="text-success">copied</span>
                  ) : (
                    <button
                      type="button"
                      className="text-muted transition-colors duration-150 hover:text-base-content hover:underline"
                      onClick={() => copy(f.name, f.content)}
                    >
                      copy
                    </button>
                  )}
                  <button
                    type="button"
                    className="text-muted transition-colors duration-150 hover:text-base-content hover:underline"
                    onClick={() => download(f.name, f.content)}
                  >
                    download
                  </button>
                </span>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}
