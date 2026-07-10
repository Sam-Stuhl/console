import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { Link, useNavigate, useParams } from 'react-router-dom'
import { deleteProject, fetchDeployments, fetchProject } from '../api/client'
import { localDay } from '../lib/format'
import SecretsSection from '../components/SecretsSection'
import DeploymentsSection from '../components/DeploymentsSection'
import SetupSection from '../components/SetupSection'
import ConfirmButton from '../components/ConfirmButton'

export default function ProjectDetail() {
  const { id } = useParams<{ id: string }>()
  const navigate = useNavigate()
  const queryClient = useQueryClient()

  const { data: project, isError } = useQuery({
    queryKey: ['project', id],
    queryFn: () => fetchProject(id!),
    enabled: Boolean(id),
  })

  // Shares the cache with DeploymentsSection's polling query, so the setup
  // section disappears on its own when the first deploy lands
  const { data: deployments } = useQuery({
    queryKey: ['deployments', id],
    queryFn: () => fetchDeployments(id!),
    enabled: Boolean(id),
  })
  const needsSetup = deployments !== undefined && deployments.length === 0

  const remove = useMutation({
    mutationFn: () => deleteProject(id!),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['projects'] })
      navigate('/')
    },
  })

  if (isError) {
    return (
      <div className="rounded-box border border-error/40 px-4 py-3 font-mono text-sm">
        <span className="text-error">project not found </span>
        <Link to="/" className="text-accent hover:underline">
          back to projects
        </Link>
      </div>
    )
  }

  return (
    <div className="flex flex-col gap-8">
      <div className="flex flex-col gap-5">
        <Link to="/" className="self-start font-mono text-xs text-muted hover:text-base-content">
          &larr; projects
        </Link>
        {project ? (
          <>
            <h1 className="font-mono text-xl font-semibold">{project.name}</h1>
            <dl className="grid grid-cols-[auto_1fr] gap-x-6 gap-y-1.5 text-sm sm:grid-cols-[auto_1fr_auto_1fr]">
              <dt className="font-mono text-xs leading-6 text-muted">repo</dt>
              <dd className="truncate font-mono text-xs leading-6">
                <a
                  href={`https://github.com/${project.repo}`}
                  target="_blank"
                  rel="noreferrer"
                  className="hover:text-primary"
                >
                  {project.repo}
                </a>
              </dd>
              <dt className="font-mono text-xs leading-6 text-muted">subdomain</dt>
              <dd className="truncate font-mono text-xs leading-6">{project.subdomain}</dd>
              <dt className="font-mono text-xs leading-6 text-muted">branch</dt>
              <dd className="truncate font-mono text-xs leading-6">{project.branch}</dd>
              <dt className="font-mono text-xs leading-6 text-muted">created</dt>
              <dd className="truncate font-mono text-xs leading-6">
                {localDay(project.created_at)}
              </dd>
            </dl>
          </>
        ) : (
          <div className="flex flex-col gap-3">
            <span className="skeleton h-6 w-64" />
            <span className="skeleton h-16 w-full max-w-2xl" />
          </div>
        )}
      </div>

      {needsSetup && id && (
        <Section title="next steps">
          <SetupSection projectId={id} branch={project?.branch ?? 'main'} />
        </Section>
      )}

      {!needsSetup && (
        <Section title="deployments">
          {id && (
            <DeploymentsSection projectId={id} branch={project?.branch ?? 'main'} />
          )}
        </Section>
      )}

      <Section title="secrets">{id && <SecretsSection projectId={id} />}</Section>

      <Section title="danger">
        <div className="flex items-center gap-4">
          <ConfirmButton
            label="delete project"
            confirmLabel="really delete?"
            onConfirm={() => remove.mutate()}
          />
          <span className="font-mono text-xs text-faint">
            removes the registration and its secrets. running containers are untouched.
          </span>
        </div>
        {remove.isError && (
          <p className="font-mono text-xs text-error">{(remove.error as Error).message}</p>
        )}
      </Section>
    </div>
  )
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="flex flex-col gap-3 border-t border-base-300 pt-4">
      <h2 className="font-mono text-xs text-muted">{title}</h2>
      {children}
    </section>
  )
}
