/** Demo Mode / Simulation data for the Production Command Center in the browser build.
 *
 * Everything here is sample data held in memory. No media is analysed, rendered, reviewed or packaged,
 * and nothing is sent anywhere. The UI labels every screen that uses it as a simulation. */

import type {
  Batch, BatchStatusDetail, BatchSummary, Candidate, Client, ClipDetail, ClipStatus, DeliveryManifest, InstructionProfile,
  NewBatch, Overview, ProductionClip, ProductionRepository, ProductionTask, ProfileScope, ProviderInfo, ResourceConfig, UsageRow,
} from '../services/productionRepository';

const now = () => new Date().toISOString();
const sample = (n: number) => `demo_${n.toString().padStart(4, '0')}`;

interface DemoState {
  batches: Batch[];
  clips: ProductionClip[];
  tasks: ProductionTask[];
  candidates: Record<string, Candidate[]>;
  deliveries: Record<string, DeliveryManifest[]>;
  providers: ProviderInfo[];
  config: ResourceConfig;
  usage: UsageRow[];
  clients: Client[];
  bindings: Record<string, string>;
  /** Every saved profile revision, keyed by scope ("global" or "client:<id>"). */
  profiles: Record<string, InstructionProfile[]>;
  seq: number;
}

const emptyProfile = (scope: ProfileScope, clientId: string | null): InstructionProfile => ({
  schema_version: 1, scope, client_id: clientId, revision: 0,
  creative: { caption_preset: null, color_preset: null, captions: null, normalize_audio: null, pacing: null, tone: null, platform: null },
  instructions: '', required_constraints: [], locked_creative: [], style_observations: [],
  delivery: { allow_partial_delivery: true, include_captions: true, include_project_snapshot: true, naming_prefix: null },
});
const profileKey = (scope: ProfileScope, clientId?: string | null) => (scope === 'global' ? 'global' : `client:${clientId}`);

const policy = (count: number | null): Batch['policy'] => ({
  target_mode: count ? 'count' : 'auto', target_count: count, min_duration_us: 20_000_000, max_duration_us: 60_000_000,
  selection: 'human', editorial_qa: 'required', allow_data_egress: false, budget_micros: 2_000_000,
});

const clip = (n: number, batchId: string, title: string, status: ClipStatus, reason = ''): ProductionClip => ({
  id: sample(100 + n), batch_id: batchId, title, ordinal: n, status, status_reason: reason,
  current_revision: status === 'discovered' ? null : 4, draft_revision: ['review_ready', 'human_approved', 'exported'].includes(status) ? 4 : null,
  team_id: status === 'editing' ? 'team_demo_a' : null, updated_at: now(),
  plan_id: status === 'discovered' ? null : `plan_demo_${n}`, instructions: '',
});

const task = (n: number, clipId: string | null, type: string, status: ProductionTask['status'], extra: Partial<ProductionTask> = {}): ProductionTask => ({
  id: sample(500 + n), clip_id: clipId, type, cycle: 0, status, block_reason: null, attempt_count: status === 'created' ? 0 : 1,
  max_attempts: 3, not_before: null, result: null, error: null, created_at: now(), updated_at: now(), ...extra,
});

