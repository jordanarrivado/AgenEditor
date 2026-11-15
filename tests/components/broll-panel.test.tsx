import React from 'react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { BrollPanel } from '../../apps/desktop/src/features/live/BrollPanel';
import type { EngineAsset, EngineAssetChecklist } from '../../apps/desktop/src/services/liveEngineRepository';

const engine = vi.hoisted(() => ({
  assetUrl: vi.fn(), openGenerator: vi.fn(), approveChecklistRequest: vi.fn(), resetChecklistRequest: vi.fn(),
  saveChecklist: vi.fn(), removeAsset: vi.fn(),
}));
vi.mock('../../apps/desktop/src/services/liveEngineRepository', () => ({ liveEngineRepository: engine }));

const storyboard = { id: 'broll_01_storyboard', kind: 'image' as const, purpose: 'Storyboard for the street shot', prompt: 'Make a 4-frame grid', aspect: '9:16' };
const video = { id: 'broll_01', kind: 'video' as const, purpose: 'Street shot', prompt: 'Camera pushes in', aspect: '9:16' };
const checklist = (extra: Partial<EngineAssetChecklist> = {}): EngineAssetChecklist => ({
  schema_version: 1, project_id: 'project_a', version: 1, configured: true, requests: [storyboard, video],
  links: {}, accepted: {}, review_required: true, approved: [], ...extra,
});
const asset = (id: string, kind: string, name: string): EngineAsset => ({
  id, role: kind === 'image' ? 'reference' : 'broll', kind, original_filename: name, sha256: '', size_bytes: 1, video: null, audio: null,
  provenance: { origin: 'generated-external', tool_name: 'ChatGPT' },
} as unknown as EngineAsset);

const notice = vi.fn();
const onBrowse = vi.fn();
const onChange = vi.fn();
const writeText = vi.fn();

const renderPanel = (list: EngineAssetChecklist, assets: EngineAsset[] = []) =>
  render(<BrollPanel projectId="project_a" checklist={list} assets={assets} attaching={null} onBrowse={onBrowse} onChange={onChange} onNotice={notice} />);
const card = (id: string) => within(screen.getByTestId(`broll-${id}`));

beforeEach(() => {
  vi.resetAllMocks();
  writeText.mockResolvedValue(undefined);
  Object.defineProperty(navigator, 'clipboard', { value: { writeText }, configurable: true });
  engine.assetUrl.mockResolvedValue('asset://preview');
  engine.openGenerator.mockResolvedValue(undefined);
  engine.removeAsset.mockResolvedValue({ removed: 'x' });
});

