import React from 'react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { PreviewCheckPanel } from '../../apps/desktop/src/features/live/PreviewCheckPanel';
import type { EnginePreviewQA } from '../../apps/desktop/src/services/liveEngineRepository';

const engine = vi.hoisted(() => ({ previewQA: vi.fn() }));
vi.mock('../../apps/desktop/src/services/liveEngineRepository', () => ({ liveEngineRepository: engine }));

const report: EnginePreviewQA = {
  id: 'pqa_1', job_id: 'job_p', timeline_revision: 3, frames: [5, 15, 25], frame_grid_path: 'renders/job_p/frame_grid.png', overall: 'warn',
  checks: [
    { check_id: 'preview_format', status: 'pass', measured: 'matches', message: '' },
    { check_id: 'black_bars', status: 'warn', measured: '0.50s picture 180x240', message: 'black bars detected where the clip should fill the frame; check the crop' },
    { check_id: 'caption_audio', status: 'skipped', message: 'the edit has no captions' },
  ],
};

beforeEach(() => vi.resetAllMocks());

describe('preview check panel', () => {
  it('offers to check a preview that has not been checked, then shows results and the frame grid', async () => {
    engine.previewQA.mockResolvedValueOnce({ report: null, gridUrl: null }).mockResolvedValueOnce({ report, gridUrl: 'asset://grid.png' });
    render(<PreviewCheckPanel projectId="p" jobId="job_p" />);
    await waitFor(() => expect(engine.previewQA).toHaveBeenCalledWith('p', 'job_p', false));
    fireEvent.click(screen.getByRole('button', { name: /Check this preview/ }));
    await waitFor(() => expect(engine.previewQA).toHaveBeenCalledWith('p', 'job_p', true));
    expect(await screen.findByText('Look at the flagged items')).toBeInTheDocument();
    expect(screen.getByText(/0.50s picture 180x240/)).toBeInTheDocument();
    expect(screen.getByText(/not checked \(the edit has no captions\)/)).toBeInTheDocument();
    expect(screen.getByAltText(/Frame grid of the preview: 3 frames/)).toHaveAttribute('src', 'asset://grid.png');
  });

  it('shows a stored result straight away and does not run again by itself', async () => {
    engine.previewQA.mockResolvedValue({ report: { ...report, overall: 'pass', checks: [report.checks[0]] }, gridUrl: null });
    render(<PreviewCheckPanel projectId="p" jobId="job_p" />);
    expect(await screen.findByText('Nothing flagged')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Check again' })).toBeInTheDocument();
    expect(engine.previewQA).toHaveBeenCalledTimes(1);
  });
});
