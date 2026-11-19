/** Typed IPC seam for the AgenEditor 2.0 Production Command Center (spec §16, §17).
 *
 * Every operation here is a human-authorized desktop operation. Agents never reach these: they only get
 * the scoped production tools, authorized per task by the engine. */

import { convertFileSrc, invoke } from '@tauri-apps/api/core';

export type ClipStatus =
  | 'discovered' | 'planned' | 'waiting_for_assets' | 'ready' | 'editing' | 'preview_rendering'
  | 'technical_qa' | 'editorial_qa' | 'needs_revision' | 'review_ready' | 'human_approved' | 'exported'
  | 'delivered' | 'blocked' | 'retry_wait' | 'needs_human' | 'failed' | 'cancelled';
export type TaskStatus = 'created' | 'blocked' | 'ready' | 'leased' | 'running' | 'succeeded' | 'retry_wait'
  | 'needs_human' | 'failed' | 'cancelled';
export type BatchStatus = 'created' | 'discovering' | 'awaiting_selection' | 'producing' | 'completed' | 'cancelled';
export type CostKind = 'measured' | 'provider_reported' | 'estimated' | 'unknown';

export interface RenderProfileLite { id: string; width: number; height: number; frame_rate: { num: number; den: number } }

export interface BatchPolicy {
  target_mode: 'auto' | 'count';
  target_count: number | null;
  min_duration_us: number;
  max_duration_us: number;
  platform?: string;
  audience?: string;
  exclusions?: string[];
  output_profile?: RenderProfileLite;
  priority?: number;
  deadline?: string | null;
  selection: 'human' | 'auto_top';
  editorial_qa: 'required' | 'skip';
  allow_data_egress: boolean;
  budget_micros: number | null;
  allow_partial_delivery?: boolean | null;
  [key: string]: unknown;
}

export interface Batch {
  id: string;
  project_id: string;
  client_id: string | null;
  name: string;
  sequence: number;
  status: BatchStatus;
  paused: boolean;
  policy: BatchPolicy;
  created_at: string;
}

export interface BatchSummary {
  batch: Batch;
  project_name: string;
  clip_counts: Partial<Record<ClipStatus, number>>;
  clips_total: number;
  attention: number;
  review_ready: number;
  tasks_active: number;
  tasks_waiting: number;
}

export interface PoolState { capacity: number; in_use: number; holders: string[] }
export interface ResourceSnapshot {
  pools: Record<string, PoolState>;
  cpu_count: number | null;
  memory_total_bytes: number | null;
  memory_available_bytes: number | null;
  disk_free_bytes: number | null;
  /** CPU busy share since the previous engine sample, in permille; null until measured. */
  cpu_busy_permille?: number | null;
  measured_at: string;
}
export interface RunningWork {
  attempt_id: string; task_id: string; type: string; clip_id: string | null; batch_id: string;
  adapter: string; model: string | null; provider_id: string | null; started_at: string;
}
export interface TeamAssignment { team_id: string; project_id: string; clip_id: string; batch_id: string; assigned_at: string }
export interface Overview { batches: BatchSummary[]; running: RunningWork[]; teams: TeamAssignment[]; resources: ResourceSnapshot }

export interface Candidate {
  id: string;
  rank: number;
  topic: string;
  hook_text: string;
  quote: string;
  duration_us: number;
  overall_score: number;
  narrative_complete: boolean;
  method: 'deterministic' | 'agent';
  caveats: string[];
  status: 'proposed' | 'selected' | 'rejected';
}

export interface ProductionClip {
  id: string;
  batch_id: string;
  title: string;
  ordinal: number;
  status: ClipStatus;
  status_reason: string;
  /** Set once planning finished; clip instructions are fixed from then on. */
  plan_id?: string | null;
  instructions?: string;
  current_revision: number | null;
  draft_revision: number | null;
  team_id: string | null;
  updated_at: string;
}

