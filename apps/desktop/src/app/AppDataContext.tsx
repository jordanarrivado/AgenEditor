/**
 * App-wide repository state: project list, job list and all demo assets.
 * Screens read through this context (or useProjectWorkspace) — they never
 * import the mock repository class.
 */
import React, { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from 'react';
import type { ProjectRepository } from '../services/projectRepository';
import type { DemoAsset, DemoJob, DemoProject, JobKind, NewProjectInput } from '../types';

export type LoadStatus = 'loading' | 'ready' | 'error';

interface AppDataCtx {
  repo: ProjectRepository;
  persistsLocally: boolean;
  status: LoadStatus;
  error: string | null;
  projects: DemoProject[];
  jobs: DemoJob[];
  allAssets: DemoAsset[];
  /** Increments after every mutation; workspace hooks use it to refetch. */
  dataVersion: number;
  refresh: () => Promise<void>;
  createProject: (input: NewProjectInput) => Promise<DemoProject>;
  duplicateProject: (id: string) => Promise<DemoProject>;
  updateProject: (id: string, patch: Partial<DemoProject>) => Promise<DemoProject>;
  archiveProject: (id: string) => Promise<void>;
  startSimulationJob: (projectId: string, kind: JobKind, message?: string) => Promise<DemoJob>;
  retryJob: (jobId: string) => Promise<void>;
  cancelJob: (jobId: string) => Promise<void>;
  resetDemoData: () => Promise<void>;
}

const Ctx = createContext<AppDataCtx | null>(null);

/** Deterministic simulation step: +10% every tick (tick = 400 ms). */
export const SIM_STEP_PERCENT = 10;
export const SIM_TICK_MS = 400;

export const AppDataProvider: React.FC<{
  repo: ProjectRepository;
  persistsLocally?: boolean;
  children: React.ReactNode;
}> = ({ repo, persistsLocally = false, children }) => {
  const [status, setStatus] = useState<LoadStatus>('loading');
  const [error, setError] = useState<string | null>(null);
  const [projects, setProjects] = useState<DemoProject[]>([]);
  const [jobs, setJobs] = useState<DemoJob[]>([]);
  const [allAssets, setAllAssets] = useState<DemoAsset[]>([]);
  const [dataVersion, setDataVersion] = useState(0);
  /** Jobs started in this session advance automatically; fixture jobs stay as presentation states. */
  const liveJobs = useRef<Set<string>>(new Set());

  const refresh = useCallback(async () => {
    try {
      const [p, j, a] = await Promise.all([repo.listProjects(), repo.listJobs(), repo.listAllAssets()]);
      setProjects(p);
      setJobs(j);
      setAllAssets(a);
      setError(null);
      setStatus('ready');
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
      setStatus('error');
    }
  }, [repo]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const mutated = useCallback(async () => {
    await refresh();
    setDataVersion((v) => v + 1);
  }, [refresh]);

  // ---- Deterministic job simulation ticker
  const hasLive = jobs.some((j) => liveJobs.current.has(j.id) && (j.status === 'queued' || j.status === 'running'));
  useEffect(() => {
    if (!hasLive) return;
    let busy = false;
    const timer = window.setInterval(async () => {
      if (busy) return;
      busy = true;
      try {
        await advance();
      } finally {
        busy = false;
      }
    }, SIM_TICK_MS);
    const advance = async () => {
      const current = await repo.listJobs();
      for (const job of current) {
        if (!liveJobs.current.has(job.id)) continue;
        if (job.status === 'queued') {
          await repo.updateJob(job.id, { status: 'running', progressPercent: SIM_STEP_PERCENT });
        } else if (job.status === 'running') {
          const next = Math.min(100, job.progressPercent + SIM_STEP_PERCENT);
          await repo.updateJob(
            job.id,
            next >= 100
              ? {
                  status: 'completed',
                  progressPercent: 100,
                  message: `Simulation complete: ${job.kind} job finished. No file was rendered or written.`,
                }
              : { progressPercent: next }
          );
          if (next >= 100) liveJobs.current.delete(job.id);
        }
      }
      setJobs(await repo.listJobs());
    };
    return () => window.clearInterval(timer);
  }, [hasLive, repo]);

  const value = useMemo<AppDataCtx>(
    () => ({
      repo,
      persistsLocally,
      status,
      error,
      projects,
      jobs,
      allAssets,
      dataVersion,
      refresh,
      createProject: async (input) => {
        const p = await repo.createProject(input);
        await mutated();
        return p;
      },
      duplicateProject: async (id) => {
        const p = await repo.duplicateProject(id);
        await mutated();
        return p;
      },
      updateProject: async (id, patch) => {
        const p = await repo.updateProject(id, patch);
        await mutated();
        return p;
      },
      archiveProject: async (id) => {
        await repo.archiveProject(id);
        await mutated();
      },
      startSimulationJob: async (projectId, kind, message) => {
        const job = await repo.createSimulationJob(projectId, kind, message);
        liveJobs.current.add(job.id);
        await refresh();
        return job;
      },
      retryJob: async (jobId) => {
        await repo.updateJob(jobId, {
          status: 'queued',
          progressPercent: 0,
          errorDetail: undefined,
          message: 'Simulation: job re-queued.',
        });
        liveJobs.current.add(jobId);
        await refresh();
      },
      cancelJob: async (jobId) => {
        liveJobs.current.delete(jobId);
        await repo.updateJob(jobId, { status: 'canceled', message: 'Simulation: job canceled by user.' });
        await refresh();
      },
      resetDemoData: async () => {
        liveJobs.current.clear();
        await repo.resetDemoData();
        await mutated();
      },
    }),
    [repo, persistsLocally, status, error, projects, jobs, allAssets, dataVersion, refresh, mutated]
  );

  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
};

export function useAppData(): AppDataCtx {
  const v = useContext(Ctx);
  if (!v) throw new Error('useAppData must be used inside AppDataProvider');
  return v;
}