function initial(): DemoState {
  const batch: Batch = {
    id: 'batch_demo_launch', project_id: 'project_demo', client_id: 'client_demo', name: 'Podcast ep. 12 shorts',
    sequence: 1, status: 'producing', paused: false, policy: policy(4), created_at: now(),
  };
  const clips = [
    clip(1, batch.id, 'Why we brew before sunrise', 'review_ready', 'editorial QA complete; waiting for a person to approve'),
    clip(2, batch.id, 'The one tool we never skip', 'editing', 'captions'),
    clip(3, batch.id, 'What a slow morning teaches', 'waiting_for_assets', 'waiting for: broll_01'),
    clip(4, batch.id, 'Our worst first week', 'retry_wait', 'provider rate limit; retrying'),
  ];
  return {
    batches: [batch],
    clips,
    tasks: [
      task(1, clips[0].id, 'editorial_qa', 'succeeded', { result: { verdict: 'pass' } }),
      task(2, clips[1].id, 'captions', 'running'),
      task(3, clips[1].id, 'render_preview', 'blocked', { block_reason: 'dependencies' }),
      task(4, clips[2].id, 'structural_edit', 'blocked', { block_reason: 'waiting_for_assets' }),
      task(5, clips[3].id, 'editorial_qa', 'retry_wait', { error: { code: 'RATE_LIMITED', message: 'provider rate limit' }, not_before: now() }),
    ],
    candidates: { [batch.id]: [] },
    deliveries: { [batch.id]: [] },
    providers: [
      { id: 'claude_code', label: 'Claude Code (your subscription)', adapter_type: 'claude_code', enabled: false, adapter_ready: false,
        adapter_reason: 'Demo Mode', sends_data_off_device: true, models: [{ model: 'claude-opus-5-5', task_types: ['editorial_qa'], quality: 75 }] },
      { id: 'anthropic_api', label: 'Anthropic API (your API key)', adapter_type: 'anthropic_api', enabled: false, adapter_ready: false,
        adapter_reason: 'Demo Mode', sends_data_off_device: true, models: [{ model: 'claude-opus-5-5', task_types: ['editorial_qa', 'discover_clips'], quality: 75 }] },
    ],
    config: { team_slots: 2, analysis_slots: 1, render_slots: 1, qa_slots: 1, lease_seconds: 120, host_timeout_seconds: 1800 },
    usage: [{ provider_id: 'anthropic_api', model: 'claude-opus-5-5', tokens_in: 24000, tokens_out: 2100, cost_micros: 138000,
      cost_kind: 'estimated', task_type: 'editorial_qa', batch_id: batch.id, created_at: now() }],
    clients: [{ id: 'client_demo', name: 'Demo client', status: 'active' }],
    bindings: { project_demo: 'client_demo' },
    profiles: {},
    seq: 1,
  };
}

const counts = <T extends string>(values: T[]) => values.reduce<Partial<Record<T, number>>>((acc, value) => ({ ...acc, [value]: (acc[value] ?? 0) + 1 }), {});