export interface ProductionTask {
  id: string;
  clip_id: string | null;
  type: string;
  cycle: number;
  status: TaskStatus;
  block_reason: string | null;
  attempt_count: number;
  max_attempts: number;
  not_before: string | null;
  result: Record<string, unknown> | null;
  error: { code: string; message: string } | null;
  created_at: string;
  updated_at: string;
}

export interface UsageRow {
  provider_id: string; model: string; tokens_in: number | null; tokens_out: number | null;
  cost_micros: number | null; cost_kind: CostKind; task_type: string; batch_id: string; created_at: string;
}

export interface DeliveryManifest {
  id: string; package_name: string; partial: boolean; authorized_by: string; created_at: string;
  clips: { clip_id: string; title: string; video: string; captions: string | null }[];
  files: { path: string; sha256: string; size_bytes: number }[];
  notice: string;
  folder?: string;
}

export interface BatchStatusDetail {
  batch: Batch;
  clips: ProductionClip[];
  tasks: ProductionTask[];
  candidates: Candidate[];
  task_counts: Partial<Record<TaskStatus, number>>;
  clip_counts: Partial<Record<ClipStatus, number>>;
  usage: UsageRow[];
  deliveries: DeliveryManifest[];
}

export interface Finding {
  id: string; severity: 'blocker' | 'major' | 'minor' | 'info'; category: string; evidence: string;
  suggested_correction: string; confidence: number; state: string; source: 'ai' | 'human' | 'technical';
  review_revision: number; reference: { kind: string; id?: string | null };
}
export interface Review {
  id: string; timeline_revision: number; reviewer: string; status: string; verdict: string | null;
  summary: string; evidence_basis: string[]; created_at: string;
}
export interface Artifact { id: string; kind: string; sha256: string; timeline_revision: number | null; path: string }
export interface ClipDetail {
  clip: ProductionClip;
  reviews: Review[];
  findings: Finding[];
  approvals: { id: string; timeline_revision: number; render_sha256: string; approved_by: string; approved_at: string; revoked_at: string | null; revoked_reason: string }[];
  artifacts: Artifact[];
  revisions: { revision: number; attempt_id: string | null; created_at: string }[];
}

export interface ProviderInfo {
  id: string; label: string; adapter_type: string; enabled: boolean; adapter_ready: boolean; adapter_reason: string;
  sends_data_off_device: boolean; models: { model: string; task_types: string[]; quality: number }[];
}
export interface ResourceConfig {
  team_slots: number; analysis_slots: number; render_slots: number; qa_slots: number;
  lease_seconds: number; host_timeout_seconds: number;
}
export interface Client { id: string; name: string; status: string }

export type CaptionPreset = 'clean_white' | 'bold_yellow' | 'boxed';
export type ColorPreset = 'none' | 'natural' | 'warm' | 'cool';
export type Pacing = 'calm' | 'balanced' | 'fast';
/** Overridable creative defaults; `null` inherits from the layer above. */
export interface CreativeDefaults {
  caption_preset: CaptionPreset | null;
  color_preset: ColorPreset | null;
  captions: boolean | null;
  normalize_audio: boolean | null;
  pacing: Pacing | null;
  tone: string | null;
  platform: string | null;
}
export type CreativeField = keyof CreativeDefaults;
export type ProfileScope = 'global' | 'client';
export interface DeliveryRules {
  allow_partial_delivery: boolean;
  include_captions: boolean;
  include_project_snapshot: boolean;
  naming_prefix: string | null;
}
/** A versioned instruction layer. Saving appends a new immutable revision. */
export interface InstructionProfile {
  schema_version: 1;
  scope: ProfileScope;
  client_id: string | null;
  /** Optimistic-lock counter; 0 means nothing saved yet. */
  revision: number;
  creative: CreativeDefaults;
  instructions: string;
  required_constraints: string[];
  locked_creative: CreativeField[];
  style_observations: { id: string; label: string; value: string; basis: 'measured' | 'estimate' | 'person'; source_asset_id: string | null }[];
  delivery: DeliveryRules;
}

