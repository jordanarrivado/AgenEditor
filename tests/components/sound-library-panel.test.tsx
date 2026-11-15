import React from 'react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { SoundLibraryPanel } from '../../apps/desktop/src/features/live/SoundLibraryPanel';
import type { SoundCatalogEntry, SoundPackSummary } from '../../apps/desktop/src/services/liveEngineRepository';

const engine = vi.hoisted(() => ({
  listSoundPacks: vi.fn(), installSoundPack: vi.fn(), setSoundPackActive: vi.fn(), removeSoundPack: vi.fn(),
  listSoundCatalog: vi.fn(), downloadSoundPack: vi.fn(),
}));
vi.mock('../../apps/desktop/src/services/liveEngineRepository', () => ({ liveEngineRepository: engine }));

const pack = (id: string, name: string, active: boolean, extra: Partial<SoundPackSummary> = {}): SoundPackSummary => ({
  id, name, active, license: 'cc0', credit: '', source: '', catalog_id: null, installed_at: '', sound_count: 12, ...extra,
});
const entry = (id: string, name: string, extra: Partial<SoundCatalogEntry> = {}): SoundCatalogEntry => ({
  id, name, description: 'Clicks and pops.', author: 'Kenney', license: 'cc0', page_url: 'https://opengameart.org/content/x',
  download_url: 'https://opengameart.org/sites/default/files/x.zip', sha256: 'a'.repeat(64), size_bytes: 1_425_902,
  sound_count: 51, bundled_file: null, installed_pack_id: null, ...extra,
});

let packs: SoundPackSummary[] = [];
let catalog: SoundCatalogEntry[] = [];
const notice = vi.fn();

const openDialog = async () => {
  fireEvent.click(await screen.findByRole('button', { name: /Manage sound packs/ }));
  return within(await screen.findByRole('dialog', { name: 'Sound effect packs' }));
};

beforeEach(() => {
  vi.resetAllMocks();
  packs = [
    pack('pack_a', 'Interface Sounds (Kenney)', true, { catalog_id: 'kenney-interface-sounds', sound_count: 100 }),
    pack('pack_b', 'Freesound picks', false, { license: 'cc_by', credit: 'Sounds by alice (CC BY 4.0)' }),
  ];
  catalog = [
    entry('kenney-interface-sounds', 'Interface Sounds', { bundled_file: 'kenney_interfaceSounds.zip', installed_pack_id: 'pack_a', description: 'Clicks, pops and dings.' }),
    entry('kenney-ui-sfx', 'UI Sound Effects'),
  ];
  engine.listSoundPacks.mockImplementation(async () => packs);
  engine.listSoundCatalog.mockImplementation(async () => catalog);
  engine.setSoundPackActive.mockImplementation(async (id: string, active: boolean) => {
    packs = packs.map((p) => (p.id === id ? { ...p, active } : p));
    return packs.find((p) => p.id === id);
  });
  engine.removeSoundPack.mockImplementation(async (id: string) => { packs = packs.filter((p) => p.id !== id); return { removed: id }; });
});

