import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { fetchStarters } from '../api/client'
import { copyText } from '../lib/clipboard'

/* First-deploy checklist with prefilled starter files. Rendered only while
   the project has no deployments; disappears once the first one lands. */
export default function SetupSection({
  projectId,
  branch,
}: {
  projectId: string
  branch: string
}) {
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
      file: 'console.toml',
      content: starters.console_toml,
      hint: 'prefilled for this project; set the port, declare your secrets',
    },
    {
      name: 'Dockerfile',
      file: 'Dockerfile',
      content: starters.dockerfile,
      hint: 'python example; swap the base image and commands for your stack',
    },
    {
      name: '.github/workflows/deploy.yml',
      file: 'deploy.yml',
      content: starters.workflow,
      hint: 'thin caller for the shared build workflow; no edits needed',
    },
  ]

  async function copy(name: string, content: string) {
    await copyText(content)
    setCopiedFile(name)
    window.setTimeout(() => setCopiedFile(null), 2000)
  }

  function download(file: string, content: string) {
    const url = URL.createObjectURL(new Blob([content], { type: 'text/plain' }))
    const a = document.createElement('a')
    a.href = url
    a.download = file
    a.click()
    URL.revokeObjectURL(url)
  }

  return (
    <div className="flex max-w-3xl flex-col gap-3 font-mono text-xs">
      <p className="text-muted">
        no deployments yet. to get the first one out the door:
      </p>

      <ol className="flex list-none flex-col gap-3">
        <li className="flex flex-col gap-1">
          <span>
            <span className="text-faint">1.</span> add these files to the repo
            (console.toml and Dockerfile at the root), commented inside:
          </span>
          <table className="w-full">
            <tbody>
              {files.map((f) => (
                <tr
                  key={f.name}
                  className="border-b border-base-300/40 last:border-none"
                >
                  <td className="w-36 py-2 pr-4">{f.name}</td>
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
                        onClick={() => download(f.file, f.content)}
                      >
                        download
                      </button>
                    </span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </li>
        <li>
          <span className="text-faint">2.</span> add the values for any secrets
          you declared in console.toml to the secrets section below
        </li>
        <li>
          <span className="text-faint">3.</span> push to {branch}; the build
          workflow notifies the console and the deploy shows up here
        </li>
        <li className="text-muted">
          already have an image in the registry? you do not need any of this to
          deploy it: use "deploy an image" under deployments below.
        </li>
      </ol>
    </div>
  )
}