export interface DurationStats { samples: number; mean_ms: number | null; p50_ms: number | null; p95_ms: number | null; max_ms: number | null }
/** Spec §20 indicators for one batch, from engine records only. Empty values were not observed. */
export interface ProductionMetrics {
  batch_id: string;
  measured_at: string;
  batch_created_at: string;
  clips_total: number;
  clips_reached_review: number;
  clips_approved: number;
  clips_delivered: number;
  first_review_ready_ms: number | null;
  last_approval_ms: number | null;
  queue_wait: DurationStats;
  attempt_run: DurationStats;
  attempts_total: number;
  attempts_unsuccessful: number;
  tasks_total: number;
  tasks_succeeded: number;
  tasks_failed: number;
  checkpoint_reuses: number;
  revision_tasks: number;
  cost: { measured_micros: number; provider_reported_micros: number; estimated_micros: number; unpriced_requests: number };
  pressure: ResourcePressure;
}

/** Machine pressure sampled during the batch (permille of total). */
export interface ResourcePressure {
  samples: number;
  cpu_busy_mean_permille: number | null;
  cpu_busy_max_permille: number | null;
  memory_used_mean_permille: number | null;
  memory_used_max_permille: number | null;
  memory_available_min_bytes: number | null;
  disk_free_min_bytes: number | null;
  pool_peak_in_use: Record<string, number>;
  pool_full_samples: Record<string, number>;
}

export type StyleObservation = InstructionProfile['style_observations'][number];
export interface ReferenceAsset { id: string; name: string; kind: string; duration_us: number | null }

/** Result of a change long-poll: a new cursor, and whether state changed since the one sent. */
export interface ChangeNotice { cursor: string; changed: boolean }

export interface NewBatch {
  project_id: string;
  name: string;
  instructions?: string;
  policy: Partial<BatchPolicy>;
}

export interface ProductionRepository {
  /** True when every value shown is sample data (browser Demo Mode). */
  readonly simulated: boolean;
  overview(): Promise<Overview>;
  status(batchId: string): Promise<BatchStatusDetail>;
  clipDetail(clipId: string): Promise<ClipDetail>;
  createBatch(batch: NewBatch): Promise<{ batch: Batch; conflicts: { field: string; message: string }[] }>;
  startDiscovery(batchId: string): Promise<Batch>;
  confirmSelection(batchId: string, candidateIds: string[], confirmedBy: string): Promise<ProductionClip[]>;
  setPaused(batchId: string, paused: boolean): Promise<Batch>;
  cancelBatch(batchId: string): Promise<Batch>;
  approve(clipIds: string[], approvedBy: string, note?: string): Promise<unknown[]>;
  requestRevision(clipId: string, note: string, requestedBy: string): Promise<{ task_ids: string[] }>;
  acceptFindings(clipId: string, decidedBy: string, note: string): Promise<ProductionClip>;
  retryTask(taskId: string): Promise<ProductionTask>;
  packageDelivery(batchId: string, clipIds: string[], authorizedBy: string): Promise<DeliveryManifest>;
  costs(batchId?: string): Promise<UsageRow[]>;
  metrics(batchId: string): Promise<ProductionMetrics>;
  /** Long-poll: resolves when production state differs from `cursor`, or after about `waitMs`. */
  waitForChanges(cursor: string | null, waitMs: number): Promise<ChangeNotice>;
  /** A project's reference files that can be measured for style. */
  referenceAssets(projectId: string): Promise<ReferenceAsset[]>;
  /** Measured, source-cited style notes from one reference file (format, shot structure, loudness). */
  measureReference(projectId: string, assetId: string): Promise<StyleObservation[]>;
  providers(): Promise<ProviderInfo[]>;
  setProviderEnabled(providerId: string, enabled: boolean): Promise<ProviderInfo>;
  resources(): Promise<{ config: ResourceConfig; snapshot: ResourceSnapshot }>;
  saveResources(config: ResourceConfig): Promise<ResourceConfig>;
  clients(): Promise<Client[]>;
  createClient(name: string): Promise<Client>;
  bindProject(projectId: string, clientId: string): Promise<unknown>;
  /** Which client each bound project belongs to (`project_id → client_id`). */
  projectClients(): Promise<Record<string, string>>;
  getProfile(scope: ProfileScope, clientId?: string | null): Promise<InstructionProfile>;
  /** Fails with a revision conflict when someone saved since `expectedRevision`. */
  saveProfile(profile: InstructionProfile, expectedRevision: number): Promise<InstructionProfile>;
  /** Clip-level instructions; the engine accepts them only until the clip is planned. */
  setClipInstructions(clipId: string, instructions: string, creative: Partial<CreativeDefaults>): Promise<ProductionClip>;
  /** A playable URL for a verified render on this computer (null in Demo Mode). */
  fileUrl(path: string): string | null;
}

