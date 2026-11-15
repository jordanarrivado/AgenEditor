/** Typed Tauri IPC seam for the live local engine. Screens never call invoke directly. */

import { convertFileSrc, invoke } from '@tauri-apps/api/core';
import { listen } from '@tauri-apps/api/event';
import type { AssetChecklist } from '../utils/assetChecklist';

export interface EngineAssetChecklist extends AssetChecklist {
  schema_version: 1;
  project_id: string;
  version: number;
  configured: boolean;
}

export interface EngineBriefItem {
  id: string;
  text: string;
  status: 'open' | 'met' | 'waived';
  note: string;
}

export interface EngineBrief {
  schema_version: 1;
  project_id: string;
  version: number;
  brief_text: string;
  script_text: string;
  reference_note: string;
  checklist: EngineBriefItem[];
}

export interface EngineProject {
  id: string;
  name: string;
  created_at: string;
  current_revision: number;
}

export type SoundLicense = 'cc0' | 'royalty_free' | 'cc_by' | 'other';

/** An installed sound-effect pack, without its sound list. */
export interface SoundPackSummary {
  id: string;
  name: string;
  license: SoundLicense;
  credit: string;
  source: string;
  catalog_id: string | null;
  active: boolean;
  installed_at: string;
  sound_count: number;
}

/** A free CC0 pack from AgenEditor's catalog; bundled with the app or downloaded on request. */
export interface SoundCatalogEntry {
  id: string;
  name: string;
  description: string;
  author: string;
  license: SoundLicense;
  page_url: string;
  download_url: string;
  sha256: string;
  size_bytes: number;
  sound_count: number;
  bundled_file: string | null;
  installed_pack_id: string | null;
}

export interface SoundPackInstallResult { pack: SoundPackSummary; skipped: { file: string; reason: string }[] }

export type TransitionStyleId = 'classic' | 'modern' | 'montage' | 'cinematic';
/** A built-in transition style (FFmpeg xfade presets) and whether Claude may use it. */
export interface TransitionStyleSummary {
  id: TransitionStyleId;
  label: string;
  description: string;
  active: boolean;
  count: number;
  examples: string[];
}

export interface SoundPackDetails { name: string; license: SoundLicense; credit: string; source: string }

export interface EngineAsset {
  id: string;
  role: 'primary' | 'broll' | 'music' | 'logo' | 'subtitles' | 'reference' | 'sfx';
  kind: string;
  original_filename: string;
  sha256: string;
  size_bytes: number;
  provenance: {
    origin: 'original' | 'generated-external' | 'sound-library';
    tool_name: string | null;
    library?: { pack_id: string; pack_name: string; sound_id: string; license: SoundLicense; credit: string; source: string };
  };
  video: { width: number; height: number; duration_frames: number; frame_rate: { num: number; den: number } } | null;
  audio: { channels: number; sample_rate: number } | null;
}

export interface EngineClip {
  clip_id: string;
  asset_id: string;
  track: 'V1' | 'V2' | 'A1' | 'A2';
  source_in_frames: number;
  source_out_frames: number;
  timeline_in_frames: number;
  timeline_duration_frames?: number | null;
  rationale?: string | null;
  [key: string]: unknown;
}

export interface EngineTimeline {
  project_id: string;
  revision: number;
  target_duration_frames: number;
  render_profile: { width: number; height: number; frame_rate: { num: number; den: number } };
  clips: EngineClip[];
  captions: unknown[];
  provenance: { author: string; summary: string; [key: string]: unknown };
  [key: string]: unknown;
}

export interface EngineJob {
  id: string;
  project_id: string;
  kind: string;
  status: 'queued' | 'running' | 'succeeded' | 'failed' | 'canceled' | 'interrupted';
  progress: number;
  timeline_revision: number | null;
  output_path: string | null;
  error: { code: string; message: string } | null;
  created_at: string;
}