describe('sound library panel', () => {
  it('summarises packs in the sidebar and reports how many are on', async () => {
    const onActiveCount = vi.fn();
    render(<SoundLibraryPanel onNotice={notice} onActiveCount={onActiveCount} />);
    expect(await screen.findByText('1 of 2 packs on')).toBeInTheDocument();
    const summary = within(screen.getByRole('list', { name: 'Sound packs summary' }));
    expect(summary.getByText('Interface Sounds')).toBeInTheDocument(); // catalog name, not the stored one
    expect(summary.getByText('Off')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Manage sound packs · 1 free/ })).toBeInTheDocument();
    await waitFor(() => expect(onActiveCount).toHaveBeenLastCalledWith(1));
  });

  it('lists installed packs with on/off state, licence and credit in the dialog', async () => {
    render(<SoundLibraryPanel onNotice={notice} />);
    const dialog = await openDialog();
    expect(dialog.getByRole('switch', { name: 'On: Interface Sounds' })).toHaveAttribute('aria-checked', 'true');
    expect(dialog.getByRole('switch', { name: 'On: Freesound picks' })).toHaveAttribute('aria-checked', 'false');
    expect(dialog.getByText('Kenney · 100 sounds · CC0 · included with AgenEditor')).toBeInTheDocument();
    expect(dialog.getByText('Credit: Sounds by alice (CC BY 4.0)')).toBeInTheDocument();
  });

  it('turning a pack on saves it in the engine', async () => {
    render(<SoundLibraryPanel onNotice={notice} />);
    const dialog = await openDialog();
    fireEvent.click(dialog.getByRole('switch', { name: 'On: Freesound picks' }));
    await waitFor(() => expect(engine.setSoundPackActive).toHaveBeenCalledWith('pack_b', true));
    await waitFor(() => expect(dialog.getByRole('switch', { name: 'On: Freesound picks' })).toHaveAttribute('aria-checked', 'true'));
    expect(screen.getByText('2 of 2 packs on')).toBeInTheDocument();
  });

  it('asks for a second click before removing a pack', async () => {
    render(<SoundLibraryPanel onNotice={notice} />);
    const dialog = await openDialog();
    fireEvent.click(dialog.getAllByRole('button', { name: 'Remove' })[0]);
    expect(engine.removeSoundPack).not.toHaveBeenCalled();
    fireEvent.click(dialog.getByRole('button', { name: 'Click again to remove' }));
    await waitFor(() => expect(engine.removeSoundPack).toHaveBeenCalledWith('pack_a'));
    await waitFor(() => expect(dialog.queryByRole('switch', { name: 'On: Interface Sounds' })).toBeNull());
  });

  it('installs a free pack with one click and moves it to Installed', async () => {
    engine.downloadSoundPack.mockImplementation(async (id: string) => {
      const installed = pack('pack_d', 'UI Sound Effects', true, { catalog_id: id, sound_count: 51 });
      packs = [...packs, installed];
      catalog = catalog.map((item) => (item.id === id ? { ...item, installed_pack_id: 'pack_d' } : item));
      return { pack: installed, skipped: [] };
    });
    render(<SoundLibraryPanel onNotice={notice} />);
    const dialog = await openDialog();
    const free = within(dialog.getByRole('list', { name: 'Free sound packs' }));
    expect(free.getByText('51 sounds · 1.4 MB')).toBeInTheDocument();
    expect(free.queryByText('Interface Sounds')).toBeNull(); // already installed
    fireEvent.click(free.getByRole('button', { name: 'Install UI Sound Effects' }));
    await waitFor(() => expect(engine.downloadSoundPack).toHaveBeenCalledWith('kenney-ui-sfx'));
    await waitFor(() => expect(notice).toHaveBeenCalledWith('Installed "UI Sound Effects" with 51 sounds. It is on.'));
    expect(await dialog.findByRole('switch', { name: 'On: UI Sound Effects' })).toHaveAttribute('aria-checked', 'true');
    expect(dialog.getByText('All free packs are installed.')).toBeInTheDocument();
  });

  it('reports a failed download with the engine message only', async () => {
    engine.downloadSoundPack.mockRejectedValue('{"code":"DOWNLOAD_FAILED","message":"could not download UI Sound Effects; check your connection","details":{}}');
    render(<SoundLibraryPanel onNotice={notice} />);
    const dialog = await openDialog();
    fireEvent.click(dialog.getByRole('button', { name: 'Install UI Sound Effects' }));
    const text = 'UI Sound Effects not installed: could not download UI Sound Effects; check your connection';
    await waitFor(() => expect(notice).toHaveBeenCalledWith(text));
    expect(dialog.getByRole('status')).toHaveTextContent(text);
    expect(dialog.getByRole('button', { name: 'Install UI Sound Effects' })).toBeEnabled();
  });

  it('requires a credit line for CC BY before picking a folder, then reports skipped files', async () => {
    engine.installSoundPack.mockResolvedValue({ pack: pack('pack_c', 'Hits', true, { sound_count: 3 }), skipped: [{ file: 'drone.wav', reason: 'longer than 60 seconds' }] });
    render(<SoundLibraryPanel onNotice={notice} />);
    const dialog = await openDialog();
    fireEvent.click(dialog.getByRole('button', { name: /Install from folder/ }));
    fireEvent.change(dialog.getByLabelText('Pack name'), { target: { value: 'Hits' } });
    fireEvent.change(dialog.getByLabelText('Licence'), { target: { value: 'cc_by' } });
    const installButton = dialog.getByRole('button', { name: 'Choose folder & install' });
    expect(installButton).toBeDisabled();
    expect(dialog.getByText(/CC BY sounds need the credit line/)).toBeInTheDocument();
    fireEvent.change(dialog.getByLabelText(/Credit line/), { target: { value: ' Sounds by bob ' } });
    fireEvent.click(installButton);
    await waitFor(() => expect(engine.installSoundPack).toHaveBeenCalledWith({ name: 'Hits', license: 'cc_by', credit: 'Sounds by bob', source: '' }));
    await waitFor(() => expect(notice).toHaveBeenCalledWith('Installed "Hits" with 3 sounds. It is on.\nSkipped drone.wav: longer than 60 seconds'));
  });

  it('keeps the form open and silent when the folder picker is cancelled', async () => {
    engine.installSoundPack.mockResolvedValue(null);
    render(<SoundLibraryPanel onNotice={notice} />);
    const dialog = await openDialog();
    fireEvent.click(dialog.getByRole('button', { name: /Install from folder/ }));
    fireEvent.change(dialog.getByLabelText('Pack name'), { target: { value: 'Pops' } });
    fireEvent.click(dialog.getByRole('button', { name: 'Choose folder & install' }));
    await waitFor(() => expect(engine.installSoundPack).toHaveBeenCalled());
    expect(dialog.getByLabelText('Pack name')).toHaveValue('Pops');
    expect(notice).not.toHaveBeenCalled();
  });
});