const desktop = <T>(operation: string, args: Record<string, unknown> = {}) =>
  invoke<T>('engine_desktop', { operation, args });

export const liveProductionRepository: ProductionRepository = {
  simulated: false,
  overview: () => desktop('production_overview'),
  status: (batchId) => desktop('production_status', { batch_id: batchId }),
  clipDetail: (clipId) => desktop('production_clip_detail', { clip_id: clipId }),
  createBatch: (batch) => desktop('production_create_batch', { request: batch }),
  startDiscovery: (batchId) => desktop('production_start_discovery', { batch_id: batchId }),
  confirmSelection: (batchId, candidateIds, confirmedBy) => desktop('production_confirm_selection', {
    batch_id: batchId, candidate_ids: candidateIds, confirmed_by: confirmedBy,
  }),
  setPaused: (batchId, paused) => desktop('production_set_paused', { batch_id: batchId, paused }),
  cancelBatch: (batchId) => desktop('production_cancel_batch', { batch_id: batchId }),
  approve: (clipIds, approvedBy, note = '') => desktop('production_approve', { clip_ids: clipIds, approved_by: approvedBy, note }),
  requestRevision: (clipId, note, requestedBy) => desktop('production_request_revision', {
    clip_id: clipId, note, requested_by: requestedBy,
  }),
  acceptFindings: (clipId, decidedBy, note) => desktop('production_accept_findings', { clip_id: clipId, decided_by: decidedBy, note }),
  retryTask: (taskId) => desktop('production_retry_task', { task_id: taskId }),
  packageDelivery: (batchId, clipIds, authorizedBy) => desktop('production_package_delivery', {
    batch_id: batchId, clip_ids: clipIds, authorized_by: authorizedBy,
  }),
  costs: (batchId) => desktop('production_costs', batchId ? { batch_id: batchId } : {}),
  metrics: (batchId) => desktop('production_metrics', { batch_id: batchId }),
  waitForChanges: (cursor, waitMs) => desktop('production_changes', { cursor, wait_ms: waitMs }),
  referenceAssets: (projectId) => desktop('production_reference_assets', { project_id: projectId }),
  measureReference: (projectId, assetId) => desktop('production_measure_reference', { project_id: projectId, asset_id: assetId }),
  providers: () => desktop('production_list_providers'),
  setProviderEnabled: (providerId, enabled) => desktop('production_set_provider_enabled', { provider_id: providerId, enabled }),
  resources: () => desktop('production_get_resources'),
  saveResources: (config) => desktop('production_save_resources', { config }),
  clients: () => desktop('production_list_clients'),
  createClient: (name) => desktop('production_create_client', { name }),
  bindProject: (projectId, clientId) => desktop('production_bind_project', { project_id: projectId, client_id: clientId }),
  projectClients: () => desktop('production_project_clients'),
  getProfile: (scope, clientId) => desktop('production_get_profile', clientId ? { scope, client_id: clientId } : { scope }),
  saveProfile: (profile, expectedRevision) => desktop('production_save_profile', { profile, expected_revision: expectedRevision }),
  setClipInstructions: (clipId, instructions, creative) => desktop('production_set_clip_instructions', { clip_id: clipId, instructions, creative }),
  fileUrl: (path) => convertFileSrc(path),
};
