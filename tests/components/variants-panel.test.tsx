import React from 'react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { VariantsPanel } from '../../apps/desktop/src/features/live/VariantsPanel';
import type { EngineJob, EngineVariantSummary } from '../../apps/desktop/src/services/liveEngineRepository';

const engine = vi.hoisted(() => ({ mediaUrl: vi.fn(), startRender: vi.fn(), runQA: vi.fn(), saveExport: vi.fn() }));
vi.mock('../../apps/desktop/src/services/liveEngineRepository', () => ({ liveEngineRepository: engine }));

const summary = (id: string, extra: Partial<EngineVariantSummary> = {}): EngineVariantSummary => ({
  variant: { id, name: `Variant ${id}`, kind: 'hook', language: null, note: '', revision: 2 },
  main_revision: 4, duration_frames: 105, renderable: true, errors: [], warnings: [], pending_requests: [], ...extra,
});
const job = (extra: Partial<EngineJob>): EngineJob => ({
  id: 'job_v', project_id: 'p', kind: 'preview_render', status: 'succeeded', progress: 1, timeline_revision: 4,
  output_path: 'renders/job_v/preview.mp4', error: null, created_at: '', variant_id: 'variant_a', variant_revision: 2, ...extra,
});
const notice = vi.fn();
const jobsChanged = vi.fn();
const props = { projectId: 'p', revision: 4, onJobsChanged: jobsChanged, onNotice: notice };
const item = (id: string) => within(screen.getByTestId(`variant-${id}`));

beforeEach(() => {
  vi.resetAllMocks();
  engine.mediaUrl.mockResolvedValue('asset://variant.mp4');
});

describe('variants panel', () => {
  it('shows each variant with its status and plays its own latest preview', async () => {
    const spanish = summary('variant_b', {
      variant: { id: 'variant_b', name: 'Spanish', kind: 'language', language: 'es', note: '', revision: 1 },
      renderable: false, pending_requests: ['shot_es'],
    });
    const broken = summary('variant_c', { renderable: false, errors: ['the hook replaces 3 clips but the main edit has only 2'] });
    render(<VariantsPanel {...props} variants={[summary('variant_a'), spanish, broken]} jobs={[job({})]} />);

    expect(item('variant_a').getByText('Ready to render')).toBeInTheDocument();
    expect(item('variant_a').getByText(/revision 2 on main revision 4/)).toBeInTheDocument();
    expect(item('variant_b').getByText('Waiting for 1 file')).toBeInTheDocument();
    expect(item('variant_b').getByText(/Language · es/)).toBeInTheDocument();
    expect(item('variant_b').getByText('Not rendered yet.')).toBeInTheDocument();
    expect(item('variant_c').getByText("Doesn't fit the current edit")).toBeInTheDocument();
    expect(item('variant_c').getByText(/main edit has only 2/)).toBeInTheDocument();

    fireEvent.click(item('variant_a').getByRole('button', { name: /Play preview/ }));
    await waitFor(() => expect(engine.mediaUrl).toHaveBeenCalledWith('p', 'job_v'));
    expect(await item('variant_a').findByLabelText('Preview of Variant variant_a')).toBeInTheDocument();
  });

  it('says when the preview is of an earlier version', () => {
    render(<VariantsPanel {...props} variants={[summary('variant_a')]} jobs={[job({ timeline_revision: 3 })]} />);
    expect(item('variant_a').getByText(/Preview is of an earlier version/)).toBeInTheDocument();
  });

  it('renders a final of a variant, checks it and saves it like the main edit', async () => {
    engine.startRender.mockResolvedValue(job({ id: 'job_f', kind: 'final_render', status: 'queued', progress: 0 }));
    const { rerender } = render(<VariantsPanel {...props} variants={[summary('variant_a')]} jobs={[]} />);
    fireEvent.click(item('variant_a').getByRole('button', { name: 'Render final' }));
    await waitFor(() => expect(engine.startRender).toHaveBeenCalledWith('p', 4, true, 'variant_a'));
    expect(jobsChanged).toHaveBeenCalled();

    engine.runQA.mockResolvedValue({ overall: 'pass', checks: [], generated_assets: [] });
    engine.saveExport.mockResolvedValue('C:/out/hook-b.mp4');
    rerender(<VariantsPanel {...props} variants={[summary('variant_a')]} jobs={[job({ id: 'job_f', kind: 'final_render' })]} />);
    fireEvent.click(item('variant_a').getByRole('button', { name: /Check & save/ }));
    await waitFor(() => expect(engine.saveExport).toHaveBeenCalledWith('p', 'job_f'));
    expect(engine.runQA).toHaveBeenCalledWith('p', 'job_f');
    expect(notice).toHaveBeenCalledWith('Saved "Variant variant_a" to C:/out/hook-b.mp4');
  });

  it('does not save a variant whose technical check failed, and cannot render one that does not fit', async () => {
    engine.runQA.mockResolvedValue({ overall: 'fail', checks: [{ check_id: 'duration', status: 'fail', measured: '3.0s', message: '' }], generated_assets: [] });
    const broken = summary('variant_b', { renderable: false, errors: ['does not fit'] });
    render(<VariantsPanel {...props} variants={[summary('variant_a'), broken]} jobs={[job({ id: 'job_f', kind: 'final_render' })]} />);
    expect(item('variant_b').getByRole('button', { name: 'Render final' })).toBeDisabled();
    fireEvent.click(item('variant_a').getByRole('button', { name: /Check & save/ }));
    await waitFor(() => expect(notice).toHaveBeenCalledWith(expect.stringContaining('Technical check failed')));
    expect(engine.saveExport).not.toHaveBeenCalled();
    expect(item('variant_a').getByText(/Technical check: fail · duration: 3.0s/)).toBeInTheDocument();
  });
});
