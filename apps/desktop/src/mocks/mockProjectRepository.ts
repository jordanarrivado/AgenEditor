import type { ProjectRepository } from '../services/projectRepository';
import type {
  DemoAsset,
  DemoJob,
  DemoProject,
  DemoQAItem,
  DemoReviewNote,
  DemoRevision,
  DemoTimelineTrack,
  DemoTranscriptUtterance,
  JobKind,
  NewProjectInput,
} from '../types';
import {
  INITIAL_ASSETS,
  INITIAL_JOBS,
  INITIAL_PROJECTS,
  INITIAL_QA_ITEMS,
  INITIAL_REVIEW_NOTES,
  INITIAL_REVISIONS,
  INITIAL_TRANSCRIPTS,
} from './fixtures';

const KEYS = {
  projects: 'ageneditor_demo_projects_v2',
  assets: 'ageneditor_demo_assets_v2',
  revisions: 'ageneditor_demo_revisions_v2',
  transcripts: 'ageneditor_demo_transcripts_v2',
  qa: 'ageneditor_demo_qa_v2',
  notes: 'ageneditor_demo_notes_v2',
  jobs: 'ageneditor_demo_jobs_v2',
} as const;

type Key = keyof typeof KEYS;

interface DemoDb {
  projects: DemoProject[];
  assets: DemoAsset[];
  revisions: Record<string, DemoRevision[]>;
  transcripts: Record<string, DemoTranscriptUtterance[]>;
  qa: Record<string, DemoQAItem[]>;
  notes: Record<string, DemoReviewNote[]>;
  jobs: DemoJob[];
}

const clone = <T,>(value: T): T => (value === undefined ? value : structuredClone(value));

function fixtureDb(): DemoDb {
  return clone({
    projects: INITIAL_PROJECTS,
    assets: INITIAL_ASSETS,
    revisions: INITIAL_REVISIONS,
    transcripts: INITIAL_TRANSCRIPTS,
    qa: INITIAL_QA_ITEMS,
    notes: INITIAL_REVIEW_NOTES,
    jobs: INITIAL_JOBS,
  });
}

/** Minimal storage contract so tests can inject an in-memory store. */
export interface KeyValueStorage {
  getItem(key: string): string | null;
  setItem(key: string, value: string): void;
}

function safeBrowserStorage(): KeyValueStorage | null {
  try {
    const s = window.localStorage;
    const probe = '__ageneditor_probe__';
    s.setItem(probe, '1');
    s.removeItem(probe);
    return s;
  } catch {
    return null; // private mode / blocked storage → in-memory only
  }
}

let idCounter = 0;
const newId = (prefix: string) => `${prefix}_${Date.now().toString(36)}${(idCounter++).toString(36)}`;

/**
 * Demo repository backed by localStorage (falls back to memory). Every record
 * is fixture/demo data; nothing here touches real media.
 */
export class MockProjectRepository implements ProjectRepository {
  private db: DemoDb;
  private storage: KeyValueStorage | null;
  /** Artificial latency so loading states are visible; 0 in tests. */
  private latencyMs: number;

  constructor(options: { storage?: KeyValueStorage | null; latencyMs?: number } = {}) {
    this.storage = options.storage === undefined ? safeBrowserStorage() : options.storage;
    this.latencyMs = options.latencyMs ?? 0;
    this.db = this.load();
  }

  /** True when demo edits persist across reloads (drives the save indicator). */
  get persistsLocally(): boolean {
    return this.storage !== null;
  }

  private load(): DemoDb {
    const base = fixtureDb();
    if (!this.storage) return base;
    try {
      const raw = this.storage.getItem(KEYS.projects);
      if (!raw) {
        this.persistAll(base);
        return base;
      }
      const read = <K extends Key>(k: K): DemoDb[K] => {
        const v = this.storage!.getItem(KEYS[k]);
        return v ? (JSON.parse(v) as DemoDb[K]) : base[k];
      };
      return {
        projects: read('projects'),
        assets: read('assets'),
        revisions: read('revisions'),
        transcripts: read('transcripts'),
        qa: read('qa'),
        notes: read('notes'),
        jobs: read('jobs'),
      };
    } catch {
      return base;
    }
  }