describe('B-roll panel', () => {
  it('copies the storyboard prompt and opens ChatGPT; the video waits for the storyboard', async () => {
    renderPanel(checklist());
    expect(screen.getByText('0 of 2 ready · Execute is locked')).toBeInTheDocument();
    fireEvent.click(card('broll_01_storyboard').getByRole('button', { name: /Copy prompt & open ChatGPT/ }));
    await waitFor(() => expect(writeText).toHaveBeenCalledWith('Make a 4-frame grid'));
    await waitFor(() => expect(engine.openGenerator).toHaveBeenCalledWith('chatgpt'));
    await waitFor(() => expect(notice).toHaveBeenCalledWith(expect.stringContaining('Prompt copied. ChatGPT is opening')));
    expect(card('broll_01').getByText('Waiting for storyboard')).toBeInTheDocument();
    expect(card('broll_01').queryByRole('button', { name: /Copy prompt/ })).toBeNull();
  });

  it('opens Google Flow for a video once the storyboard is approved', async () => {
    renderPanel(checklist({ links: { broll_01_storyboard: 'asset_s' }, approved: ['broll_01_storyboard'] }), [asset('asset_s', 'image', 'grid.png')]);
    expect(card('broll_01').getByText(/attach the approved storyboard/)).toBeInTheDocument();
    fireEvent.click(card('broll_01').getByRole('button', { name: /Copy prompt & open Google Flow/ }));
    await waitFor(() => expect(writeText).toHaveBeenCalledWith('Camera pushes in'));
    await waitFor(() => expect(engine.openGenerator).toHaveBeenCalledWith('flow'));
  });

  it('does not open the site when copying fails, and says so', async () => {
    writeText.mockRejectedValue(new Error('denied'));
    document.execCommand = vi.fn(() => false);
    renderPanel(checklist());
    fireEvent.click(card('broll_01_storyboard').getByRole('button', { name: /Copy prompt/ }));
    await waitFor(() => expect(notice).toHaveBeenCalledWith(expect.stringContaining('Could not copy automatically')));
    expect(engine.openGenerator).not.toHaveBeenCalled();
  });

  it('shows a file for review with a preview and approves it', async () => {
    const list = checklist({ links: { broll_01_storyboard: 'asset_s' } });
    const approved = checklist({ version: 2, links: { broll_01_storyboard: 'asset_s' }, approved: ['broll_01_storyboard'] });
    engine.approveChecklistRequest.mockResolvedValue(approved);
    renderPanel(list, [asset('asset_s', 'image', 'grid.png')]);
    expect(card('broll_01_storyboard').getByText('Needs your review')).toBeInTheDocument();
    expect(await card('broll_01_storyboard').findByAltText('Preview of grid.png')).toBeInTheDocument();
    fireEvent.click(card('broll_01_storyboard').getByRole('button', { name: 'Approve storyboard' }));
    await waitFor(() => expect(engine.approveChecklistRequest).toHaveBeenCalledWith('project_a', 'broll_01_storyboard', 1));
    expect(onChange).toHaveBeenCalledWith(approved);
  });

  it('redo resets the request, removes the old file and copies the prompt again', async () => {
    const list = checklist({ links: { broll_01_storyboard: 'asset_s' } });
    engine.resetChecklistRequest.mockResolvedValue(checklist({ version: 2 }));
    renderPanel(list, [asset('asset_s', 'image', 'grid.png')]);
    fireEvent.click(card('broll_01_storyboard').getByRole('button', { name: /Redo/ }));
    await waitFor(() => expect(engine.resetChecklistRequest).toHaveBeenCalledWith('project_a', 'broll_01_storyboard', 1));
    await waitFor(() => expect(engine.removeAsset).toHaveBeenCalledWith('project_a', 'asset_s'));
    await waitFor(() => expect(writeText).toHaveBeenCalledWith('Make a 4-frame grid'));
  });

  it('skips a request and can undo the skip', async () => {
    engine.saveChecklist.mockResolvedValue(checklist({ version: 2, links: { broll_01_storyboard: 'dropped' } }));
    renderPanel(checklist());
    fireEvent.click(card('broll_01_storyboard').getByRole('button', { name: /Skip/ }));
    await waitFor(() => expect(engine.saveChecklist).toHaveBeenCalledWith('project_a', expect.objectContaining({ links: { broll_01_storyboard: 'dropped' }, review_required: true, approved: [] }), 1));
  });

  it('reports a skipped request with undo, and a complete list as ready', () => {
    renderPanel(checklist({ links: { broll_01_storyboard: 'dropped', broll_01: 'dropped' } }));
    expect(screen.getByText('2 of 2 ready')).toBeInTheDocument();
    expect(card('broll_01').getByRole('button', { name: /Undo skip/ })).toBeInTheDocument();
  });

  it('clears the whole list only after a second click', async () => {
    engine.saveChecklist.mockResolvedValue({ ...checklist({ version: 2 }), configured: false, requests: [], links: {} });
    renderPanel(checklist({ links: { broll_01_storyboard: 'asset_s' } }), [asset('asset_s', 'image', 'grid.png')]);
    fireEvent.click(screen.getByRole('button', { name: 'Clear list' }));
    expect(engine.saveChecklist).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole('button', { name: 'Click again to clear' }));
    await waitFor(() => expect(engine.saveChecklist).toHaveBeenCalledWith('project_a', null, 1));
    await waitFor(() => expect(notice).toHaveBeenCalledWith('B-roll list cleared. Files you already added stay in your Assets.'));
    expect(onChange).toHaveBeenCalledWith(expect.objectContaining({ configured: false, requests: [] }));
  });

  it('browse hands the request to the page', () => {
    renderPanel(checklist());
    fireEvent.click(card('broll_01_storyboard').getByRole('button', { name: /Browse/ }));
    expect(onBrowse).toHaveBeenCalledWith(storyboard);
  });

  it('shows an engine refusal instead of failing silently', async () => {
    engine.resetChecklistRequest.mockRejectedValue('{"code":"INVALID_ARGUMENT","message":"reset the video first; it was made from this storyboard","details":{}}');
    renderPanel(checklist({ links: { broll_01_storyboard: 'asset_s', broll_01: 'asset_v' }, approved: ['broll_01_storyboard'] }),
      [asset('asset_s', 'image', 'grid.png'), asset('asset_v', 'video', 'clip.mp4')]);
    fireEvent.click(card('broll_01_storyboard').getByRole('button', { name: /Redo/ }));
    await waitFor(() => expect(notice).toHaveBeenCalledWith('reset the video first; it was made from this storyboard'));
    expect(engine.removeAsset).not.toHaveBeenCalled();
  });
});
