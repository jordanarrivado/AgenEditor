import React from 'react';
import { describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen } from '@testing-library/react';
import { TimelineView } from '../../apps/desktop/src/features/live/TimelineView';
import type { EngineAsset, EngineTimeline } from '../../apps/desktop/src/services/liveEngineRepository';

const timeline: EngineTimeline = {
  project_id: 'p', revision: 3, target_duration_frames: 90,
  render_profile: { width: 180, height: 320, frame_rate: { num: 30, den: 1 } },
  clips: [
    { clip_id: 'a', asset_id: 'asset_1', track: 'V1', source_in_frames: 0, source_out_frames: 45, timeline_in_frames: 0 },
    { clip_id: 'b', asset_id: 'asset_1', track: 'V1', source_in_frames: 300, source_out_frames: 345, timeline_in_frames: 45 },
    { clip_id: 'shot', asset_id: 'asset_2', track: 'V2', source_in_frames: 0, source_out_frames: 15, timeline_in_frames: 30 },
    { clip_id: 'line', asset_id: 'asset_3', track: 'A4', source_in_frames: 0, source_out_frames: 20, timeline_in_frames: 60 },
  ],
  captions: [{ cue_id: 'c1', text: 'Hello', timeline_in_frames: 0, timeline_out_frames: 30 }],
  provenance: { author: 'agent', summary: '' },
};
const assets = [
  { id: 'asset_1', original_filename: 'interview.mp4' }, { id: 'asset_2', original_filename: 'city.mp4' }, { id: 'asset_3', original_filename: 'voice.wav' },
] as unknown as EngineAsset[];

describe('timeline view', () => {
  it('draws only the tracks in use, to scale, with file names', () => {
    render(<TimelineView timeline={timeline} assets={assets} currentTime={0} previewRevision={3} onSeek={vi.fn()} />);
    expect(screen.getByText('V1 main')).toBeInTheDocument();
    expect(screen.getByText('A4 voice')).toBeInTheDocument();
    expect(screen.queryByText('A2 music')).toBeNull();
    expect(screen.getByText('T1 captions')).toBeInTheDocument();
    expect(screen.getByTestId('clip-shot')).toHaveStyle({ left: '33.33333333333333%' });
    expect(screen.getByTestId('clip-b')).toHaveTextContent('interview.mp4');
    expect(screen.getByText(/3.0s · click to jump the preview/)).toBeInTheDocument();
  });

  it('seeks the preview when it is of this revision, and only then', () => {
    const onSeek = vi.fn();
    const { rerender } = render(<TimelineView timeline={timeline} assets={assets} currentTime={1} previewRevision={3} onSeek={onSeek} />);
    const track = screen.getByRole('button', { name: 'Seek on V1 main' });
    track.getBoundingClientRect = () => ({ left: 0, width: 300, top: 0, height: 20, right: 300, bottom: 20, x: 0, y: 0, toJSON: () => ({}) });
    fireEvent.click(track, { clientX: 150 });
    expect(onSeek).toHaveBeenCalledWith(1.5);
    expect(screen.getByTestId('playhead')).toBeInTheDocument();

    rerender(<TimelineView timeline={timeline} assets={assets} currentTime={1} previewRevision={2} onSeek={onSeek} />);
    expect(screen.queryByRole('button', { name: 'Seek on V1 main' })).toBeNull();
    expect(screen.queryByTestId('playhead')).toBeNull();
    expect(screen.getByText(/preview is of revision 2/)).toBeInTheDocument();
  });
});