  private persist(...keys: Key[]): void {
    if (!this.storage) return;
    for (const k of keys) {
      try {
        this.storage.setItem(KEYS[k], JSON.stringify(this.db[k]));
      } catch {
        /* quota exceeded — keep in-memory state */
      }
    }
  }

  private persistAll(db: DemoDb): void {
    this.db = db;
    this.persist('projects', 'assets', 'revisions', 'transcripts', 'qa', 'notes', 'jobs');
  }

  private async tick<T>(value: T): Promise<T> {
    if (this.latencyMs > 0) await new Promise((r) => setTimeout(r, this.latencyMs));
    return clone(value);
  }

  private requireProject(projectId: string): DemoProject {
    const p = this.db.projects.find((x) => x.id === projectId);
    if (!p) throw new Error(`Project ${projectId} not found`);
    return p;
  }

  // ---------------- Projects ----------------

  async listProjects() {
    return this.tick(this.db.projects);
  }

  async getProject(projectId: string) {
    return this.tick(this.db.projects.find((p) => p.id === projectId));
  }

  async createProject(input: NewProjectInput) {
    const id = newId('proj');
    const durationFrames = Math.round(input.targetDurationSeconds * input.fps);
    const [width, height] =
      input.aspectRatio === '16:9' ? [1920, 1080] : input.aspectRatio === '1:1' ? [1080, 1080] : [1080, 1920];
    const project: DemoProject = {
      id,
      name: input.name.trim(),
      description: input.description.trim() || 'Demo project created in the UI prototype.',
      status: 'draft',
      platform: input.platform,
      targetDurationFrames: durationFrames,
      fpsNum: input.fps,
      fpsDen: 1,
      width,
      height,
      brief: input.brief,
      briefOptions: input.briefOptions,
      stylePresetId: input.stylePresetId,
      assetIds: [...input.selectedAssetIds],
      activeRevisionId: null,
      updatedAtIso: new Date().toISOString(),
    };
    this.db.projects.unshift(project);
    this.db.revisions[id] = [];
    this.persist('projects', 'revisions');
    return this.tick(project);
  }

  async duplicateProject(projectId: string) {
    const src = this.requireProject(projectId);
    const id = newId('proj');
    const revisions = (this.db.revisions[projectId] ?? []).map((r) => ({
      ...clone(r),
      id: `${r.id}_${id}`,
      projectId: id,
    }));
    const activeIdx = (this.db.revisions[projectId] ?? []).findIndex((r) => r.id === src.activeRevisionId);
    const copy: DemoProject = {
      ...clone(src),
      id,
      name: `${src.name} (Copy)`,
      status: 'draft',
      activeRevisionId: activeIdx >= 0 ? revisions[activeIdx].id : null,
      updatedAtIso: new Date().toISOString(),
    };
    this.db.projects.unshift(copy);
    this.db.revisions[id] = revisions;
    this.db.transcripts[id] = clone(this.db.transcripts[projectId] ?? []);
    this.persist('projects', 'revisions', 'transcripts');
    return this.tick(copy);
  }

  async updateProject(projectId: string, patch: Partial<DemoProject>) {
    const idx = this.db.projects.findIndex((p) => p.id === projectId);
    if (idx === -1) throw new Error(`Project ${projectId} not found`);
    const updated = { ...this.db.projects[idx], ...patch, id: projectId, updatedAtIso: new Date().toISOString() };
    this.db.projects[idx] = updated;
    this.persist('projects');
    return this.tick(updated);
  }

  async archiveProject(projectId: string) {
    this.db.projects = this.db.projects.filter((p) => p.id !== projectId);
    this.persist('projects');
    await this.tick(null);
  }

  // ---------------- Assets ----------------

  async listAssets(projectId: string) {
    const project = this.db.projects.find((p) => p.id === projectId);
    const ids = new Set(project?.assetIds ?? []);
    return this.tick(this.db.assets.filter((a) => ids.has(a.id) || a.projectId === projectId));
  }

  async listAllAssets() {
    return this.tick(this.db.assets);
  }