export function createDemoProductionRepository(): ProductionRepository {
  let state = initial();
  const find = (batchId: string) => {
    const batch = state.batches.find((item) => item.id === batchId);
    if (!batch) throw new Error('Demo batch not found');
    return batch;
  };
  const setClip = (id: string, patch: Partial<ProductionClip>) => {
    state = { ...state, clips: state.clips.map((item) => (item.id === id ? { ...item, ...patch, updated_at: now() } : item)) };
  };
  const summary = (batch: Batch): BatchSummary => {
    const clips = state.clips.filter((item) => item.batch_id === batch.id);
    const tasks = state.tasks.filter((item) => clips.some((c) => c.id === item.clip_id));
    return {
      batch, project_name: 'Demo podcast', clip_counts: counts(clips.map((c) => c.status)), clips_total: clips.length,
      attention: clips.filter((c) => ['needs_human', 'failed', 'blocked', 'waiting_for_assets'].includes(c.status)).length,
      review_ready: clips.filter((c) => c.status === 'review_ready').length,
      tasks_active: tasks.filter((t) => t.status === 'running' || t.status === 'leased').length,
      tasks_waiting: tasks.filter((t) => ['ready', 'blocked', 'retry_wait'].includes(t.status)).length,
    };
  };
  return {
    simulated: true,
    async overview(): Promise<Overview> {
      // Simulated progress: approved demo clips become "exported" on the next refresh.
      state.clips.filter((c) => c.status === 'human_approved').forEach((c) => setClip(c.id, { status: 'exported', status_reason: 'simulated export' }));
      return {
        batches: state.batches.map(summary),
        running: state.tasks.filter((t) => t.status === 'running').map((t) => ({
          attempt_id: `attempt_${t.id}`, task_id: t.id, type: t.type, clip_id: t.clip_id, batch_id: state.batches[0].id,
          adapter: 'engine', model: null, provider_id: null, started_at: t.updated_at,
        })),
        teams: [{ team_id: 'team_demo_a', project_id: 'project_demo', clip_id: state.clips[1].id, batch_id: state.batches[0].id, assigned_at: now() }],
        resources: {
          pools: { team: { capacity: state.config.team_slots, in_use: 1, holders: [] }, analysis: { capacity: 1, in_use: 0, holders: [] },
            render: { capacity: 1, in_use: 0, holders: [] }, qa: { capacity: 1, in_use: 0, holders: [] } },
          cpu_count: null, memory_total_bytes: null, memory_available_bytes: null, disk_free_bytes: null, measured_at: now(),
        },
      };
    },
    async status(batchId): Promise<BatchStatusDetail> {
      const batch = find(batchId);
      const clips = state.clips.filter((c) => c.batch_id === batchId);
      const tasks = state.tasks.filter((t) => clips.some((c) => c.id === t.clip_id));
      return {
        batch, clips, tasks, candidates: state.candidates[batchId] ?? [], task_counts: counts(tasks.map((t) => t.status)),
        clip_counts: counts(clips.map((c) => c.status)), usage: state.usage.filter((u) => u.batch_id === batchId),
        deliveries: state.deliveries[batchId] ?? [],
      };
    },
    async clipDetail(clipId): Promise<ClipDetail> {
      const item = state.clips.find((c) => c.id === clipId);
      if (!item) throw new Error('Demo clip not found');
      return {
        clip: item,
        reviews: item.draft_revision ? [{ id: 'review_demo', timeline_revision: 4, reviewer: 'anthropic_api:claude-opus-5-5', status: 'completed',
          verdict: 'pass', summary: 'Sample review text (simulation).', evidence_basis: ['timeline', 'transcript', 'technical_qa'], created_at: now() }] : [],
        findings: item.draft_revision ? [{ id: 'finding_demo', severity: 'minor', category: 'pacing', evidence: 'Sample finding: the second cut lingers.',
          suggested_correction: 'Trim the pause.', confidence: 0.6, state: 'open', source: 'ai', review_revision: 4, reference: { kind: 'clip', id: 'cut_02' } }] : [],
        approvals: [], artifacts: [], revisions: [{ revision: 4, attempt_id: null, created_at: now() }],
      };
    },
    async createBatch(request: NewBatch) {
      state.seq += 1;
      const batch: Batch = {
        id: `batch_demo_${state.seq}`, project_id: request.project_id, client_id: null, name: request.name, sequence: state.seq,
        status: 'created', paused: false, policy: { ...policy(null), ...request.policy } as Batch['policy'], created_at: now(),
      };
      state = { ...state, batches: [batch, ...state.batches], candidates: { ...state.candidates, [batch.id]: [] }, deliveries: { ...state.deliveries, [batch.id]: [] } };
      return { batch, conflicts: [] };
    },
    async startDiscovery(batchId) {
      const batch = { ...find(batchId), status: 'awaiting_selection' as const };
      state = {
        ...state, batches: state.batches.map((b) => (b.id === batchId ? batch : b)),
        candidates: { ...state.candidates, [batchId]: [1, 2, 3].map((n) => ({
          id: `cand_demo_${batchId}_${n}`, rank: n, topic: `Sample candidate ${n}`, hook_text: 'Sample hook sentence.',
          quote: 'Sample transcript passage (simulation; no real transcript was read).', duration_us: 30_000_000 + n * 4_000_000,
          overall_score: 0.8 - n * 0.1, narrative_complete: n !== 3, method: 'deterministic' as const,
          caveats: n === 3 ? ['Ends without sentence punctuation.'] : [], status: 'proposed' as const,
        })) },
      };
      return batch;
    },
    async confirmSelection(batchId, candidateIds) {
      const chosen = (state.candidates[batchId] ?? []).filter((c) => candidateIds.includes(c.id));
      const created = chosen.map((c, index) => ({ ...clip(900 + state.seq * 10 + index, batchId, c.topic, 'discovered'), id: `clip_demo_${c.id}` }));
      state = {
        ...state, clips: [...state.clips, ...created],
        batches: state.batches.map((b) => (b.id === batchId ? { ...b, status: 'producing' } : b)),
      };
      return created;
    },
    async setPaused(batchId, paused) {
      const batch = { ...find(batchId), paused };
      state = { ...state, batches: state.batches.map((b) => (b.id === batchId ? batch : b)) };
      return batch;
    },
    async cancelBatch(batchId) {
      const batch = { ...find(batchId), status: 'cancelled' as const };
      state = { ...state, batches: state.batches.map((b) => (b.id === batchId ? batch : b)) };
      state.clips.filter((c) => c.batch_id === batchId && c.status !== 'delivered').forEach((c) => setClip(c.id, { status: 'cancelled' }));
      return batch;
    },
    async approve(clipIds, approvedBy) {
      clipIds.forEach((id) => setClip(id, { status: 'human_approved', status_reason: `approved by ${approvedBy}` }));
      return clipIds.map((id) => ({ clip_id: id }));
    },
    async requestRevision(clipId) {
      setClip(clipId, { status: 'needs_revision', status_reason: 'a person requested changes' });
      return { task_ids: [] };
    },
    async acceptFindings(clipId) {
      setClip(clipId, { status: 'review_ready', status_reason: 'open findings accepted' });
      return state.clips.find((c) => c.id === clipId)!;
    },
    async retryTask(taskId) {
      state = { ...state, tasks: state.tasks.map((t) => (t.id === taskId ? { ...t, status: 'created', error: null } : t)) };
      return state.tasks.find((t) => t.id === taskId)!;
    },
    async packageDelivery(batchId, clipIds, authorizedBy) {
      const manifest: DeliveryManifest = {
        id: `delivery_demo_${state.seq}`, package_name: `Demo_Batch_${find(batchId).sequence.toString().padStart(3, '0')}_01`,
        partial: true, authorized_by: authorizedBy, created_at: now(),
        clips: clipIds.map((id) => ({ clip_id: id, title: state.clips.find((c) => c.id === id)?.title ?? id, video: 'videos/(simulated).mp4', captions: null })),
        files: [], notice: 'Simulation: no files were created.',
      };
      clipIds.forEach((id) => setClip(id, { status: 'delivered' }));
      state = { ...state, deliveries: { ...state.deliveries, [batchId]: [...(state.deliveries[batchId] ?? []), manifest] } };
      return manifest;
    },
    async costs() { return state.usage; },
    async metrics(batchId) {
      // Simulation: counts follow the demo clips; durations are fixed sample values, not measurements.
      const batch = find(batchId);
      const clips = state.clips.filter((c) => c.batch_id === batchId);
      const approved = clips.filter((c) => ['human_approved', 'exported', 'delivered'].includes(c.status)).length;
      const reviewed = approved + clips.filter((c) => c.status === 'review_ready').length;
      const sampleStats = (mean: number) => ({ samples: 6, mean_ms: mean, p50_ms: mean, p95_ms: mean * 3, max_ms: mean * 4 });
      return {
        batch_id: batchId, measured_at: now(), batch_created_at: batch.created_at,
        clips_total: clips.length, clips_reached_review: reviewed, clips_approved: approved,
        clips_delivered: clips.filter((c) => c.status === 'delivered').length,
        first_review_ready_ms: reviewed ? 1_260_000 : null, last_approval_ms: approved ? 2_700_000 : null,
        queue_wait: sampleStats(4_000), attempt_run: sampleStats(48_000),
        attempts_total: 9, attempts_unsuccessful: 1, tasks_total: 12, tasks_succeeded: 7, tasks_failed: 0,
        checkpoint_reuses: 2, revision_tasks: 1,
        cost: { measured_micros: 0, provider_reported_micros: 0, estimated_micros: 138_000, unpriced_requests: 0 },
        pressure: {
          samples: 12, cpu_busy_mean_permille: 420, cpu_busy_max_permille: 910, memory_used_mean_permille: 610,
          memory_used_max_permille: 740, memory_available_min_bytes: 4_200_000_000, disk_free_min_bytes: 120_000_000_000,
          pool_peak_in_use: { team: 2, render: 1, analysis: 1, qa: 1 }, pool_full_samples: { team: 5, render: 3 },
        },
      };
    },
    async referenceAssets(projectId) {
      return projectId === 'project_demo' ? [{ id: 'asset_demo_ref', name: 'reference_reel.mp4', kind: 'video', duration_us: 32_000_000 }] : [];
    },
    async measureReference(_projectId, assetId) {
      // Simulation: fixed sample notes; no file is decoded.
      const note = (key: string, label: string, value: string) => ({
        id: `ref-${key}-${assetId}`.slice(0, 64), label, value: `${value} (simulation)`, basis: 'measured' as const, source_asset_id: assetId,
      });
      return [
        note('frame', 'Frame', '1080x1920 (vertical, 9:16)'),
        note('shot', 'Average shot length', '2.1 s'),
        note('loudness', 'Integrated loudness', '-14.3 LUFS'),
      ];
    },
    // Demo state changes only through this tab's own actions, which refresh anyway: wait, then report no change.
    waitForChanges(cursor, waitMs) {
      return new Promise((resolve) => { window.setTimeout(() => resolve({ cursor: cursor ?? 'demo', changed: false }), waitMs); });
    },
    async providers() { return state.providers; },
    async setProviderEnabled(providerId, enabled) {
      state = { ...state, providers: state.providers.map((p) => (p.id === providerId ? { ...p, enabled } : p)) };
      return state.providers.find((p) => p.id === providerId)!;
    },
    async resources() {
      return { config: state.config, snapshot: (await this.overview()).resources };
    },
    async saveResources(config) { state = { ...state, config }; return config; },
    async clients() { return state.clients; },
    async createClient(name) {
      const trimmed = name.trim();
      if (!trimmed) throw new Error('name the client');
      if (state.clients.some((c) => c.name.toLowerCase() === trimmed.toLowerCase())) throw new Error('a client with this name already exists');
      state.seq += 1;
      const client: Client = { id: `client_demo_${state.seq}`, name: trimmed, status: 'active' };
      state = { ...state, clients: [...state.clients, client].sort((a, b) => a.name.localeCompare(b.name)) };
      return client;
    },
    async bindProject(projectId, clientId) {
      const bound = state.bindings[projectId];
      if (bound && bound !== clientId && state.batches.some((b) => b.project_id === projectId)) {
        throw new Error('a project with batches cannot move to another client');
      }
      state = { ...state, bindings: { ...state.bindings, [projectId]: clientId } };
      return { project_id: projectId, client_id: clientId };
    },
    async projectClients() { return { ...state.bindings }; },
    async getProfile(scope, clientId) {
      const history = state.profiles[profileKey(scope, clientId)] ?? [];
      return history[history.length - 1] ?? emptyProfile(scope, scope === 'client' ? clientId ?? null : null);
    },
    async saveProfile(profile, expectedRevision) {
      const key = profileKey(profile.scope, profile.client_id);
      const history = state.profiles[key] ?? [];
      const current = history[history.length - 1]?.revision ?? 0;
      if (current !== expectedRevision) throw new Error('{"code":"REVISION_CONFLICT","message":"profile changed; refresh it before saving"}');
      const saved = { ...profile, revision: current + 1 };
      state = { ...state, profiles: { ...state.profiles, [key]: [...history, saved] } };
      return saved;
    },
    async setClipInstructions(clipId, instructions) {
      const item = state.clips.find((c) => c.id === clipId);
      if (!item) throw new Error('Demo clip not found');
      if (item.plan_id) throw new Error('the clip is already planned');
      setClip(clipId, { instructions });
      return state.clips.find((c) => c.id === clipId)!;
    },
    fileUrl: () => null,
  };
}
