import React from 'react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { ProductionWorkspace } from '../../apps/desktop/src/features/production/ProductionWorkspace';
import { costTotals } from '../../apps/desktop/src/features/production/SystemViews';
import { profileProblems } from '../../apps/desktop/src/features/production/ClientsView';
import { approvedPerHour, costPerApproved } from '../../apps/desktop/src/features/production/MetricsPanel';
import type { ProductionMetrics } from '../../apps/desktop/src/services/productionRepository';
import { createDemoProductionRepository } from '../../apps/desktop/src/mocks/productionDemo';
import type { ProductionRepository } from '../../apps/desktop/src/services/productionRepository';

const projects = [{ id: 'project_demo', name: 'Demo podcast' }];
const tab = (name: string) => fireEvent.click(screen.getByRole('tab', { name: new RegExp(`^${name}`) }));

function setup(repo: ProductionRepository = createDemoProductionRepository()) {
  render(<ProductionWorkspace repository={repo} projects={projects} refreshMs={60_000} />);
  return repo;
}

beforeEach(() => {
  window.localStorage.clear();
});

describe('production workspace', () => {
  it('labels demo data as a simulation and surfaces blocked clips', async () => {
    setup();
    expect(await screen.findByRole('heading', { level: 2, name: 'Podcast ep. 12 shorts' })).toBeInTheDocument();
    expect(screen.getByText('Demo Mode · Simulation')).toBeInTheDocument();
    const attention = screen.getByText('Needs attention').parentElement!;
    expect(within(attention).getByText('What a slow morning teaches')).toBeInTheDocument();
    expect(within(attention).getByText('Our worst first week')).toBeInTheDocument();
    expect(within(attention).queryByText('Why we brew before sunrise')).not.toBeInTheDocument();
  });

  it('shows queue reasons and errors without inventing progress', async () => {
    setup();
    await screen.findByRole('heading', { level: 2, name: 'Podcast ep. 12 shorts' });
    tab('Queue');
    expect(screen.getByText('RATE_LIMITED: provider rate limit')).toBeInTheDocument();
    expect(screen.getByText('waiting for an approved asset')).toBeInTheDocument();
    expect(screen.queryByText(/%/)).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('radio', { name: /Running/ }));
    expect(screen.getAllByRole('row')).toHaveLength(2);
  });

  it('requires a name before approval and records it', async () => {
    const repo = setup();
    await screen.findByRole('heading', { level: 2, name: 'Podcast ep. 12 shorts' });
    tab('Review');
    const approve = await screen.findByRole('button', { name: 'Approve this revision' });
    expect(approve).toBeDisabled();
    expect(screen.getByText('Simulation: no video was rendered.')).toBeInTheDocument();
    expect(await screen.findByText('Sample finding: the second cut lingers.')).toBeInTheDocument();

    fireEvent.change(screen.getByLabelText('Your name'), { target: { value: 'Sam' } });
    const spy = vi.spyOn(repo, 'approve');
    fireEvent.click(screen.getByRole('button', { name: 'Approve this revision' }));
    await waitFor(() => expect(spy).toHaveBeenCalledWith(['demo_0101'], 'Sam', ''));
    expect(window.localStorage.getItem('ageneditor.production.reviewer')).toBe('Sam');
  });

  it('runs a new batch from discovery to selected clips', async () => {
    setup();
    await screen.findByRole('heading', { level: 2, name: 'Podcast ep. 12 shorts' });
    fireEvent.click(screen.getByRole('button', { name: 'New batch' }));
    const form = screen.getByRole('form', { name: 'New batch' });
    const create = within(form).getByRole('button', { name: 'Create batch' });
    expect(create).toBeDisabled();
    fireEvent.change(within(form).getByPlaceholderText('Episode 12 shorts'), { target: { value: 'Ep 13' } });
    fireEvent.change(within(form).getByLabelText('Min length (s)'), { target: { value: '90' } });
    expect(within(form).getByText(/minimum ≤ maximum/)).toBeInTheDocument();
    fireEvent.change(within(form).getByLabelText('Min length (s)'), { target: { value: '15' } });
    fireEvent.click(create);

    expect(await screen.findByRole('heading', { level: 2, name: 'Ep 13' })).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Find clips' }));
    expect(await screen.findByText('Choose the clips to produce')).toBeInTheDocument();
    expect(screen.getByText('Ends without sentence punctuation.')).toBeInTheDocument();
    fireEvent.click(screen.getByLabelText('Select Sample candidate 1'));
    fireEvent.click(screen.getByLabelText('Select Sample candidate 2'));
    fireEvent.click(screen.getByRole('button', { name: 'Produce 2 clips' }));
    expect(await screen.findByRole('cell', { name: 'Sample candidate 2' })).toBeInTheDocument();
  });

  it('packages only approved clips and shows the manifest', async () => {
    const repo = setup();
    await repo.approve(['demo_0101'], 'Sam');
    fireEvent.change(await screen.findByLabelText('Your name'), { target: { value: 'Sam' } });
    tab('Delivery');
    fireEvent.click(screen.getByRole('button', { name: 'Refresh' }));
    const pick = await screen.findByLabelText('Deliver Why we brew before sunrise');
    expect(screen.queryByLabelText('Deliver The one tool we never skip')).not.toBeInTheDocument();
    fireEvent.click(pick);
    fireEvent.click(screen.getByRole('button', { name: 'Package 1 clip (partial)' }));
    expect(await screen.findByText('Simulation: no files were created.')).toBeInTheDocument();
  });

  it('keeps estimated and measured cost apart', async () => {
    setup();
    await screen.findByRole('heading', { level: 2, name: 'Podcast ep. 12 shorts' });
    tab('Costs');
    expect(screen.getAllByText('Estimated').length).toBeGreaterThan(0);
    expect(costTotals([
      { provider_id: 'p', model: 'm', tokens_in: 1, tokens_out: 1, cost_micros: 100, cost_kind: 'measured', task_type: 'x', batch_id: 'b', created_at: '' },
      { provider_id: 'p', model: 'm', tokens_in: 1, tokens_out: 1, cost_micros: 50, cost_kind: 'estimated', task_type: 'x', batch_id: 'b', created_at: '' },
      { provider_id: 'p', model: 'm', tokens_in: 1, tokens_out: 1, cost_micros: null, cost_kind: 'unknown', task_type: 'x', batch_id: 'b', created_at: '' },
    ])).toEqual({ measured: 100, estimated: 50, unknown: 0 });
  });

  it('reports a refused operation instead of pretending it worked', async () => {
    const repo = createDemoProductionRepository();
    repo.setPaused = vi.fn().mockRejectedValue(new Error('{"code":"INVALID_STATE_TRANSITION","message":"batch is completed"}'));
    setup(repo);
    fireEvent.click(await screen.findByRole('button', { name: 'Pause' }));
    expect(await screen.findByRole('alert')).toHaveTextContent('Pause: batch is completed');
  });
});