  async addMockAsset(asset: Omit<DemoAsset, 'id' | 'isMock'>) {
    const created: DemoAsset = { ...asset, id: newId('asset'), isMock: true };
    this.db.assets.push(created);
    const project = this.db.projects.find((p) => p.id === asset.projectId);
    if (project && !project.assetIds.includes(created.id)) project.assetIds.push(created.id);
    this.persist('assets', 'projects');
    return this.tick(created);
  }

  async addAssetsToProject(projectId: string, assetIds: string[]) {
    const project = this.requireProject(projectId);
    project.assetIds = Array.from(new Set([...project.assetIds, ...assetIds]));
    project.updatedAtIso = new Date().toISOString();
    this.persist('projects');
    return this.tick(project);
  }

  // ---------------- Revisions ----------------

  async listRevisions(projectId: string) {
    return this.tick(this.db.revisions[projectId] ?? []);
  }

  async getRevision(projectId: string, revisionId: string) {
    return this.tick((this.db.revisions[projectId] ?? []).find((r) => r.id === revisionId));
  }

  async saveRevision(projectId: string, revision: DemoRevision) {
    const list = (this.db.revisions[projectId] ??= []);
    const idx = list.findIndex((r) => r.id === revision.id);
    if (idx >= 0) list[idx] = clone(revision);
    else list.push(clone(revision));
    this.persist('revisions');
    return this.tick(revision);
  }

  /** "Create sample edit plan" — lays the project's demo assets onto a 5-track timeline. */
  async createSampleRevision(projectId: string) {
    const project = this.requireProject(projectId);
    const assets = this.db.assets.filter((a) => project.assetIds.includes(a.id));
    const videos = assets.filter((a) => a.kind === 'video');
    const audio = assets.filter((a) => a.kind === 'audio');
    const list = (this.db.revisions[projectId] ??= []);
    const revNumber = list.length + 1;
    const revId = `rev_${projectId}_v${revNumber}`;
    const total = project.targetDurationFrames;
    const t = (k: string) => `trk_${revId}_${k}`;
    const main = videos[0];
    const broll = videos.slice(1);
    const third = Math.floor(total / 3);

    const tracks: DemoTimelineTrack[] = [
      {
        id: t('v2'),
        type: 'broll',
        label: 'V2 B-roll',
        clips: broll.slice(0, 2).map((a, i) => ({
          id: `clip_${revId}_b${i}`,
          trackId: t('v2'),
          assetId: a.id,
          timelineInFrames: third * (i + 1) - 45,
          durationFrames: 90,
          sourceInFrames: 0,
          sourceOutFrames: 90,
          label: a.name,
          rationale: 'Sample plan: cutaway placed at a section boundary (demo rationale).',
        })),
      },
      {
        id: t('v1'),
        type: 'video',
        label: 'V1 Main Video',
        clips: main
          ? [0, 1, 2].map((i) => ({
              id: `clip_${revId}_v${i}`,
              trackId: t('v1'),
              assetId: main.id,
              timelineInFrames: third * i,
              durationFrames: i === 2 ? total - third * 2 : third,
              sourceInFrames: 900 * (i + 1),
              sourceOutFrames: 900 * (i + 1) + (i === 2 ? total - third * 2 : third),
              label: `Segment ${i + 1}`,
              fitMode: 'cover' as const,
            }))
          : [],
      },
      {
        id: t('a1'),
        type: 'dialogue',
        label: 'A1 Dialogue',
        clips: main
          ? [
              {
                id: `clip_${revId}_a1`,
                trackId: t('a1'),
                assetId: main.id,
                timelineInFrames: 0,
                durationFrames: total,
                sourceInFrames: 900,
                sourceOutFrames: 900 + total,
                label: 'Dialogue (from main video)',
              },
            ]
          : [],
      },
      {
        id: t('a2'),
        type: 'music',
        label: 'A2 Music',
        clips: audio[0]
          ? [
              {
                id: `clip_${revId}_m`,
                trackId: t('a2'),
                assetId: audio[0].id,
                timelineInFrames: 0,
                durationFrames: total,
                sourceInFrames: 0,
                sourceOutFrames: total,
                label: audio[0].name,
                volumePercent: 30,
              },
            ]
          : [],
      },
      { id: t('t1'), type: 'captions', label: 'T1 Captions', clips: [] },
    ];

    const revision: DemoRevision = {
      id: revId,
      projectId,
      revisionNumber: revNumber,
      createdAtIso: new Date().toISOString(),
      durationFrames: total,
      tracks,
      notes: 'Sample edit plan assembled in the UI prototype from demo assets. Not an AI edit.',
      changeSummary: 'Sample plan: main video split into three segments with placeholder cutaways.',
    };
    list.push(revision);
    project.activeRevisionId = revId;
    project.updatedAtIso = new Date().toISOString();
    this.persist('revisions', 'projects');
    return this.tick(revision);
  }

