import React, { useState } from 'react';
import { Archive, ArchiveRestore, ArrowRight, Clapperboard, Pencil, Plus, Search } from 'lucide-react';
import type { EngineProject } from '../../services/liveEngineRepository';
import { Button } from '../../components/ui/primitives';

const field = 'w-full rounded-lg border border-line bg-app px-3 py-2 text-sm text-fg outline-none placeholder:text-fg-3 focus:border-accent';
const SEARCH_FROM = 7;

const STEPS = [
  { title: 'Add footage', text: 'Main video, references and assets.' },
  { title: 'Plan', text: 'Opus 5.5 writes the edit plan.' },
  { title: 'Execute', text: 'Sonnet 5.5 cuts and renders a preview.' },
  { title: 'Review & export', text: 'Opus 5.5 checks it; you save the MP4.' },
];

const createdOn = (iso: string) => {
  const date = new Date(iso);
  return Number.isNaN(date.getTime()) ? '' : new Intl.DateTimeFormat(undefined, { dateStyle: 'medium' }).format(date);
};

export interface HomeViewProps {
  projects: EngineProject[] | null;
  creating: boolean;
  newName: string;
  busy: boolean;
  onNewName: (name: string) => void;
  onStartCreate: () => void;
  onCancelCreate: () => void;
  onCreate: () => void;
  onOpen: (projectId: string) => void;
  /** The "Set up this PC" checklist, shown above the projects. */
  setup?: React.ReactNode;
  /** Renames a project (display name only). */
  onRename?: (projectId: string, name: string) => Promise<void>;
  /** Hides or shows a project in the list; nothing is deleted. */
  onArchive?: (projectId: string, archived: boolean) => Promise<void>;
}

