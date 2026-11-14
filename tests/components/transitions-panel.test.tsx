import React from 'react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { TransitionsPanel } from '../../apps/desktop/src/features/live/TransitionsPanel';

const engine = vi.hoisted(() => ({ listTransitionStyles: vi.fn(), setTransitionStyleActive: vi.fn() }));
vi.mock('../../apps/desktop/src/services/liveEngineRepository', () => ({ liveEngineRepository: engine }));

const style = (id: string, label: string, active: boolean, count: number) => ({
  id, label, active, count, description: `${label} transitions.`, examples: [`${label} one`, `${label} two`],
});
let styles = [style('classic', 'Classic', true, 9), style('modern', 'Modern', true, 18), style('montage', 'Montage', false, 21), style('cinematic', 'Cinematic', true, 10)];

beforeEach(() => {
  vi.resetAllMocks();
  styles = styles.map((s) => ({ ...s, active: s.id !== 'montage' }));
  engine.listTransitionStyles.mockImplementation(async () => styles);
  engine.setTransitionStyleActive.mockImplementation(async (id: string, active: boolean) => {
    styles = styles.map((s) => (s.id === id ? { ...s, active } : s));
    return styles;
  });
});

describe('transitions panel', () => {
  it('shows the four built-in styles with counts and active state', async () => {
    render(<TransitionsPanel onNotice={vi.fn()} />);
    expect(await screen.findByLabelText('Active: Classic')).toBeChecked();
    expect(screen.getByLabelText('Active: Montage')).not.toBeChecked();
    expect(screen.getByText('3 of 4 styles active')).toBeInTheDocument();
    expect(screen.getByText('58 built in · free')).toBeInTheDocument();
    expect(screen.getByText('e.g. Cinematic one, Cinematic two')).toBeInTheDocument();
  });

  it('ticking a style saves it in the engine', async () => {
    render(<TransitionsPanel onNotice={vi.fn()} />);
    fireEvent.click(await screen.findByLabelText('Active: Montage'));
    await waitFor(() => expect(engine.setTransitionStyleActive).toHaveBeenCalledWith('montage', true));
    await waitFor(() => expect(screen.getByLabelText('Active: Montage')).toBeChecked());
    fireEvent.click(screen.getByLabelText('Active: Classic'));
    await waitFor(() => expect(engine.setTransitionStyleActive).toHaveBeenCalledWith('classic', false));
    await waitFor(() => expect(screen.getByText('3 of 4 styles active')).toBeInTheDocument());
  });

  it('reports a failed save instead of pretending it worked', async () => {
    const notice = vi.fn();
    engine.setTransitionStyleActive.mockRejectedValue(new Error('engine unavailable'));
    render(<TransitionsPanel onNotice={notice} />);
    fireEvent.click(await screen.findByLabelText('Active: Montage'));
    await waitFor(() => expect(notice).toHaveBeenCalledWith('engine unavailable'));
    expect(screen.getByLabelText('Active: Montage')).not.toBeChecked();
  });
});
