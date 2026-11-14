/**
 * ProjectRepository — the single seam between screens and project data.
 *
 * Screens depend ONLY on this interface. Today it is implemented by
 * MockProjectRepository (src/mocks/mockProjectRepository.ts) which keeps demo
 * fixtures in localStorage. Later a TauriProjectRepository will implement the
 * same interface over typed desktop IPC (see README → "Integration seam").
 *
 *   listProjects       → IPC project.list
 *   createProject      → IPC project.create
 *   listAssets         → IPC asset.list
 *   addAssetsToProject → native file dialog + IPC asset.import
 *   getRevision        → IPC timeline.get
 *   saveRevision       → IPC timeline.commit (optimistic revision check)
 *   getTranscript      → IPC transcript.get
 *   getQAItems         → IPC qa.get
 *   createSimulationJob→ IPC render.preview.start / export.start
 *   listJobs/updateJob → IPC job.get / job event subscription
 */
import type {
  DemoAsset,
  DemoJob,
  DemoProject,
  DemoQAItem,
  DemoReviewNote,
  DemoRevision,
  DemoTranscriptUtterance,
  JobKind,
  NewProjectInput,
} from '../types';

export interface ProjectRepository {
  listProjects(): Promise<DemoProject[]>;
  getProject(projectId: string): Promise<DemoProject | undefined>;
  createProject(input: NewProjectInput): Promise<DemoProject>;
  duplicateProject(projectId: string): Promise<DemoProject>;
  updateProject(projectId: string, patch: Partial<DemoProject>): Promise<DemoProject>;
  archiveProject(projectId: string): Promise<void>;

  listAssets(projectId: string): Promise<DemoAsset[]>;
  listAllAssets(): Promise<DemoAsset[]>;
  addMockAsset(asset: Omit<DemoAsset, 'id' | 'isMock'>): Promise<DemoAsset>;
  addAssetsToProject(projectId: string, assetIds: string[]): Promise<DemoProject>;

  listRevisions(projectId: string): Promise<DemoRevision[]>;
  getRevision(projectId: string, revisionId: string): Promise<DemoRevision | undefined>;
  saveRevision(projectId: string, revision: DemoRevision): Promise<DemoRevision>;
  createSampleRevision(projectId: string): Promise<DemoRevision>;

  getTranscript(projectId: string): Promise<DemoTranscriptUtterance[]>;
  getQAItems(projectId: string): Promise<DemoQAItem[]>;

  getReviewNotes(projectId: string): Promise<DemoReviewNote[]>;
  addReviewNote(projectId: string, note: Omit<DemoReviewNote, 'id' | 'createdAtIso'>): Promise<DemoReviewNote>;
  updateReviewNote(projectId: string, noteId: string, patch: Partial<DemoReviewNote>): Promise<DemoReviewNote>;
  removeReviewNote(projectId: string, noteId: string): Promise<void>;

  listJobs(projectId?: string): Promise<DemoJob[]>;
  createSimulationJob(projectId: string, kind: JobKind, message?: string): Promise<DemoJob>;
  updateJob(jobId: string, patch: Partial<DemoJob>): Promise<DemoJob>;

  /** Demo-only: restore deterministic fixtures. Never a production action. */
  resetDemoData(): Promise<void>;
}