/** The start screen: every project in this workspace, newest first, and a way to make a new one. */
export const HomeView: React.FC<HomeViewProps> = ({
  projects, creating, newName, busy, onNewName, onStartCreate, onCancelCreate, onCreate, onOpen, setup, onRename, onArchive,
}) => {
  const [query, setQuery] = useState('');
  const [showArchived, setShowArchived] = useState(false);
  const [renaming, setRenaming] = useState<{ id: string; name: string } | null>(null);
  const archivedCount = (projects ?? []).filter((project) => project.archived).length;
  const shown = (projects ?? [])
    .filter((project) => showArchived || !project.archived)
    .filter((project) => project.name.toLowerCase().includes(query.trim().toLowerCase()));
  const submitRename = async () => {
    if (!renaming || !onRename || !renaming.name.trim()) return;
    await onRename(renaming.id, renaming.name.trim());
    setRenaming(null);
  };

  return (
    <main aria-labelledby="home-title" className="min-h-0 flex-1 overflow-y-auto">
      <div className="mx-auto max-w-5xl space-y-8 px-8 py-10">
        <div className="flex flex-wrap items-end justify-between gap-4">
          <div>
            <h1 id="home-title" className="text-2xl font-semibold tracking-tight">Your projects</h1>
            <p className="mt-1 text-sm text-fg-2">Each project keeps its own files, instructions and revisions. Your original files are never changed.</p>
          </div>
          {creating ? (
            <form className="flex items-center gap-2" onSubmit={(e) => { e.preventDefault(); onCreate(); }}>
              <input autoFocus aria-label="New project name" className={`${field} h-10 w-56`} placeholder="Project name" value={newName} maxLength={100} onChange={(e) => onNewName(e.target.value)} />
              <Button variant="primary" size="lg" type="submit" disabled={busy || !newName.trim()}>Create</Button>
              <Button size="lg" onClick={onCancelCreate}>Cancel</Button>
            </form>
          ) : <Button variant="primary" size="lg" icon={<Plus className="h-4 w-4" />} onClick={onStartCreate}>New project</Button>}
        </div>

        {setup}

        {projects && projects.length >= SEARCH_FROM && (
          <label className="relative block max-w-sm">
            <span className="sr-only">Search projects</span>
            <Search aria-hidden className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-fg-3" />
            <input className={`${field} pl-9`} placeholder="Search projects" value={query} onChange={(e) => setQuery(e.target.value)} />
          </label>
        )}

        {archivedCount > 0 && (
          <label className="flex items-center gap-2 text-sm text-fg-2">
            <input type="checkbox" checked={showArchived} onChange={(e) => setShowArchived(e.target.checked)} />
            Show archived projects ({archivedCount})
          </label>
        )}

        {projects === null && <p className="text-sm text-fg-3">Loading projects…</p>}

        {projects && projects.length === 0 && (
          <div className="rounded-2xl border border-dashed border-line bg-surface px-6 py-12 text-center">
            <span aria-hidden className="mx-auto mb-3 flex h-12 w-12 items-center justify-center rounded-xl bg-accent/12 text-accent"><Clapperboard className="h-6 w-6" /></span>
            <p className="font-semibold">No projects yet</p>
            <p className="mt-1 text-sm text-fg-3">Create one, add your footage, and tell Claude what to make.</p>
            {!creating && <Button variant="primary" size="lg" className="mt-4" icon={<Plus className="h-4 w-4" />} onClick={onStartCreate}>New project</Button>}
          </div>
        )}

        {projects && projects.length > 0 && (
          shown.length === 0
            ? <p className="text-sm text-fg-3">{query.trim() ? `No project matches “${query.trim()}”.` : 'All projects are archived.'}</p>
            : (
              <ul className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3" aria-label="Projects">
                {shown.map((project) => (
                  <li key={project.id} className="space-y-1.5">
                    {renaming?.id === project.id ? (
                      <form className="flex items-center gap-2 rounded-2xl border border-accent/60 bg-surface p-3" onSubmit={(e) => { e.preventDefault(); void submitRename(); }}>
                        <input autoFocus aria-label={`New name for ${project.name}`} className={`${field} h-9`} maxLength={80} value={renaming.name}
                          onChange={(e) => setRenaming({ id: project.id, name: e.target.value })} />
                        <Button variant="primary" type="submit" disabled={busy || !renaming.name.trim()}>Save</Button>
                        <Button onClick={() => setRenaming(null)}>Cancel</Button>
                      </form>
                    ) : (
                    <button type="button" aria-label={`Open ${project.name}`} onClick={() => onOpen(project.id)}
                      className="group flex h-full w-full items-start gap-3 rounded-2xl border border-line bg-surface p-4 text-left transition-colors hover:border-accent/60 hover:bg-elevated">
                      <span aria-hidden className="flex h-11 w-11 shrink-0 items-center justify-center rounded-xl bg-accent/12 text-accent"><Clapperboard className="h-5 w-5" /></span>
                      <span className="min-w-0 flex-1">
                        <span className="block truncate font-semibold">{project.name}</span>
                        <span className="mt-0.5 block text-xs text-fg-3">
                          {project.current_revision === 0 ? 'Not edited yet' : `Revision ${project.current_revision}`}
                          {createdOn(project.created_at) && ` · created ${createdOn(project.created_at)}`}
                        </span>
                      </span>
                      <ArrowRight aria-hidden className="mt-1 h-4 w-4 shrink-0 text-fg-3 transition-transform group-hover:translate-x-0.5 group-hover:text-accent" />
                    </button>
                    )}
                    {(onRename || onArchive) && renaming?.id !== project.id && (
                      <div className="flex items-center gap-1 px-1 text-xs">
                        {project.archived && <span className="mr-auto text-fg-3">Archived</span>}
                        {onRename && (
                          <button type="button" aria-label={`Rename ${project.name}`} className="inline-flex items-center gap-1 rounded px-1.5 py-0.5 text-fg-3 hover:text-fg" disabled={busy}
                            onClick={() => setRenaming({ id: project.id, name: project.name })}>
                            <Pencil aria-hidden className="h-3 w-3" /> Rename
                          </button>
                        )}
                        {onArchive && (
                          <button type="button" aria-label={`${project.archived ? 'Unarchive' : 'Archive'} ${project.name}`} className="inline-flex items-center gap-1 rounded px-1.5 py-0.5 text-fg-3 hover:text-fg" disabled={busy}
                            onClick={() => void onArchive(project.id, !project.archived)}>
                            {project.archived ? <ArchiveRestore aria-hidden className="h-3 w-3" /> : <Archive aria-hidden className="h-3 w-3" />}
                            {project.archived ? 'Unarchive' : 'Archive'}
                          </button>
                        )}
                      </div>
                    )}
                  </li>
                ))}
              </ul>
            )
        )}

        <section aria-labelledby="how-title" className="space-y-3">
          <h2 id="how-title" className="text-xs font-semibold uppercase tracking-[0.08em] text-fg-2">How it works</h2>
          <ol className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-4">
            {STEPS.map((step, index) => (
              <li key={step.title} className="flex items-start gap-3 rounded-xl border border-line bg-surface/60 p-3.5">
                <span aria-hidden className="flex h-7 w-7 shrink-0 items-center justify-center rounded-full border border-accent/60 text-xs font-semibold text-accent">{index + 1}</span>
                <span className="min-w-0">
                  <span className="block text-sm font-semibold">{step.title}</span>
                  <span className="block text-xs text-fg-3">{step.text}</span>
                </span>
              </li>
            ))}
          </ol>
        </section>
      </div>
    </main>
  );
};