export interface EngineQA {
  overall: 'pass' | 'warn' | 'fail';
  checks: { check_id: string; status: string; expected?: string; measured?: string; message: string }[];
  generated_assets: { asset_id: string; original_filename: string; tool_name: string }[];
}

export interface EngineConnection { workspace: string; command: string; connected: boolean; claude_available?: boolean }

/** Which import box a file was added to. The shell maps it to an asset role. */
export type ImportBucket = 'main' | 'reference' | 'asset';
export type ImportOutcome = { file: string; asset: EngineAsset; error?: undefined } | { file: string; asset?: undefined; error: string };

export type AgentEvent = { kind: 'text'; text: string } | { kind: 'tool'; name: string };
/** Which step of Plan -> Execute -> Review a Claude run is; the shell picks the model from it. */
export type AgentStage = 'plan' | 'execute' | 'review';
export interface AgentResult {
  ok: boolean;
  stopped: string | null;
  text: string | null;
  cost_usd?: number | null;
  stage?: AgentStage;
  model?: string;
}

/** Claude Code on this PC, as checked by the shell. No email or organisation is included. */
export interface ClaudeSetupStatus {
  installed: boolean;
  /** Set when a Claude Code file exists but AgenEditor refuses to run it (e.g. not signed by Anthropic). */
  problem?: string | null;
  version: string | null;
  signed_in: boolean;
  plan: string | null;
  method: string | null;
}
export type SpeechModelStatus = Record<'tiny' | 'base' | 'small', boolean>;

/** One Claude plan rate-limit window, as Claude Code reports it (utilization 0..1). */
export interface UsageWindow { utilization: number; resets_at: number | null; status: string }
/** The latest plan usage Claude Code reported, with when it was captured (unix seconds). */
export interface ClaudeUsage {
  status: string;
  five_hour: UsageWindow | null;
  seven_day: UsageWindow | null;
  using_overage: boolean;
  captured_at: number;
  last_run?: { stage: AgentStage; model: string; cost_usd: number | null; duration_ms: number | null; finished_at: number };
}

