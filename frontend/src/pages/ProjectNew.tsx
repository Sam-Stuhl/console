import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Link, useNavigate } from 'react-router-dom'
import Combobox from '../components/Combobox'
import { since } from '../lib/format'
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
  const { data: branchData, isFetching: branchesLoading } = useQuery({
    queryKey: ['github-branches', form.repo],
    queryFn: () => fetchGitHubBranches(form.repo),
    enabled: picking && form.repo !== '',
    retry: false,
  })
  const branches = branchData?.branches ?? []
  const defaultBranch = repos.find((r) => r.full_name === form.repo)?.default_branch
  // Also gated on `picking`: once the repo is being typed by hand, a cached
  // branch list belongs to a repo that is no longer the one being registered.
  // Loading counts as picking, so the field does not flash a text input and
  // then swap under the cursor; the picker shows its skeleton instead. A repo
  // whose branches fail to load still falls back to typing.
  const pickingBranch =
    picking && form.repo !== '' && (branches.length > 0 || branchesLoading) && !typingBranch

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
          <Combobox
            label="repo"
            value={form.repo}
            items={repos.map((r) => ({
              value: r.full_name,
              label: r.full_name,
              tag: r.private ? 'private' : undefined,
              detail: r.pushed_at ? since(r.pushed_at) : undefined,
            }))}
            onSelect={pickRepo}
            placeholder="search your repos"
            required
            hint={
              <>
                from your connected github account, most recently pushed first.{' '}
                <button
                  type="button"
                  className="text-accent hover:underline"
                  onClick={() => setTypingRepo(true)}
                >
                  enter one manually
                </button>
              </>
            }
            emptyText="no repo matches that"
          />
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
          <Combobox
            label="branch"
            value={form.branch}
            items={branches.map((b) => ({
              value: b,
              label: b,
              // The repo's default is preselected but stays in the list: a
              // project can track any branch it likes, and saying which one is
              // the default is what makes an unfamiliar name safe to pick.
              tag: b === defaultBranch ? 'default' : undefined,
            }))}
            onSelect={(branch) => setForm((f) => ({ ...f, branch }))}
            placeholder="search branches"
            loading={branchesLoading}
            required
            hint={
              <>
                deploys trigger on pushes to this branch.{' '}
                <button
                  type="button"
                  className="text-accent hover:underline"
                  onClick={() => setTypingBranch(true)}
                >
                  type one instead
                </button>
              </>
            }
            emptyText="no branch matches that"
          />
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