  // ---------------- Transcript & QA ----------------

  async getTranscript(projectId: string) {
    return this.tick(this.db.transcripts[projectId] ?? []);
  }

  async getQAItems(projectId: string) {
    const items = this.db.qa[projectId];
    if (items) return this.tick(items);
    // Projects without sample QA show every check as not tested.
    const untested: DemoQAItem[] = [
      ['Output dimensions', 'dimensions'],
      ['Codec profile', 'dimensions'],
      ['Target duration', 'dimensions'],
      ['Integrated loudness', 'audio'],
      ['Caption safe-zone alignment', 'captions'],
      ['Black frames', 'visual'],
    ].map(([title, category], i) => ({
      id: `qa_untested_${i}`,
      title,
      category: category as DemoQAItem['category'],
      state: 'untested',
      detail: 'Not tested: no sample QA data exists for this project.',
    }));
    return this.tick(untested);
  }

  // ---------------- Review notes ----------------

  async getReviewNotes(projectId: string) {
    return this.tick(this.db.notes[projectId] ?? []);
  }

  async addReviewNote(projectId: string, note: Omit<DemoReviewNote, 'id' | 'createdAtIso'>) {
    const created: DemoReviewNote = { ...note, id: newId('note'), createdAtIso: new Date().toISOString() };
    (this.db.notes[projectId] ??= []).push(created);
    this.persist('notes');
    return this.tick(created);
  }

  async updateReviewNote(projectId: string, noteId: string, patch: Partial<DemoReviewNote>) {
    const list = this.db.notes[projectId] ?? [];
    const idx = list.findIndex((n) => n.id === noteId);
    if (idx === -1) throw new Error(`Note ${noteId} not found`);
    list[idx] = { ...list[idx], ...patch, id: noteId };
    this.persist('notes');
    return this.tick(list[idx]);
  }

  async removeReviewNote(projectId: string, noteId: string) {
    this.db.notes[projectId] = (this.db.notes[projectId] ?? []).filter((n) => n.id !== noteId);
    this.persist('notes');
    await this.tick(null);
  }

  // ---------------- Jobs (simulations only) ----------------

  async listJobs(projectId?: string) {
    return this.tick(projectId ? this.db.jobs.filter((j) => j.projectId === projectId) : this.db.jobs);
  }

  async createSimulationJob(projectId: string, kind: JobKind, message?: string) {
    const job: DemoJob = {
      id: newId('job'),
      projectId,
      kind,
      status: 'queued',
      progressPercent: 0,
      isSimulation: true,
      message: message ?? `Simulation: ${kind} job queued. Nothing will be rendered or written.`,
      createdAtIso: new Date().toISOString(),
    };
    this.db.jobs.unshift(job);
    this.persist('jobs');
    return this.tick(job);
  }

  async updateJob(jobId: string, patch: Partial<DemoJob>) {
    const idx = this.db.jobs.findIndex((j) => j.id === jobId);
    if (idx === -1) throw new Error(`Job ${jobId} not found`);
    this.db.jobs[idx] = { ...this.db.jobs[idx], ...patch, id: jobId, isSimulation: true };
    this.persist('jobs');
    return this.tick(this.db.jobs[idx]);
  }

  async resetDemoData() {
    this.persistAll(fixtureDb());
    await this.tick(null);
  }
}
