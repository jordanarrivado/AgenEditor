import React, { useMemo, useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import * as Dropdown from '@radix-ui/react-dropdown-menu';
import { Archive, Copy, LayoutGrid, List, MoreHorizontal, Pencil, Plus, Search } from 'lucide-react';
import { useAppData } from '../../app/AppDataContext';
import type { DemoProject, ProjectStatus } from '../../types';
import {
  PageLayout,
  PLATFORM_LABEL,
  ProjectStatusBadge,
  ProjectThumb,
  aspectLabel,
  durationLabel,
  formatDate,
} from '../../components/projects/ProjectBits';
import { Button, Card, Field, IconButton, Input, Segmented, Select, cx } from '../../components/ui/primitives';
import { ConfirmDialog, Modal } from '../../components/ui/Modal';
import { EmptyState, ErrorState, LoadingState } from '../../components/ui/StateViews';
import { showToast } from '../../components/common/Toast';

type Sort = 'updated' | 'name';
const VIEW_KEY = 'ageneditor_projects_view';

function readView(): 'grid' | 'list' {
  try {
    return window.localStorage.getItem(VIEW_KEY) === 'list' ? 'list' : 'grid';
  } catch {
    return 'grid';
  }
}

export const ProjectsPage: React.FC = () => {
  const { projects, status, error, refresh, duplicateProject, updateProject, archiveProject } = useAppData();
  const navigate = useNavigate();
  const [q, setQ] = useState('');
  const [statusFilter, setStatusFilter] = useState<'all' | ProjectStatus>('all');
  const [sort, setSort] = useState<Sort>('updated');
  const [view, setViewState] = useState<'grid' | 'list'>(readView);
  const [renaming, setRenaming] = useState<DemoProject | null>(null);
  const [archiving, setArchiving] = useState<DemoProject | null>(null);

  const setView = (v: 'grid' | 'list') => {
    setViewState(v);
    try {
      window.localStorage.setItem(VIEW_KEY, v);
    } catch {
      /* per-viewer convenience only */
    }
  };

  const visible = useMemo(() => {
    const s = q.trim().toLowerCase();
    return projects
      .filter((p) => (statusFilter === 'all' || p.status === statusFilter) && (!s || `${p.name} ${p.description}`.toLowerCase().includes(s)))
      .sort((a, b) => (sort === 'name' ? a.name.localeCompare(b.name) : b.updatedAtIso.localeCompare(a.updatedAtIso)));
  }, [projects, q, statusFilter, sort]);

  const actions = {
    duplicate: async (p: DemoProject) => {
      const copy = await duplicateProject(p.id);
      showToast({ type: 'success', title: 'Project duplicated', message: `${copy.name} (demo data)` });
    },
    rename: (p: DemoProject) => setRenaming(p),
    archive: (p: DemoProject) => setArchiving(p),
  };

  return (
    <PageLayout
      title="Projects"
      description="All demo projects. Archive and rename only change local demo data."
      width="wide"
      actions={
        <Button variant="primary" icon={<Plus className="w-4 h-4" />} onClick={() => navigate('/projects/new')}>
          New Project
        </Button>
      }
    >
      <div className="flex flex-wrap items-center gap-3 mb-5">
        <div className="relative w-full sm:w-72">
          <Search aria-hidden className="w-3.5 h-3.5 absolute left-2.5 top-2.5 text-fg-3" />
          <input
            type="search"
            aria-label="Search projects"
            placeholder="Search projects"
            value={q}
            onChange={(e) => setQ(e.target.value)}
            className="w-full h-8 pl-8 pr-2 rounded-md bg-app border border-line text-[13px] text-fg placeholder:text-fg-3 focus:outline-none focus:border-accent"
          />
        </div>
        <label className="sr-only" htmlFor="status-filter">
          Status
        </label>
        <Select id="status-filter" value={statusFilter} onChange={(e) => setStatusFilter(e.target.value as typeof statusFilter)} className="w-40">
          <option value="all">All statuses</option>
          <option value="draft">Draft</option>
          <option value="review">In review</option>
          <option value="approved">Approved</option>
        </Select>
        <label className="sr-only" htmlFor="sort">
          Sort
        </label>
        <Select id="sort" value={sort} onChange={(e) => setSort(e.target.value as Sort)} className="w-44">
          <option value="updated">Recently updated</option>
          <option value="name">Name (A–Z)</option>
        </Select>
        <div className="flex-1" />
        <Segmented
          label="View"
          value={view}
          onChange={setView}
          options={[
            { value: 'grid', label: <><LayoutGrid className="w-3.5 h-3.5" /> Grid</> },
            { value: 'list', label: <><List className="w-3.5 h-3.5" /> List</> },
          ]}
        />
      </div>

      {status === 'loading' && <LoadingState rows={4} />}
      {status === 'error' && <ErrorState message={error} onRetry={refresh} />}
      {status === 'ready' &&
        (projects.length === 0 ? (
          <EmptyState
            title="No projects"
            message="Create one, or restore the demo fixtures with Reset Demo Data."
            action={<Button variant="primary" onClick={() => navigate('/projects/new')}>New Project</Button>}
          />
        ) : visible.length === 0 ? (
          <EmptyState
            title="No matching projects"
            message="Try a different search or status."
            action={
              <Button onClick={() => { setQ(''); setStatusFilter('all'); }}>
                Clear filters
              </Button>
            }
          />
        ) : view === 'grid' ? (
          <ul aria-label="Projects" className="grid grid-cols-[repeat(auto-fill,minmax(240px,1fr))] gap-4">
            {visible.map((p) => (
              <li key={p.id}>
                <Card className="overflow-hidden h-full flex flex-col hover:border-fg-3 transition-colors">
                  <Link to={`/projects/${p.id}`} aria-label={`Open ${p.name}`}>
                    <ProjectThumb project={p} className="h-40" />
                  </Link>
                  <div className="p-3.5 flex-1 flex flex-col gap-1.5">
                    <div className="flex items-start justify-between gap-2">
                      <Link to={`/projects/${p.id}`} className="text-[14px] font-semibold text-fg leading-snug hover:underline line-clamp-2">
                        {p.name}
                      </Link>
                      <ProjectMenu project={p} {...actions} />
                    </div>
                    <div className="flex flex-wrap items-center gap-x-2 gap-y-1 text-xs text-fg-3">
                      <span>{aspectLabel(p)}</span>·<span>{durationLabel(p)}</span>·<span>{p.assetIds.length} assets</span>
                    </div>
                    <div className="mt-auto pt-2 flex items-center justify-between gap-2">
                      <ProjectStatusBadge status={p.status} />
                      <span className="text-[11px] text-fg-3">{formatDate(p.updatedAtIso)}</span>
                    </div>
                  </div>
                </Card>
              </li>
            ))}
          </ul>
        ) : (
          <Card className="overflow-x-auto">
            <table className="w-full text-[13px]" aria-label="Projects">
              <thead>
                <tr className="text-left text-xs text-fg-3 border-b border-line">
                  <th className="font-medium px-4 py-2">Title</th>
                  <th className="font-medium px-2 py-2">Platform</th>
                  <th className="font-medium px-2 py-2">Format</th>
                  <th className="font-medium px-2 py-2">Target</th>
                  <th className="font-medium px-2 py-2">Assets</th>
                  <th className="font-medium px-2 py-2">Updated</th>
                  <th className="font-medium px-2 py-2">Status</th>
                  <th className="px-2 py-2">
                    <span className="sr-only">Actions</span>
                  </th>
                </tr>
              </thead>
              <tbody>
                {visible.map((p) => (
                  <tr key={p.id} className="border-b border-line-subtle last:border-0 hover:bg-elevated/50">
                    <td className="px-4 py-2.5">
                      <Link to={`/projects/${p.id}`} className="text-fg font-medium hover:underline">
                        {p.name}
                      </Link>
                    </td>
                    <td className="px-2 py-2.5 text-fg-2 whitespace-nowrap">{PLATFORM_LABEL[p.platform]}</td>
                    <td className="px-2 py-2.5 font-mono text-fg-2">{aspectLabel(p)}</td>
                    <td className="px-2 py-2.5 font-mono text-fg-2">{durationLabel(p)}</td>
                    <td className="px-2 py-2.5 font-mono text-fg-2">{p.assetIds.length}</td>
                    <td className="px-2 py-2.5 text-fg-3 whitespace-nowrap">{formatDate(p.updatedAtIso)}</td>
                    <td className="px-2 py-2.5">
                      <ProjectStatusBadge status={p.status} />
                    </td>
                    <td className="px-2 py-2.5 text-right">
                      <ProjectMenu project={p} {...actions} />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </Card>
        ))}

      <RenameDialog
        project={renaming}
        onClose={() => setRenaming(null)}
        onSave={async (name) => {
          if (!renaming) return;
          await updateProject(renaming.id, { name });
          showToast({ type: 'success', title: 'Project renamed' });
          setRenaming(null);
        }}
      />
      <ConfirmDialog
        open={!!archiving}
        onOpenChange={(o) => !o && setArchiving(null)}
        destructive
        title="Archive project?"
        message={`“${archiving?.name}” will be removed from the demo library. Use Reset Demo Data to restore fixtures.`}
        confirmLabel="Archive"
        onConfirm={async () => {
          if (!archiving) return;
          await archiveProject(archiving.id);
          showToast({ type: 'info', title: 'Project archived', message: 'Demo data only.' });
        }}
      />
    </PageLayout>
  );
};

const ProjectMenu: React.FC<{
  project: DemoProject;
  duplicate: (p: DemoProject) => void;
  rename: (p: DemoProject) => void;
  archive: (p: DemoProject) => void;
}> = ({ project, duplicate, rename, archive }) => {
  const item = 'flex items-center gap-2 px-2 py-1.5 rounded-md outline-none cursor-default data-[highlighted]:bg-surface';
  return (
    <Dropdown.Root>
      <Dropdown.Trigger asChild>
        <IconButton label={`More actions for ${project.name}`} size="sm">
          <MoreHorizontal className="w-4 h-4" />
        </IconButton>
      </Dropdown.Trigger>
      <Dropdown.Portal>
        <Dropdown.Content align="end" sideOffset={4} className="z-[70] min-w-[180px] rounded-lg border border-line bg-elevated p-1 text-[13px] text-fg shadow-xl">
          <Dropdown.Item className={item} onSelect={() => rename(project)}>
            <Pencil className="w-3.5 h-3.5" /> Rename
          </Dropdown.Item>
          <Dropdown.Item className={item} onSelect={() => duplicate(project)}>
            <Copy className="w-3.5 h-3.5" /> Duplicate
          </Dropdown.Item>
          <Dropdown.Separator className="h-px bg-line my-1" />
          <Dropdown.Item className={cx(item, 'text-danger')} onSelect={() => archive(project)}>
            <Archive className="w-3.5 h-3.5" /> Archive…
          </Dropdown.Item>
        </Dropdown.Content>
      </Dropdown.Portal>
    </Dropdown.Root>
  );
};

export const RenameDialog: React.FC<{ project: DemoProject | null; onClose: () => void; onSave: (name: string) => void }> = ({
  project,
  onClose,
  onSave,
}) => {
  const [name, setName] = useState('');
  React.useEffect(() => setName(project?.name ?? ''), [project]);
  const error = name.trim() ? undefined : 'Name is required.';
  return (
    <Modal
      open={!!project}
      onOpenChange={(o) => !o && onClose()}
      size="sm"
      title="Rename project"
      footer={
        <>
          <Button onClick={onClose}>Cancel</Button>
          <Button variant="primary" disabled={!!error} onClick={() => onSave(name.trim())}>
            Save
          </Button>
        </>
      }
    >
      <form
        onSubmit={(e) => {
          e.preventDefault();
          if (!error) onSave(name.trim());
        }}
      >
        <Field label="Project name" htmlFor="rename-input" error={error}>
          <Input id="rename-input" autoFocus value={name} aria-invalid={!!error} onChange={(e) => setName(e.target.value)} />
        </Field>
      </form>
    </Modal>
  );
};
