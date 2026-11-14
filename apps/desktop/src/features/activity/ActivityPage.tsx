import React, { useMemo, useState } from 'react';
import { Link } from 'react-router-dom';
import { AlertCircle, Ban, CheckCircle2, Clock, Loader2, RotateCcw, Search, XCircle } from 'lucide-react';
import { useAppData } from '../../app/AppDataContext';
import type { DemoJob, JobStatus } from '../../types';
import { PageLayout, formatDate } from '../../components/projects/ProjectBits';
import { Badge, Button, Card, Segmented, cx } from '../../components/ui/primitives';
import { EmptyState, ErrorState, LoadingState } from '../../components/ui/StateViews';

export const JOB_STATUS_META: Record<JobStatus, { label: string; tone: 'neutral' | 'accent' | 'success' | 'danger' | 'warning'; icon: React.ReactNode }> = {
  queued: { label: 'Queued', tone: 'neutral', icon: <Clock aria-hidden className="w-3 h-3" /> },
  running: { label: 'Running', tone: 'accent', icon: <Loader2 aria-hidden className="w-3 h-3 animate-spin" /> },
  completed: { label: 'Completed', tone: 'success', icon: <CheckCircle2 aria-hidden className="w-3 h-3" /> },
  failed: { label: 'Failed', tone: 'danger', icon: <XCircle aria-hidden className="w-3 h-3" /> },
  canceled: { label: 'Canceled', tone: 'warning', icon: <Ban aria-hidden className="w-3 h-3" /> },
};

const KIND_LABEL: Record<DemoJob['kind'], string> = {
  analyze: 'Asset analysis',
  preview: 'Preview render',
  export: 'Export',
  qa: 'QA checks',
};

export const JobStatusBadge: React.FC<{ status: JobStatus }> = ({ status }) => (
  <Badge tone={JOB_STATUS_META[status].tone} icon={JOB_STATUS_META[status].icon}>
    {JOB_STATUS_META[status].label}
  </Badge>
);

/** Compact feed for the dashboard / project overview. */
export const RecentActivityFeed: React.FC<{ limit?: number; projectId?: string }> = ({ limit = 5, projectId }) => {
  const { jobs, projects } = useAppData();
  const list = jobs.filter((j) => !projectId || j.projectId === projectId).slice(0, limit);
  if (list.length === 0) return <EmptyState compact title="No activity yet" message="Simulation jobs will appear here." />;
  return (
    <ul className="flex flex-col gap-2">
      {list.map((j) => (
        <li key={j.id} className="p-3 rounded-lg border border-line bg-surface">
          <div className="flex items-center justify-between gap-2">
            <span className="text-[13px] font-medium text-fg">{KIND_LABEL[j.kind]}</span>
            <JobStatusBadge status={j.status} />
          </div>
          <p className="text-xs text-fg-2 mt-1 line-clamp-2">{j.message}</p>
          <p className="text-[11px] text-fg-3 mt-1 truncate">
            {projects.find((p) => p.id === j.projectId)?.name ?? j.projectId} · Simulation
          </p>
        </li>
      ))}
    </ul>
  );
};

