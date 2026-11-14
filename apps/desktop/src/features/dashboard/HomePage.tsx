import React, { useMemo, useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { Activity, ArrowRight, FolderOpen, Plus, Search } from 'lucide-react';
import { useAppData } from '../../app/AppDataContext';
import type { ProjectStatus } from '../../types';
import {
  PageLayout,
  PLATFORM_LABEL,
  ProjectStatusBadge,
  ProjectThumb,
  aspectLabel,
  durationLabel,
  formatDate,
} from '../../components/projects/ProjectBits';
import { Badge, Button, Card, SectionHeading, Segmented, buttonClass } from '../../components/ui/primitives';
import { EmptyState, ErrorState, LoadingState } from '../../components/ui/StateViews';
import { RecentActivityFeed } from '../activity/ActivityPage';
import { OPEN_PALETTE_EVENT } from '../../components/common/CommandPalette';

export const HomePage: React.FC = () => {
  const { projects, jobs, status, error, refresh } = useAppData();
  const navigate = useNavigate();
  const [filter, setFilter] = useState<'all' | ProjectStatus>('all');

  const sorted = useMemo(() => [...projects].sort((a, b) => b.updatedAtIso.localeCompare(a.updatedAtIso)), [projects]);
  const recent = sorted.filter((p) => filter === 'all' || p.status === filter).slice(0, 5);
  const featured = sorted.find((p) => p.activeRevisionId) ?? sorted[0];

  const stats = [
    { label: 'Projects', value: projects.length },
    { label: 'Drafts awaiting review', value: projects.filter((p) => p.status === 'review').length },
    { label: 'Demo jobs completed', value: jobs.filter((j) => j.status === 'completed').length },
  ];

  return (
    <PageLayout
      title="Home"
      eyebrow={<Badge tone="warning">Sample data</Badge>}
      description="Pick up where you left off, or start a new AI-assisted edit from your own footage."
      actions={
        <>
          <Button icon={<Search className="w-3.5 h-3.5" />} onClick={() => window.dispatchEvent(new Event(OPEN_PALETTE_EVENT))}>
            Search <span className="text-fg-3 font-mono text-[11px] ml-1">Ctrl K</span>
          </Button>
          <Button variant="primary" icon={<Plus className="w-4 h-4" />} onClick={() => navigate('/projects/new')}>
            New Project
          </Button>
        </>
      }
    >
      {status === 'loading' && <LoadingState rows={4} />}
      {status === 'error' && <ErrorState message={error} onRetry={refresh} />}
      {status === 'ready' && (
        <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
          <div className="lg:col-span-2 flex flex-col gap-6">
            {featured ? (
              <Card className="overflow-hidden flex flex-col sm:flex-row">
                <ProjectThumb project={featured} className="sm:w-44 h-40 sm:h-auto shrink-0" />
                <div className="p-5 flex-1 min-w-0 flex flex-col">
                  <span className="text-xs font-medium text-accent">Continue editing</span>
                  <h2 className="text-lg font-semibold text-fg mt-1 truncate">{featured.name}</h2>
                  <p className="text-[13px] text-fg-2 mt-1 line-clamp-2">{featured.description}</p>
                  <div className="flex flex-wrap items-center gap-2 mt-3 text-xs text-fg-3">
                    <ProjectStatusBadge status={featured.status} />
                    <span>{aspectLabel(featured)}</span>·<span>{durationLabel(featured)}</span>·<span>{featured.assetIds.length} assets</span>
                  </div>
                  <div className="mt-auto pt-4 flex gap-2">
                    <Link to={`/projects/${featured.id}/editor`} className={buttonClass('primary')}>
                      Open editor <ArrowRight className="w-3.5 h-3.5 ml-1.5" />
                    </Link>
                    <Link to={`/projects/${featured.id}`} className={buttonClass('secondary')}>
                      Overview
                    </Link>
                  </div>
                </div>
              </Card>
            ) : (
              <EmptyState
                icon={<FolderOpen className="w-5 h-5" />}
                title="No projects yet"
                message="Create a project to start planning an edit."
                action={<Button variant="primary" onClick={() => navigate('/projects/new')}>New Project</Button>}
              />
            )}

            <section aria-labelledby="stats-h">
              <h2 id="stats-h" className="sr-only">
                Statistics
              </h2>
              <dl className="grid grid-cols-3 gap-3">
                {stats.map((s) => (
                  <Card key={s.label} className="p-4">
                    <dt className="text-xs text-fg-3">{s.label}</dt>
                    <dd className="text-2xl font-semibold text-fg mt-1 font-mono">{s.value}</dd>
                  </Card>
                ))}
              </dl>
              <p className="text-[11px] text-fg-3 mt-1.5">Sample values from demo fixtures.</p>
            </section>

            <section aria-labelledby="recent-h">
              <SectionHeading
                id="recent-h"
                title="Recent projects"
                action={
                  <Segmented
                    size="sm"
                    label="Filter recent projects"
                    value={filter}
                    onChange={setFilter}
                    options={[
                      { value: 'all', label: 'All' },
                      { value: 'draft', label: 'Draft' },
                      { value: 'review', label: 'Review' },
                      { value: 'approved', label: 'Approved' },
                    ]}
                  />
                }
              />
              {recent.length === 0 ? (
                <EmptyState compact title="No projects with this status" action={<Button size="sm" onClick={() => setFilter('all')}>Show all</Button>} />
              ) : (
                <Card className="overflow-hidden">
                  <table className="w-full text-[13px]">
                    <thead>
                      <tr className="text-left text-xs text-fg-3 border-b border-line">
                        <th className="font-medium px-4 py-2">Title</th>
                        <th className="font-medium px-2 py-2 hidden md:table-cell">Format</th>
                        <th className="font-medium px-2 py-2">Target</th>
                        <th className="font-medium px-2 py-2 hidden md:table-cell">Last edited</th>
                        <th className="font-medium px-4 py-2">State</th>
                      </tr>
                    </thead>
                    <tbody>
                      {recent.map((p) => (
                        <tr key={p.id} className="border-b border-line-subtle last:border-0 hover:bg-elevated/50">
                          <td className="px-4 py-2.5 max-w-0 w-1/2">
                            <Link to={`/projects/${p.id}`} className="text-fg font-medium hover:underline truncate block">
                              {p.name}
                            </Link>
                          </td>
                          <td className="px-2 py-2.5 text-fg-2 hidden md:table-cell whitespace-nowrap">
                            {PLATFORM_LABEL[p.platform]} · {aspectLabel(p)}
                          </td>
                          <td className="px-2 py-2.5 font-mono text-fg-2">{durationLabel(p)}</td>
                          <td className="px-2 py-2.5 text-fg-3 hidden md:table-cell whitespace-nowrap">{formatDate(p.updatedAtIso)}</td>
                          <td className="px-4 py-2.5">
                            <ProjectStatusBadge status={p.status} />
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </Card>
              )}
            </section>
          </div>

          <div className="flex flex-col gap-6">
            <section aria-labelledby="gs-h">
              <SectionHeading id="gs-h" title="Getting started" />
              <ol className="flex flex-col gap-2">
                {[
                  ['Create a project', 'Pick a format and target length.', '/projects/new'],
                  ['Attach demo assets & write a brief', 'Interview, B-roll, music — all user-supplied.', '/projects/new'],
                  ['Inspect the timeline & export settings', 'Open the sample edit in the editor.', '/projects/reel_001/editor'],
                ].map(([t, b, to], i) => (
                  <li key={t}>
                    <Link to={to} className="flex gap-3 p-3 rounded-lg border border-line bg-surface hover:border-fg-3">
                      <span className="w-6 h-6 rounded-full bg-elevated text-xs font-mono flex items-center justify-center shrink-0">{i + 1}</span>
                      <span>
                        <span className="block text-[13px] font-medium text-fg">{t}</span>
                        <span className="block text-xs text-fg-2">{b}</span>
                      </span>
                    </Link>
                  </li>
                ))}
              </ol>
            </section>
            <section aria-labelledby="act-h">
              <SectionHeading
                id="act-h"
                title="Recent activity"
                action={
                  <Link to="/activity" className="text-xs text-accent hover:underline inline-flex items-center gap-1">
                    <Activity className="w-3.5 h-3.5" /> Open Activity
                  </Link>
                }
              />
              <RecentActivityFeed limit={4} />
            </section>
          </div>
        </div>
      )}
    </PageLayout>
  );
};
