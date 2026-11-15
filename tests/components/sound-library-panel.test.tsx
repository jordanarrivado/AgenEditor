import React from 'react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { SoundLibraryPanel } from '../../apps/desktop/src/features/live/SoundLibraryPanel';
import type { SoundCatalogEntry } from '../../apps/desktop/src/services/liveEngineRepository';

const engine = vi.hoisted(() => ({
  listSoundPacks: vi.fn(), installSoundPack: vi.fn(), setSoundPackActive: vi.fn(), removeSoundPack: vi.fn(),
  listSoundCatalog: vi.fn(), downloadSoundPack: vi.fn(),
}));
vi.mock('../../apps/desktop/src/services/liveEngineRepository', () => ({ liveEngineRepository: engine }));

const pack = (id: string, name: string, active: boolean, extra = {}) => ({
  id, name, active, license: 'cc0', credit: '', source: '', catalog_id: null, installed_at: '', sound_count: 12, ...extra,
});
const entry = (id: string, name: string, extra: Partial<SoundCatalogEntry> = {}): SoundCatalogEntry => ({
  id, name, description: 'Clicks and pops.', author: 'Kenney', license: 'cc0', page_url: 'https://opengameart.org/content/x',
  download_url: 'https://opengameart.org/sites/default/files/x.zip', sha256: 'a'.repeat(64), size_bytes: 1_425_902,
  sound_count: 51, bundled_file: null, installed_pack_id: null, ...extra,
});

let packs: ReturnType<typeof pack>[] = [];
let catalog: SoundCatalogEntry[] = [];
const notice = vi.fn();

beforeEach(() => {
  vi.resetAllMocks();
  packs = [pack('pack_a', 'Kenney UI', true), pack('pack_b', 'Freesound picks', false, { license: 'cc_by', credit: 'Sounds by alice (CC BY 4.0)' })];
  engine.listSoundPacks.mockImplementation(async () => packs);
  catalog = [
    entry('kenney-interface-sounds', 'Interface Sounds (Kenney)', { bundled_file: 'kenney_interfaceSounds.zip', installed_pack_id: 'pack_a' }),
    entry('kenney-ui-sfx', 'UI Sound Effects (Kenney)'),
  ];
  engine.listSoundCatalog.mockImplementation(async () => catalog);
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
    fireEvent.click(await screen.findByRole('button', { name: /Install from folder/ }));
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

  it('shows free packs and installs one with a single click', async () => {
    engine.downloadSoundPack.mockImplementation(async (id: string) => {
      const installed = pack('pack_d', 'UI Sound Effects (Kenney)', true, { catalog_id: id, sound_count: 51 });
      packs = [...packs, installed];
      catalog = catalog.map((item) => (item.id === id ? { ...item, installed_pack_id: 'pack_d' } : item));
      return { pack: installed, skipped: [] };
    });
    render(<SoundLibraryPanel onNotice={notice} />);
    const list = await screen.findByRole('list', { name: 'Free sound packs' });
    expect(list).toHaveTextContent('Interface Sounds (Kenney)');
    expect(list).toHaveTextContent('included with AgenEditor');
    expect(list).toHaveTextContent('1.4 MB');
    fireEvent.click(screen.getByRole('button', { name: 'Download and install UI Sound Effects (Kenney)' }));
    await waitFor(() => expect(engine.downloadSoundPack).toHaveBeenCalledWith('kenney-ui-sfx'));
    await waitFor(() => expect(notice).toHaveBeenCalledWith('Installed "UI Sound Effects (Kenney)" with 51 sounds. It is active.'));
    expect(await screen.findByLabelText('Active: UI Sound Effects (Kenney)')).toBeChecked();
    expect(screen.queryByRole('button', { name: /Download and install/ })).toBeNull();
  });

  it('reports a failed download with the engine message only', async () => {
    engine.downloadSoundPack.mockRejectedValue('{"code":"DOWNLOAD_FAILED","message":"could not download UI Sound Effects (Kenney); check your connection","details":{}}');
    render(<SoundLibraryPanel onNotice={notice} />);
    fireEvent.click(await screen.findByRole('button', { name: 'Download and install UI Sound Effects (Kenney)' }));
    await waitFor(() => expect(notice).toHaveBeenCalledWith('UI Sound Effects (Kenney) not installed: could not download UI Sound Effects (Kenney); check your connection'));
    expect(screen.getByRole('button', { name: 'Download and install UI Sound Effects (Kenney)' })).toBeEnabled();
  });

  it('keeps the form open and silent when the folder picker is cancelled', async () => {
    engine.installSoundPack.mockResolvedValue(null);
    render(<SoundLibraryPanel onNotice={notice} />);
    fireEvent.click(await screen.findByRole('button', { name: /Install from folder/ }));
    fireEvent.change(screen.getByLabelText('Pack name'), { target: { value: 'Pops' } });
    fireEvent.click(screen.getByRole('button', { name: 'Choose folder & install' }));
    await waitFor(() => expect(engine.installSoundPack).toHaveBeenCalled());
    expect(screen.getByLabelText('Pack name')).toHaveValue('Pops');
    expect(notice).not.toHaveBeenCalled();
  });
});