export const ActivityPage: React.FC = () => {
  const { jobs, projects, status, error, refresh, retryJob, cancelJob } = useAppData();
  const [filter, setFilter] = useState<'all' | JobStatus>('all');
  const [q, setQ] = useState('');

  const visible = useMemo(() => {
    const s = q.trim().toLowerCase();
    return jobs.filter((j) => {
      const project = projects.find((p) => p.id === j.projectId)?.name ?? j.projectId;
      return (filter === 'all' || j.status === filter) && (!s || `${j.message} ${KIND_LABEL[j.kind]} ${project}`.toLowerCase().includes(s));
    });
  }, [jobs, projects, filter, q]);

  return (
    <PageLayout
      title="Activity"
      eyebrow={<Badge tone="warning">All jobs are simulations</Badge>}
      description="Analysis, preview, QA and export jobs. In this prototype every job is scripted — nothing is analysed, rendered or written."
    >
      <div className="flex flex-wrap items-center gap-3 mb-4">
        <div className="relative w-full sm:w-72">
          <Search aria-hidden className="w-3.5 h-3.5 absolute left-2.5 top-2.5 text-fg-3" />
          <input
            type="search"
            aria-label="Search jobs"
            placeholder="Search jobs"
            value={q}
            onChange={(e) => setQ(e.target.value)}
            className="w-full h-8 pl-8 pr-2 rounded-md bg-app border border-line text-[13px] text-fg placeholder:text-fg-3 focus:outline-none focus:border-accent"
          />
        </div>
        <Segmented
          size="sm"
          label="Filter by status"
          value={filter}
          onChange={setFilter}
          options={[
            { value: 'all', label: 'All' },
            { value: 'running', label: 'Running' },
            { value: 'queued', label: 'Queued' },
            { value: 'completed', label: 'Completed' },
            { value: 'failed', label: 'Failed' },
            { value: 'canceled', label: 'Canceled' },
          ]}
        />
      </div>

      {status === 'loading' && <LoadingState />}
      {status === 'error' && <ErrorState message={error} onRetry={refresh} />}
      {status === 'ready' &&
        (jobs.length === 0 ? (
          <EmptyState title="No jobs yet" message="Request a simulated preview or export from the editor to see jobs here." />
        ) : visible.length === 0 ? (
          <EmptyState
            title="No matching jobs"
            action={
              <Button size="sm" onClick={() => { setFilter('all'); setQ(''); }}>
                Clear filters
              </Button>
            }
          />
        ) : (
          <ul className="flex flex-col gap-2.5" aria-label="Jobs">
            {visible.map((j) => {
              const project = projects.find((p) => p.id === j.projectId);
              return (
                <li key={j.id}>
                  <Card className="p-4" data-testid="job-card">
                    <div className="flex flex-wrap items-start justify-between gap-3">
                      <div className="min-w-0">
                        <div className="flex items-center gap-2 flex-wrap">
                          <span className="text-[14px] font-semibold text-fg">{KIND_LABEL[j.kind]}</span>
                          <JobStatusBadge status={j.status} />
                          <Badge>Simulation</Badge>
                        </div>
                        <p className="text-[13px] text-fg-2 mt-1">{j.message}</p>
                        <p className="text-xs text-fg-3 mt-1">
                          {project ? (
                            <Link to={`/projects/${project.id}`} className="hover:underline">
                              {project.name}
                            </Link>
                          ) : (
                            <span>{j.projectId} (archived)</span>
                          )}{' '}
                          · started {formatDate(j.createdAtIso)}
                        </p>
                      </div>
                      <div className="flex gap-2 shrink-0">
                        {(j.status === 'failed' || j.status === 'canceled') && (
                          <Button size="sm" icon={<RotateCcw className="w-3.5 h-3.5" />} onClick={() => retryJob(j.id)}>
                            Retry simulation
                          </Button>
                        )}
                        {(j.status === 'running' || j.status === 'queued') && (
                          <Button size="sm" variant="danger" onClick={() => cancelJob(j.id)}>
                            Cancel simulation
                          </Button>
                        )}
                        {project && (
                          <Link to={`/projects/${project.id}`} className="h-7 px-2.5 text-xs rounded-md inline-flex items-center text-fg-2 hover:text-fg hover:bg-elevated">
                            View project
                          </Link>
                        )}
                      </div>
                    </div>
                    {(j.status === 'running' || j.status === 'queued' || j.status === 'failed' || j.status === 'canceled') && (
                      <div className="mt-3">
                        <div
                          role="progressbar"
                          aria-label={`${KIND_LABEL[j.kind]} simulated progress`}
                          aria-valuemin={0}
                          aria-valuemax={100}
                          aria-valuenow={j.progressPercent}
                          className="h-1.5 rounded-full bg-elevated overflow-hidden"
                        >
                          <div
                            className={cx('h-full rounded-full transition-[width]', j.status === 'failed' ? 'bg-danger' : j.status === 'canceled' ? 'bg-warning' : 'bg-accent')}
                            style={{ width: `${j.progressPercent}%` }}
                          />
                        </div>
                        <p className="text-[11px] text-fg-3 mt-1 font-mono">{j.progressPercent}% (simulated)</p>
                      </div>
                    )}
                    {j.errorDetail && (
                      <p className="mt-2 text-xs text-danger flex items-start gap-1.5">
                        <AlertCircle aria-hidden className="w-3.5 h-3.5 mt-px shrink-0" /> {j.errorDetail}
                      </p>
                    )}
                  </Card>
                </li>
              );
            })}
          </ul>
        ))}
    </PageLayout>
  );
};