export interface LiveEngineRepository {
  listProjects(): Promise<EngineProject[]>;
  createProject(name: string, targetDurationFrames: number): Promise<{ project_id: string }>;
  summary(projectId: string): Promise<EngineProject & { assets: EngineAsset[] }>;
  importMedia(projectId: string, role: EngineAsset['role'], toolName?: string): Promise<EngineAsset | null>;
  /** Imports the file last dropped on the window (path held by the native shell, never the page). */
  importDroppedMedia(projectId: string, role: EngineAsset['role'], toolName?: string): Promise<EngineAsset>;
  /** Native multi-select picker. Each file imports on its own; null when the picker is cancelled. */
  importFiles(projectId: string, bucket: ImportBucket, toolName?: string): Promise<ImportOutcome[] | null>;
  /** Imports every file of the latest window drop into one box. */
  importDroppedFiles(projectId: string, bucket: ImportBucket, toolName?: string): Promise<ImportOutcome[]>;
  listSoundPacks(): Promise<SoundPackSummary[]>;
  /** Opens a folder picker and installs the sounds in it. Null when the picker is cancelled. */
  installSoundPack(details: SoundPackDetails): Promise<SoundPackInstallResult | null>;
  listSoundCatalog(): Promise<SoundCatalogEntry[]>;
  downloadSoundPack(catalogId: string): Promise<SoundPackInstallResult>;
  setSoundPackActive(packId: string, active: boolean): Promise<SoundPackSummary>;
  removeSoundPack(packId: string): Promise<{ removed: string }>;
  listTransitionStyles(): Promise<TransitionStyleSummary[]>;
  setTransitionStyleActive(style: TransitionStyleId, active: boolean): Promise<TransitionStyleSummary[]>;
  /** Runs Claude Code as the editor for one prompt. Follow-up prompts continue the conversation. */
  runAgent(projectId: string, prompt: string, stage: AgentStage): Promise<AgentResult>;
  /** Opus 5.5 rewrites rough standing instructions; returns a suggestion, never saves it. */
  polishInstructions(text: string): Promise<string>;
  claudeStatus(): Promise<ClaudeSetupStatus>;
  /** Runs Anthropic's official installer (claude.ai/install.ps1) and checks the signature. */
  installClaudeCode(): Promise<ClaudeSetupStatus>;
  /** Opens `claude auth login` in its own window; poll claudeStatus for the result. */
  openClaudeSignIn(): Promise<void>;
  speechModelStatus(): Promise<SpeechModelStatus>;
  /** Latest plan usage seen in a Claude run, or null before the first one. */
  claudeUsage(): Promise<ClaudeUsage | null>;
  /** Sends one tiny Haiku request to read current plan usage. */
  refreshClaudeUsage(): Promise<ClaudeUsage>;
  onClaudeUsage(handler: (usage: ClaudeUsage) => void): Promise<() => void>;
  /** Takes a file out of the project (hidden, not deleted); refused while the current edit uses it. */
  removeAsset(projectId: string, assetId: string): Promise<{ removed: string }>;
  stopAgent(): Promise<boolean>;
  newAgentConversation(projectId: string): Promise<void>;
  onAgentEvent(handler: (projectId: string, event: AgentEvent) => void): Promise<() => void>;
  /** Native file-drop notification in CSS pixels. Resolves to an unsubscribe function. */
  onMediaDropped(handler: (drop: { count: number; x: number; y: number }) => void): Promise<() => void>;
  getChecklist(projectId: string): Promise<EngineAssetChecklist>;
  saveChecklist(projectId: string, checklist: AssetChecklist | null, expectedVersion: number): Promise<EngineAssetChecklist>;
  linkChecklistAsset(projectId: string, requestId: string, assetId: string, expectedVersion: number): Promise<EngineAssetChecklist>;
  /** Records (or, with a null note, clears) a person's decision to use a linked file despite a known issue. */
  acceptChecklistIssue(projectId: string, requestId: string, note: string | null, expectedVersion: number): Promise<EngineAssetChecklist>;
  /** Records the person's approval of the file linked to a request. */
  approveChecklistRequest(projectId: string, requestId: string, expectedVersion: number): Promise<EngineAssetChecklist>;
  /** Swaps a request's file for a new one in one step; on failure the old file and approval stay. */
  replaceChecklistAsset(projectId: string, requestId: string, assetId: string, expectedVersion: number): Promise<EngineAssetChecklist>;
  /** Clears a request's file, approval and skip so a new file can be added. */
  resetChecklistRequest(projectId: string, requestId: string, expectedVersion: number): Promise<EngineAssetChecklist>;
  /** Opens ChatGPT or Google Flow in the browser. The shell accepts only these two names. */
  openGenerator(site: 'chatgpt' | 'flow'): Promise<void>;
  getBrief(projectId: string): Promise<EngineBrief>;
  saveBrief(projectId: string, brief: Omit<EngineBrief, 'schema_version' | 'project_id' | 'version'>, expectedVersion: number): Promise<EngineBrief>;
  timeline(projectId: string, revision?: number): Promise<EngineTimeline>;
  validate(projectId: string, timeline: EngineTimeline): Promise<{ valid: boolean; issues: { message: string; severity: string }[] }>;
  commit(projectId: string, timeline: EngineTimeline, expectedRevision: number): Promise<EngineTimeline>;
  range(projectId: string, assetId: string, sourceIn: number, sourceOut: number, revision: number): Promise<{ output_frames: number }>;
  listJobs(projectId: string): Promise<EngineJob[]>;
  startRender(projectId: string, revision: number, final: boolean): Promise<EngineJob>;
  cancelJob(projectId: string, jobId: string): Promise<EngineJob>;
  runQA(projectId: string, jobId: string): Promise<EngineQA>;
  getQA(projectId: string, jobId: string): Promise<EngineQA>;
  mediaUrl(projectId: string, jobId: string): Promise<string>;
  assetUrl(projectId: string, assetId: string): Promise<string>;
  saveExport(projectId: string, jobId: string): Promise<string | null>;
  connection(): Promise<EngineConnection>;
  cacheModel(preset: 'tiny' | 'base' | 'small'): Promise<string>;
  transcribe(projectId: string, assetId: string): Promise<EngineJob>;
  transcripts(projectId: string): Promise<{ id: string; asset_id: string; word_count: number; language: string }[]>;
  transcript(projectId: string, transcriptId: string): Promise<{ words: { id: string; text: string; start_us: number; end_us: number }[]; total_words: number }>;
  submitPlan(projectId: string, plan: unknown, expectedRevision: number): Promise<{ valid: boolean; issues: { message: string; severity: string }[]; candidate_timeline: EngineTimeline | null }>;
}

