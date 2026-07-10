import { useState } from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { Link, useNavigate } from 'react-router-dom'
import { createProject } from '../api/client'

const SUBDOMAIN_HINT = 'lowercase letters, digits, inner hyphens; max 32 chars'

export default function ProjectNew() {
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const [form, setForm] = useState({ name: '', repo: '', branch: 'main', subdomain: '' })

  const create = useMutation({
    mutationFn: () => createProject(form),
    onSuccess: (project) => {
      queryClient.invalidateQueries({ queryKey: ['projects'] })
      navigate(`/projects/${project.id}`)
    },
  })

  function set(field: keyof typeof form) {
    return (e: React.ChangeEvent<HTMLInputElement>) =>
      setForm({ ...form, [field]: e.target.value })
  }

  return (
    <div className="flex max-w-xl flex-col gap-6">
      <Link to="/" className="self-start font-mono text-xs text-muted hover:text-base-content">
        &larr; projects
      </Link>
      <h1 className="text-base font-semibold">New project</h1>
      <form
        className="flex flex-col gap-4"
        onSubmit={(e) => {
          e.preventDefault()
          create.mutate()
        }}
      >
        <Field
          label="name"
          hint="display name, usually the repo name"
          value={form.name}
          onChange={set('name')}
          placeholder="your-app"
          required
        />
        <Field
          label="repo"
          hint="github repo under sam-stuhl"
          value={form.repo}
          onChange={set('repo')}
          placeholder="Sam-Stuhl/your-repo"
          required
        />
        <Field
          label="branch"
          hint="deploys trigger on pushes to this branch"
          value={form.branch}
          onChange={set('branch')}
          required
        />
        <Field
          label="subdomain"
          hint={SUBDOMAIN_HINT}
          value={form.subdomain}
          onChange={set('subdomain')}
          placeholder="your-app"
          required
        />

        {create.isError && (
          <p className="font-mono text-xs text-error">{(create.error as Error).message}</p>
        )}

        <div className="flex items-center gap-3">
          <button
            type="submit"
            disabled={create.isPending}
            className="btn btn-primary btn-sm font-mono"
          >
            {create.isPending ? 'creating' : 'create project'}
          </button>
          <Link to="/" className="font-mono text-xs text-muted hover:text-base-content">
            cancel
          </Link>
        </div>
      </form>
    </div>
  )
}

function Field({
  label,
  hint,
  ...input
}: { label: string; hint: string } & React.InputHTMLAttributes<HTMLInputElement>) {
  return (
    <label className="flex flex-col gap-1">
      <span className="font-mono text-xs text-muted">{label}</span>
      <input
        {...input}
        className="input input-sm w-full border-base-300 bg-base-100 font-mono text-sm"
      />
      <span className="font-mono text-xs text-faint">{hint}</span>
    </label>
  )
}
