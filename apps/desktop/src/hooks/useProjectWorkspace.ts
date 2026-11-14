/**
 * Loads everything a project-scoped screen needs (overview, editor, review)
 * from the repository, and exposes mutations that keep local state, the
 * repository and the app-wide lists in sync.
 */
import { useCallback, useEffect, useState } from 'react';
import { useAppData } from '../app/AppDataContext';
import type {
  DemoAsset,
  DemoProject,
  DemoQAItem,
  DemoReviewNote,
  DemoRevision,
  DemoTranscriptUtterance,
} from '../types';

export type WorkspaceStatus = 'loading' | 'ready' | 'missing' | 'error';

export interface ProjectWorkspace {
  status: WorkspaceStatus;
  error: string | null;
  project: DemoProject | null;
  revisions: DemoRevision[];
  assets: DemoAsset[];
  transcript: DemoTranscriptUtterance[];
  qaItems: DemoQAItem[];
  notes: DemoReviewNote[];
  reload: () => Promise<void>;
  updateProject: (patch: Partial<DemoProject>) => Promise<void>;
  saveRevision: (rev: DemoRevision) => Promise<void>;
  createSampleRevision: () => Promise<DemoRevision | null>;
  addAssets: (assetIds: string[]) => Promise<void>;
  addNote: (note: Omit<DemoReviewNote, 'id' | 'createdAtIso'>) => Promise<void>;
  updateNote: (noteId: string, patch: Partial<DemoReviewNote>) => Promise<void>;
  removeNote: (noteId: string) => Promise<void>;
}

export function useProjectWorkspace(projectId: string | undefined): ProjectWorkspace {
  const app = useAppData();
  const { repo } = app;
  const [status, setStatus] = useState<WorkspaceStatus>('loading');
  const [error, setError] = useState<string | null>(null);
  const [project, setProject] = useState<DemoProject | null>(null);
  const [revisions, setRevisions] = useState<DemoRevision[]>([]);
  const [assets, setAssets] = useState<DemoAsset[]>([]);
  const [transcript, setTranscript] = useState<DemoTranscriptUtterance[]>([]);
  const [qaItems, setQaItems] = useState<DemoQAItem[]>([]);
  const [notes, setNotes] = useState<DemoReviewNote[]>([]);

  const reload = useCallback(async () => {
    if (!projectId) {
      setStatus('missing');
      return;
    }
    try {
      const p = await repo.getProject(projectId);
      if (!p) {
        setProject(null);
        setStatus('missing');
        return;
      }
      const [r, a, t, q, n] = await Promise.all([
        repo.listRevisions(projectId),
        repo.listAssets(projectId),
        repo.getTranscript(projectId),
        repo.getQAItems(projectId),
        repo.getReviewNotes(projectId),
      ]);
      setProject(p);
      setRevisions(r);
      setAssets(a);
      setTranscript(t);
      setQaItems(q);
      setNotes(n);
      setError(null);
      setStatus('ready');
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
      setStatus('error');
    }
  }, [projectId, repo]);

  // Show the loading state only when the project changes…
  useEffect(() => {
    setStatus('loading');
  }, [projectId]);
  // …and refetch silently whenever repository data is mutated elsewhere (e.g. Reset Demo Data).
  useEffect(() => {
    void reload();
  }, [reload, app.dataVersion]);

  const updateProject = useCallback(
    async (patch: Partial<DemoProject>) => {
      if (!projectId) return;
      const updated = await app.updateProject(projectId, patch);
      setProject(updated);
    },
    [projectId, app]
  );

  const saveRevision = useCallback(
    async (rev: DemoRevision) => {
      setRevisions((prev) => prev.map((r) => (r.id === rev.id ? rev : r)));
      await repo.saveRevision(rev.projectId, rev);
    },
    [repo]
  );

  const createSampleRevision = useCallback(async () => {
    if (!projectId) return null;
    const rev = await repo.createSampleRevision(projectId);
    await app.refresh();
    await reload();
    return rev;
  }, [projectId, repo, app, reload]);

  const addAssets = useCallback(
    async (ids: string[]) => {
      if (!projectId) return;
      await repo.addAssetsToProject(projectId, ids);
      await app.refresh();
      await reload();
    },
    [projectId, repo, app, reload]
  );

  const addNote = useCallback(
    async (note: Omit<DemoReviewNote, 'id' | 'createdAtIso'>) => {
      if (!projectId) return;
      const created = await repo.addReviewNote(projectId, note);
      setNotes((prev) => [...prev, created]);
    },
    [projectId, repo]
  );

  const updateNote = useCallback(
    async (noteId: string, patch: Partial<DemoReviewNote>) => {
      if (!projectId) return;
      const updated = await repo.updateReviewNote(projectId, noteId, patch);
      setNotes((prev) => prev.map((n) => (n.id === noteId ? updated : n)));
    },
    [projectId, repo]
  );

  const removeNote = useCallback(
    async (noteId: string) => {
      if (!projectId) return;
      await repo.removeReviewNote(projectId, noteId);
      setNotes((prev) => prev.filter((n) => n.id !== noteId));
    },
    [projectId, repo]
  );

  return {
    status,
    error,
    project,
    revisions,
    assets,
    transcript,
    qaItems,
    notes,
    reload,
    updateProject,
    saveRevision,
    createSampleRevision,
    addAssets,
    addNote,
    updateNote,
    removeNote,
  };
}

/** Resolve the revision to display: ?rev= param → project's active → latest. */
export function pickRevision(
  revisions: DemoRevision[],
  project: DemoProject | null,
  requested: string | null
): DemoRevision | null {
  return (
    revisions.find((r) => r.id === requested) ??
    revisions.find((r) => r.id === project?.activeRevisionId) ??
    revisions[revisions.length - 1] ??
    null
  );
}
