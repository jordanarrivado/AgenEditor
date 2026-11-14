import React from 'react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { SoundLibraryPanel } from '../../apps/desktop/src/features/live/SoundLibraryPanel';

const engine = vi.hoisted(() => ({
  listSoundPacks: vi.fn(), installSoundPack: vi.fn(), setSoundPackActive: vi.fn(), removeSoundPack: vi.fn(),
}));
vi.mock('../../apps/desktop/src/services/liveEngineRepository', () => ({ liveEngineRepository: engine }));

const pack = (id: string, name: string, active: boolean, extra = {}) => ({
  id, name, active, license: 'cc0', credit: '', source: '', installed_at: '', sound_count: 12, ...extra,
});

let packs: ReturnType<typeof pack>[] = [];
const notice = vi.fn();

beforeEach(() => {
  vi.resetAllMocks();
  packs = [pack('pack_a', 'Kenney UI', true), pack('pack_b', 'Freesound picks', false, { license: 'cc_by', credit: 'Sounds by alice (CC BY 4.0)' })];
  engine.listSoundPacks.mockImplementation(async () => packs);
  engine.setSoundPackActive.mockImplementation(async (id: string, active: boolean) => {
    packs = packs.map((p) => (p.id === id ? { ...p, active } : p));
    return packs.find((p) => p.id === id);
  });
  engine.removeSoundPack.mockImplementation(async (id: string) => { packs = packs.filter((p) => p.id !== id); return { removed: id }; });
});

describe('sound library panel', () => {
  it('lists packs with active state, licence and credit', async () => {
    render(<SoundLibraryPanel onNotice={notice} />);
    expect(await screen.findByLabelText('Active: Kenney UI')).toBeChecked();
    expect(screen.getByLabelText('Active: Freesound picks')).not.toBeChecked();
    expect(screen.getByText('1 of 2 active')).toBeInTheDocument();
    expect(screen.getByText('Credit: Sounds by alice (CC BY 4.0)')).toBeInTheDocument();
  });

  it('ticking a pack makes it active in the engine', async () => {
    render(<SoundLibraryPanel onNotice={notice} />);
    fireEvent.click(await screen.findByLabelText('Active: Freesound picks'));
    await waitFor(() => expect(engine.setSoundPackActive).toHaveBeenCalledWith('pack_b', true));
    await waitFor(() => expect(screen.getByLabelText('Active: Freesound picks')).toBeChecked());
    expect(screen.getByText('2 of 2 active')).toBeInTheDocument();
  });

  it('asks for a second click before removing a pack', async () => {
    render(<SoundLibraryPanel onNotice={notice} />);
    await screen.findByLabelText('Active: Kenney UI');
    fireEvent.click(screen.getAllByRole('button', { name: 'Remove' })[0]);
    expect(engine.removeSoundPack).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole('button', { name: 'Click again to remove' }));
    await waitFor(() => expect(engine.removeSoundPack).toHaveBeenCalledWith('pack_a'));
    await waitFor(() => expect(screen.queryByLabelText('Active: Kenney UI')).toBeNull());
  });

  it('requires a credit line for CC BY before picking a folder, then reports skipped files', async () => {
    engine.installSoundPack.mockResolvedValue({ pack: pack('pack_c', 'Hits', true, { sound_count: 3 }), skipped: [{ file: 'drone.wav', reason: 'longer than 60 seconds' }] });
    render(<SoundLibraryPanel onNotice={notice} />);
    fireEvent.click(await screen.findByRole('button', { name: /Install pack/ }));
    fireEvent.change(screen.getByLabelText('Pack name'), { target: { value: 'Hits' } });
    fireEvent.change(screen.getByLabelText('Licence'), { target: { value: 'cc_by' } });
    const installButton = screen.getByRole('button', { name: 'Choose folder & install' });
    expect(installButton).toBeDisabled();
    expect(screen.getByText(/CC BY sounds need the credit line/)).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText(/Credit line/), { target: { value: ' Sounds by bob ' } });
    fireEvent.click(installButton);
    await waitFor(() => expect(engine.installSoundPack).toHaveBeenCalledWith({ name: 'Hits', license: 'cc_by', credit: 'Sounds by bob', source: '' }));
    await waitFor(() => expect(notice).toHaveBeenCalledWith('Installed "Hits" with 3 sounds. It is active.\nSkipped drone.wav: longer than 60 seconds'));
  });

  it('keeps the form open and silent when the folder picker is cancelled', async () => {
    engine.installSoundPack.mockResolvedValue(null);
    render(<SoundLibraryPanel onNotice={notice} />);
    fireEvent.click(await screen.findByRole('button', { name: /Install pack/ }));
    fireEvent.change(screen.getByLabelText('Pack name'), { target: { value: 'Pops' } });
    fireEvent.click(screen.getByRole('button', { name: 'Choose folder & install' }));
    await waitFor(() => expect(engine.installSoundPack).toHaveBeenCalled());
    expect(screen.getByLabelText('Pack name')).toHaveValue('Pops');
    expect(notice).not.toHaveBeenCalled();
  });
});
