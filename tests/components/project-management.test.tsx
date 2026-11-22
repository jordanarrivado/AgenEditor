import React from 'react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { HomeView } from '../../apps/desktop/src/features/live/HomeView';
import { EditorPresetsPanel } from '../../apps/desktop/src/features/live/EditorPresetsPanel';
import type { EngineEditorPresetLibrary, EngineProject } from '../../apps/desktop/src/services/liveEngineRepository';

const engine = vi.hoisted(() => ({
  listEditorPresets: vi.fn(), projectSettings: vi.fn(), saveEditorPreset: vi.fn(), deleteEditorPreset: vi.fn(), chooseEditorPreset: vi.fn(),
}));
vi.mock('../../apps/desktop/src/services/liveEngineRepository', () => ({ liveEngineRepository: engine }));

const projects: EngineProject[] = [
  { id: 'p1', name: 'Launch reel', created_at: '2026-11-20T10:00:00Z', current_revision: 3 },
  { id: 'p2', name: 'Old test', created_at: '2026-11-01T10:00:00Z', current_revision: 1, archived: true },
];
const home = (extra: Partial<React.ComponentProps<typeof HomeView>> = {}) => render(
  <HomeView projects={projects} creating={false} newName="" busy={false} onNewName={vi.fn()} onStartCreate={vi.fn()}
    onCancelCreate={vi.fn()} onCreate={vi.fn()} onOpen={vi.fn()} {...extra} />,
);

const library: EngineEditorPresetLibrary = {
  version: 2,
  presets: [{
    id: 'preset_a', name: 'House style', caption_preset: 'top_clean', color_preset: 'warm', broll_color_match: true,
    audio: { speech_repair: 'light', duck_music: true, normalize: true }, created_at: '',
  }],
};

beforeEach(() => {
  vi.resetAllMocks();
  engine.listEditorPresets.mockResolvedValue(library);
  engine.projectSettings.mockResolvedValue({ archived: false, editor_preset_id: null });
});

describe('project management on Home', () => {
  it('hides archived projects until asked, and archives or restores one', async () => {
    const onArchive = vi.fn().mockResolvedValue(undefined);
    home({ onArchive, onRename: vi.fn() });
    const list = screen.getByRole('list', { name: 'Projects' });
    expect(within(list).getByText('Launch reel')).toBeInTheDocument();
    expect(within(list).queryByText('Old test')).toBeNull();
    fireEvent.click(screen.getByLabelText(/Show archived projects \(1\)/));
    expect(within(list).getByText('Old test')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Unarchive Old test' }));
    await waitFor(() => expect(onArchive).toHaveBeenCalledWith('p2', false));
    fireEvent.click(screen.getByRole('button', { name: 'Archive Launch reel' }));
    await waitFor(() => expect(onArchive).toHaveBeenCalledWith('p1', true));
  });

  it('renames a project inline', async () => {
    const onRename = vi.fn().mockResolvedValue(undefined);
    home({ onRename });
    fireEvent.click(screen.getByRole('button', { name: 'Rename Launch reel' }));
    const input = screen.getByLabelText('New name for Launch reel');
    fireEvent.change(input, { target: { value: '  Spring launch ' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save' }));
    await waitFor(() => expect(onRename).toHaveBeenCalledWith('p1', 'Spring launch'));
  });
});

describe('editor presets panel', () => {
  it('lists presets, picks one for the project and saves the current settings as a new one', async () => {
    engine.chooseEditorPreset.mockResolvedValue({ project_id: 'p1', editor_preset_id: 'preset_a' });
    engine.saveEditorPreset.mockResolvedValue({ ...library, version: 3 });
    const notice = vi.fn();
    render(<EditorPresetsPanel projectId="p1" revision={3} onNotice={notice} />);
    expect(await screen.findByText('House style')).toBeInTheDocument();
    expect(screen.getByText(/captions top clean · warm grade · B-roll matched · light speech repair/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole('radio', { name: /House style/ }));
    await waitFor(() => expect(engine.chooseEditorPreset).toHaveBeenCalledWith('p1', 'preset_a'));
    expect(await screen.findByText(/Claude applies this preset the next time it edits/)).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText('New preset name'), { target: { value: 'Calm' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save' }));
    await waitFor(() => expect(engine.saveEditorPreset).toHaveBeenCalledWith('p1', 'Calm', 2));
  });

  it('asks before deleting and cannot save from a project with no edit', async () => {
    engine.deleteEditorPreset.mockResolvedValue({ version: 3, presets: [] });
    render(<EditorPresetsPanel projectId="p1" revision={0} onNotice={vi.fn()} />);
    expect(await screen.findByText(/there is none in this project yet/)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Save' })).toBeDisabled();
    fireEvent.click(screen.getByRole('button', { name: 'Delete House style' }));
    expect(engine.deleteEditorPreset).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole('button', { name: 'Confirm deleting House style' }));
    await waitFor(() => expect(engine.deleteEditorPreset).toHaveBeenCalledWith('preset_a', 2));
  });
});
