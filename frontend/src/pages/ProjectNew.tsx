import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Link, useNavigate } from 'react-router-dom'
import {
  createProject,
  fetchDomains,
  fetchGitHubBranches,
  fetchGitHubRepos,
} from '../api/client'

const SUBDOMAIN_HINT = 'lowercase letters, digits, inner hyphens; max 32 chars'

/* A repo name is close enough to a subdomain to be a good guess, but not
   always legal as one: strip what the validator would reject rather than
   offering a value that fails on submit. */
function asSubdomain(repoName: string): string {
  return repoName
    .toLowerCase()
    .replace(/[^a-z0-9-]+/g, '-')
    .replace(/^-+|-+$/g, '')
    .slice(0, 32)
}

export default function ProjectNew() {
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const [form, setForm] = useState({
    name: '',
    repo: '',
    branch: 'main',
    subdomain: '',
    domain: '',
  })
  const [typingRepo, setTypingRepo] = useState(false)
  const [typingBranch, setTypingBranch] = useState(false)

  const { data: domainData } = useQuery({ queryKey: ['domains'], queryFn: fetchDomains })
  const domainList = domainData?.domains ?? []
  const domain = form.domain || domainList[0] || ''

  // 503 when no GitHub account is connected, which is not an error worth
  // showing: the repo field just stays free text.
  const { data: repoData } = useQuery({
    queryKey: ['github-repos'],
    queryFn: fetchGitHubRepos,
    retry: false,
  })
  const repos = repoData?.repos ?? []
  const picking = repos.length > 0 && !typingRepo

  // Branches of whichever repo is chosen. Only asked for once there is a repo
  // to ask about, and a repo typed by hand is not one we can list.
  const { data: branchData } = useQuery({
    queryKey: ['github-branches', form.repo],
    queryFn: () => fetchGitHubBranches(form.repo),
    enabled: picking && form.repo !== '',
    retry: false,
  })
  const branches = branchData?.branches ?? []
  // Also gated on `picking`: once the repo is being typed by hand, a cached
  // branch list belongs to a repo that is no longer the one being registered.
  const pickingBranch = picking && branches.length > 0 && !typingBranch

  const create = useMutation({
    mutationFn: () => createProject({ ...form, domain: domain || undefined }),
    onSuccess: (project) => {
      queryClient.invalidateQueries({ queryKey: ['projects'] })
      navigate(`/projects/${project.id}`)
    },
  })

  function set(field: keyof typeof form) {
    return (e: React.ChangeEvent<HTMLInputElement>) =>
      setForm({ ...form, [field]: e.target.value })
  }

  /* Picking a repo fills in what can be derived from it, but never overwrites
     something already typed. The branch matters most: a repo whose default is
     not "main" would otherwise be registered against a branch it never pushes,
     and its deploys would be silently ignored. */
  function pickRepo(fullName: string) {
    const repo = repos.find((r) => r.full_name === fullName)
    if (!repo) return
    const shortName = fullName.split('/')[1] ?? fullName
    setForm((f) => ({
      ...f,
      repo: fullName,
      branch: repo.default_branch,
      name: f.name || shortName,
      subdomain: f.subdomain || asSubdomain(shortName),
    }))
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
        {picking ? (
          <label className="flex flex-col gap-1">
            <span className="font-mono text-xs text-muted">repo</span>
            <select
              value={form.repo}
              onChange={(e) => pickRepo(e.target.value)}
              required
              className="select select-sm w-full border-base-300 bg-base-100 font-mono text-sm"
            >
              <option value="" disabled>
                pick a repo
              </option>
              {repos.map((r) => (
                <option key={r.full_name} value={r.full_name}>
                  {r.full_name}
                  {r.private ? ' (private)' : ''}
                </option>
              ))}
            </select>
            <span className="font-mono text-xs text-faint">
              from your connected github account.{' '}
              <button
                type="button"
                className="text-accent hover:underline"
                onClick={() => setTypingRepo(true)}
              >
                enter one manually
              </button>
            </span>
          </label>
        ) : (
          <Field
            label="repo"
            hint="owner/repo on github"
            value={form.repo}
            onChange={set('repo')}
            placeholder="owner/repo"
            required
          />
        )}
        {pickingBranch ? (
          <label className="flex flex-col gap-1">
            <span className="font-mono text-xs text-muted">branch</span>
            <select
              value={form.branch}
              onChange={(e) => setForm({ ...form, branch: e.target.value })}
              required
              className="select select-sm w-full border-base-300 bg-base-100 font-mono text-sm"
            >
              {/* The repo's default is preselected, but it stays listed among
                  the rest: a project can track any branch it likes. */}
              {branches.map((b) => (
                <option key={b} value={b}>
                  {b}
                </option>
              ))}
            </select>
            <span className="font-mono text-xs text-faint">
              deploys trigger on pushes to this branch.{' '}
              <button
                type="button"
                className="text-accent hover:underline"
                onClick={() => setTypingBranch(true)}
              >
                type one instead
              </button>
            </span>
          </label>
        ) : (
          <Field
            label="branch"
            hint="deploys trigger on pushes to this branch"
            value={form.branch}
            onChange={set('branch')}
            required
          />
        )}
        <Field
          label="subdomain"
          hint={SUBDOMAIN_HINT}
          value={form.subdomain}
          onChange={set('subdomain')}
          placeholder="your-app"
          required
        />
        {domainList.length > 1 && (
          <label className="flex flex-col gap-1">
            <span className="font-mono text-xs text-muted">domain</span>
            <select
              value={domain}
              onChange={(e) => setForm({ ...form, domain: e.target.value })}
              className="select select-sm w-full border-base-300 bg-base-100 font-mono text-sm"
            >
              {domainList.map((d) => (
                <option key={d} value={d}>
                  {d}
                </option>
              ))}
            </select>
            <span className="font-mono text-xs text-faint">
              serves at {form.subdomain || 'your-app'}.{domain}
            </span>
          </label>
        )}

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