const desktop = <T>(operation: string, args: Record<string, unknown> = {}) =>
  invoke<T>('engine_desktop', { operation, args });
const tool = <T>(name: string, args: Record<string, unknown>) => invoke<T>('engine_tool', { tool: name, args });

export const liveEngineRepository: LiveEngineRepository = {
  listProjects: () => desktop('list_projects'),
  createProject: (name, targetDurationFrames) => desktop('create_project', {
    name, target_duration_frames: targetDurationFrames,
  }),
  summary: (projectId) => desktop('project_summary', { project_id: projectId }),
  importMedia: (projectId, role, toolName) => invoke('import_media', { projectId, role, toolName }),
  importDroppedMedia: (projectId, role, toolName) => invoke('import_dropped_media', { projectId, role, toolName }),
  importFiles: (projectId, bucket, toolName) => invoke('import_files', { projectId, bucket, toolName }),
  importDroppedFiles: (projectId, bucket, toolName) => invoke('import_dropped_files', { projectId, bucket, toolName }),
  listSoundPacks: () => desktop('list_sound_packs'),
  installSoundPack: (details) => invoke('install_sound_pack', { details }),
  listSoundCatalog: () => desktop('list_sound_catalog'),
  downloadSoundPack: (catalogId) => invoke('download_sound_pack', { catalogId }),
  setSoundPackActive: (packId, active) => desktop('set_active_sound_pack', { pack_id: packId, active }),
  removeSoundPack: (packId) => desktop('remove_sound_pack', { pack_id: packId }),
  listTransitionStyles: () => desktop('list_transition_styles'),
  setTransitionStyleActive: (style, active) => desktop('set_active_transition_style', { style, active }),
  runAgent: (projectId, prompt, stage) => invoke('run_agent', { projectId, prompt, stage }),
  polishInstructions: (text) => invoke('polish_instructions', { text }),
  claudeStatus: () => invoke('claude_status'),
  installClaudeCode: () => invoke('install_claude_code'),
  openClaudeSignIn: () => invoke('open_claude_sign_in'),
  speechModelStatus: () => desktop('speech_model_status'),
  claudeUsage: () => invoke('claude_usage'),
  refreshClaudeUsage: () => invoke('refresh_claude_usage'),
  onClaudeUsage: (handler) => listen<ClaudeUsage>('claude-usage', (event) => handler(event.payload)),
  removeAsset: (projectId, assetId) => desktop('remove_asset', { project_id: projectId, asset_id: assetId }),
  stopAgent: () => invoke('stop_agent'),
  newAgentConversation: (projectId) => invoke('new_agent_conversation', { projectId }),
  onAgentEvent: (handler) => listen<{ project_id: string; event: AgentEvent }>('agent-event', (event) => {
    handler(event.payload.project_id, event.payload.event);
  }),
  onMediaDropped: (handler) => listen<{ count: number; x: number; y: number }>('media-dropped', (event) => {
    const ratio = window.devicePixelRatio || 1; // the shell reports physical pixels
    handler({ count: event.payload.count, x: event.payload.x / ratio, y: event.payload.y / ratio });
  }),
  getChecklist: (projectId) => desktop('get_asset_checklist', { project_id: projectId }),
  saveChecklist: (projectId, checklist, expectedVersion) => desktop('save_asset_checklist', {
    project_id: projectId,
    checklist: {
      configured: checklist !== null, requests: checklist?.requests ?? [], links: checklist?.links ?? {}, accepted: checklist?.accepted ?? {},
      review_required: checklist?.review_required ?? false, approved: checklist?.approved ?? [],
    },
    expected_version: expectedVersion,
  }),
  linkChecklistAsset: (projectId, requestId, assetId, expectedVersion) => desktop('link_asset_request', {
    project_id: projectId, request_id: requestId, asset_id: assetId, expected_version: expectedVersion,
  }),
  approveChecklistRequest: (projectId, requestId, expectedVersion) => desktop('approve_asset_request', {
    project_id: projectId, request_id: requestId, expected_version: expectedVersion,
  }),
  replaceChecklistAsset: (projectId, requestId, assetId, expectedVersion) => desktop('replace_asset_request_link', {
    project_id: projectId, request_id: requestId, asset_id: assetId, expected_version: expectedVersion,
  }),
  resetChecklistRequest: (projectId, requestId, expectedVersion) => desktop('reset_asset_request', {
    project_id: projectId, request_id: requestId, expected_version: expectedVersion,
  }),
  openGenerator: (site) => invoke('open_generator', { site }),
  getBrief: (projectId) => desktop('get_brief', { project_id: projectId }),
  saveBrief: (projectId, brief, expectedVersion) => desktop('save_brief', {
    project_id: projectId, brief, expected_version: expectedVersion,
  }),
  acceptChecklistIssue: (projectId, requestId, note, expectedVersion) => desktop('accept_asset_issue', {
    project_id: projectId, request_id: requestId, note, expected_version: expectedVersion,
  }),
  timeline: (projectId, revision) => tool('get_timeline', { project_id: projectId, revision: revision ?? null }),
  validate: (projectId, timeline) => tool('validate_timeline', { project_id: projectId, timeline }),
  commit: (projectId, timeline, expectedRevision) => tool('commit_timeline', {
    project_id: projectId, timeline, expected_revision: expectedRevision,
  }),
  range: (projectId, assetId, sourceIn, sourceOut, revision) => desktop('source_range', {
    project_id: projectId, asset_id: assetId, source_in_frames: sourceIn,
    source_out_frames: sourceOut, revision,
  }),
  listJobs: (projectId) => desktop('list_jobs', { project_id: projectId }),
  startRender: (projectId, revision, final) => tool('start_render', { project_id: projectId, revision, final }),
  cancelJob: (projectId, jobId) => tool('cancel_job', { project_id: projectId, job_id: jobId }),
  runQA: (projectId, jobId) => tool('run_qa', { project_id: projectId, job_id: jobId }),
  getQA: (projectId, jobId) => tool('get_qa_report', { project_id: projectId, job_id: jobId }),
  mediaUrl: async (projectId, jobId) => convertFileSrc((await desktop<{ path: string }>('render_path', {
    project_id: projectId, job_id: jobId,
  })).path),
  assetUrl: async (projectId, assetId) => convertFileSrc((await desktop<{ path: string }>('asset_path', {
    project_id: projectId, asset_id: assetId,
  })).path),
  saveExport: (projectId, jobId) => invoke('export_media', { projectId, jobId }),
  connection: () => invoke('engine_connection'),
  cacheModel: (preset) => invoke('cache_model', { preset }),
  transcribe: (projectId, assetId) => tool('transcribe_asset', {
    project_id: projectId, asset_id: assetId, model_preset: 'tiny',
  }),
  transcripts: (projectId) => desktop('list_transcripts', { project_id: projectId }),
  transcript: (projectId, transcriptId) => tool('get_transcript', {
    project_id: projectId, transcript_id: transcriptId, start_word: 0, limit: 300, context_words: 0,
  }),
  submitPlan: (projectId, plan, expectedRevision) => tool('submit_edit_plan', {
    project_id: projectId, plan, expected_revision: expectedRevision,
  }),
};