describe('clients and instruction profiles', () => {
  it('adds a client, saves a locked profile revision and refuses a stale save', async () => {
    const repo = setup();
    await screen.findByRole('heading', { level: 2, name: 'Podcast ep. 12 shorts' });
    tab('Clients');
    expect(await screen.findByRole('form', { name: 'Global defaults' })).toHaveTextContent('not saved yet');

    fireEvent.change(screen.getByLabelText('New client name'), { target: { value: 'Acme' } });
    fireEvent.click(screen.getByRole('button', { name: 'Add' }));
    const form = await screen.findByRole('form', { name: 'Acme profile' });
    fireEvent.change(within(form).getByLabelText('Color'), { target: { value: 'warm' } });
    fireEvent.click(within(form).getByLabelText('Lock Color'));
    fireEvent.change(within(form).getByPlaceholderText(/Never show competitor logos/), { target: { value: 'No competitor logos\n\n' } });
    expect(within(form).getByText('Unsaved changes')).toBeInTheDocument();
    fireEvent.click(within(form).getByRole('button', { name: 'Save as revision 1' }));
    expect(await within(form).findByText('revision 1')).toBeInTheDocument();
    const clientId = (await repo.clients()).find((c) => c.name === 'Acme')!.id;
    const saved = await repo.getProfile('client', clientId);
    expect(saved.creative.color_preset).toBe('warm');
    expect(saved.locked_creative).toEqual(['color_preset']);
    expect(saved.required_constraints).toEqual(['No competitor logos']);

    // Someone else saves meanwhile: this editor's revision 1 is now stale.
    await repo.saveProfile({ ...saved, instructions: 'elsewhere' }, 1);
    fireEvent.change(within(form).getByLabelText('Instructions'), { target: { value: 'mine' } });
    fireEvent.click(within(form).getByRole('button', { name: 'Save as revision 2' }));
    expect(await screen.findByRole('alert')).toHaveTextContent('Save profile: profile changed; refresh it before saving');
    expect((await repo.getProfile('client', clientId)).instructions).toBe('elsewhere');
  });

  it('keeps a project with batches on its client', async () => {
    setup();
    await screen.findByRole('heading', { level: 2, name: 'Podcast ep. 12 shorts' });
    tab('Clients');
    const select = await screen.findByLabelText('Client for Demo podcast');
    await waitFor(() => expect(select).toHaveValue('client_demo'));
    expect(select).toBeDisabled();
    expect(screen.getByText('Fixed by batches')).toBeInTheDocument();
  });

  it('checks a profile before sending it', () => {
    const base = { schema_version: 1 as const, scope: 'global' as const, client_id: null, revision: 0,
      creative: { caption_preset: null, color_preset: null, captions: null, normalize_audio: null, pacing: null, tone: null, platform: null },
      instructions: '', required_constraints: [], locked_creative: [], style_observations: [],
      delivery: { allow_partial_delivery: true, include_captions: true, include_project_snapshot: true, naming_prefix: null } };
    expect(profileProblems(base)).toEqual([]);
    expect(profileProblems({ ...base, delivery: { ...base.delivery, naming_prefix: '../x' } })[0]).toMatch(/File-name prefix/);
    expect(profileProblems({ ...base, required_constraints: ['x'.repeat(301)] })[0]).toMatch(/300 characters/);
  });

  it('accepts clip instructions only before planning', async () => {
    const repo = setup();
    await screen.findByRole('heading', { level: 2, name: 'Podcast ep. 12 shorts' });
    expect(screen.queryByRole('button', { name: /clip instructions/ })).not.toBeInTheDocument();
    const batch = (await repo.createBatch({ project_id: 'project_demo', name: 'Ep 14', policy: {} })).batch;
    await repo.startDiscovery(batch.id);
    const [clip] = await repo.confirmSelection(batch.id, [`cand_demo_${batch.id}_1`], 'Sam');
    fireEvent.change(screen.getByLabelText('Batch'), { target: { value: batch.id } });
    fireEvent.click(await screen.findByRole('button', { name: 'Add clip instructions' }));
    fireEvent.change(screen.getByLabelText('Instructions for Sample candidate 1'), { target: { value: 'Open on the laugh' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save' }));
    expect(await screen.findByText('Clip instructions: Open on the laugh')).toBeInTheDocument();
    expect((await repo.status(batch.id)).clips.find((c) => c.id === clip.id)?.instructions).toBe('Open on the laugh');
  });
});

describe('measured performance', () => {
  const empty: ProductionMetrics = {
    batch_id: 'b', measured_at: '', batch_created_at: '', clips_total: 3, clips_reached_review: 0, clips_approved: 0,
    clips_delivered: 0, first_review_ready_ms: null, last_approval_ms: null,
    queue_wait: { samples: 0, mean_ms: null, p50_ms: null, p95_ms: null, max_ms: null },
    attempt_run: { samples: 0, mean_ms: null, p50_ms: null, p95_ms: null, max_ms: null },
    attempts_total: 0, attempts_unsuccessful: 0, tasks_total: 4, tasks_succeeded: 0, tasks_failed: 0,
    checkpoint_reuses: 0, revision_tasks: 0,
    cost: { measured_micros: 0, provider_reported_micros: 0, estimated_micros: 0, unpriced_requests: 0 },
    pressure: { samples: 0, cpu_busy_mean_permille: null, cpu_busy_max_permille: null, memory_used_mean_permille: null,
      memory_used_max_permille: null, memory_available_min_bytes: null, disk_free_min_bytes: null, pool_peak_in_use: {}, pool_full_samples: {} },
  };

  it('derives rates from recorded counts and never mixes estimates in', () => {
    expect(approvedPerHour(empty)).toBeNull();
    expect(costPerApproved(empty)).toEqual({ recorded: null, estimated: null });
    const done = { ...empty, clips_approved: 3, last_approval_ms: 5_400_000,
      cost: { measured_micros: 300_000, provider_reported_micros: 150_000, estimated_micros: 90_000, unpriced_requests: 1 } };
    expect(approvedPerHour(done)).toBe('2.0');
    expect(costPerApproved(done)).toEqual({ recorded: '$0.15', estimated: '$0.03' });
  });

  it('says "not measured yet" instead of showing zeros', async () => {
    const repo = createDemoProductionRepository();
    repo.metrics = vi.fn().mockResolvedValue(empty);
    setup(repo);
    const panel = (await screen.findByRole('heading', { name: 'Measured performance' })).parentElement!;
    await waitFor(() => expect(within(panel).getAllByText('not measured yet').length).toBeGreaterThanOrEqual(4));
    expect(within(panel).queryByText(/0\.0/)).not.toBeInTheDocument();
    expect(within(panel).getByText(/No samples yet/)).toBeInTheDocument();
  });

  it('shows sampled machine pressure as average and peak', async () => {
    setup();
    const panel = (await screen.findByRole('heading', { name: 'Measured performance' })).parentElement!;
    expect(await within(panel).findByText('42% avg · 91% peak')).toBeInTheDocument();
    expect(within(panel).getByText('Team 5× · Render 3×')).toBeInTheDocument();
  });

  it('labels demo measurements as simulation', async () => {
    setup();
    const panel = (await screen.findByRole('heading', { name: 'Measured performance' })).parentElement!;
    expect(await within(panel).findByText('Simulation: sample values, not measurements.')).toBeInTheDocument();
  });
});

describe('live updates', () => {
  it('refreshes when the engine reports a change, without waiting for the fallback poll', async () => {
    const repo = createDemoProductionRepository();
    let announce: (notice: { cursor: string; changed: boolean }) => void = () => undefined;
    repo.waitForChanges = vi.fn()
      .mockResolvedValueOnce({ cursor: 'a', changed: false })
      .mockImplementationOnce(() => new Promise((resolve) => { announce = resolve; }))
      .mockImplementation(() => new Promise(() => undefined));
    const overview = vi.spyOn(repo, 'overview');
    render(<ProductionWorkspace repository={repo} projects={projects} refreshMs={600_000} changeWaitMs={1} />);
    await screen.findByRole('heading', { level: 2, name: 'Podcast ep. 12 shorts' });
    await waitFor(() => expect(repo.waitForChanges).toHaveBeenCalledTimes(2));
    const before = overview.mock.calls.length;
    announce({ cursor: 'b', changed: true });
    await waitFor(() => expect(overview).toHaveBeenCalledTimes(before + 1));
    expect(repo.waitForChanges).toHaveBeenNthCalledWith(1, null, 1);
    expect(repo.waitForChanges).toHaveBeenNthCalledWith(2, 'a', 1);
    await waitFor(() => expect(repo.waitForChanges).toHaveBeenNthCalledWith(3, 'b', 1));
  });
});

describe('style notes', () => {
  it('adds a person note to a client profile and saves it', async () => {
    const repo = setup();
    await screen.findByRole('heading', { level: 2, name: 'Podcast ep. 12 shorts' });
    tab('Clients');
    fireEvent.click(await screen.findByRole('button', { name: 'Demo client' }));
    const form = await screen.findByRole('form', { name: 'Demo client profile' });
    fireEvent.change(within(form).getByLabelText('Style note name'), { target: { value: 'Caption position' } });
    fireEvent.change(within(form).getByLabelText('Style note value'), { target: { value: 'Lower third' } });
    fireEvent.click(within(form).getByRole('button', { name: 'Add note' }));
    expect(within(form).getByText('From a person')).toBeInTheDocument();
    fireEvent.click(within(form).getByRole('button', { name: 'Save as revision 1' }));
    await waitFor(async () => expect((await repo.getProfile('client', 'client_demo')).style_observations).toEqual([
      expect.objectContaining({ label: 'Caption position', value: 'Lower third', basis: 'person', source_asset_id: null }),
    ]));
    expect((await repo.getProfile('client', 'client_demo')).style_observations[0].id).toMatch(/^[a-z0-9][a-z0-9_-]{0,63}$/);
  });
});

describe('reference measurement', () => {
  it("adds measured notes from the client's own reference file", async () => {
    const repo = setup();
    await screen.findByRole('heading', { level: 2, name: 'Podcast ep. 12 shorts' });
    tab('Clients');
    fireEvent.click(await screen.findByRole('button', { name: 'Demo client' }));
    const form = await screen.findByRole('form', { name: 'Demo client profile' });
    await waitFor(() => expect(within(form).getByLabelText('Reference file')).toHaveValue('asset_demo_ref'));
    fireEvent.click(within(form).getByRole('button', { name: 'Measure' }));
    expect(await within(form).findByRole('status')).toHaveTextContent('Added 3 measured notes');
    expect(within(form).getAllByText('Measured')).toHaveLength(3);
    // Measuring again replaces, never duplicates.
    fireEvent.click(within(form).getByRole('button', { name: 'Measure' }));
    await waitFor(() => expect(within(form).getAllByText('Measured')).toHaveLength(3));
    fireEvent.click(within(form).getByRole('button', { name: 'Save as revision 1' }));
    await waitFor(async () => expect((await repo.getProfile('client', 'client_demo')).style_observations).toHaveLength(3));
    const saved = (await repo.getProfile('client', 'client_demo')).style_observations;
    expect(saved.every((n) => n.basis === 'measured' && n.source_asset_id === 'asset_demo_ref')).toBe(true);
  });

  it("keeps a client's footage out of the global defaults", async () => {
    setup();
    await screen.findByRole('heading', { level: 2, name: 'Podcast ep. 12 shorts' });
    tab('Clients');
    const form = await screen.findByRole('form', { name: 'Global defaults' });
    expect(within(form).getByText(/No eligible project/)).toBeInTheDocument();
    expect(within(form).queryByRole('button', { name: 'Measure' })).not.toBeInTheDocument();
  });
});
