import { describe, expect, it } from 'vitest';
import { mergePlanRequests, reviewProgress, reviewState, type AssetChecklist } from '../../apps/desktop/src/utils/assetChecklist';

const storyboard = { id: 'broll_01_storyboard', kind: 'image' as const, purpose: 'storyboard', prompt: 'A grid', aspect: '9:16' };
const video = { id: 'broll_01', kind: 'video' as const, purpose: 'street', prompt: 'Push in', aspect: '9:16' };
const list = (extra: Partial<AssetChecklist> = {}): AssetChecklist => ({
  requests: [storyboard, video], links: {}, approved: [], review_required: true, ...extra,
});

describe('B-roll review states', () => {
  it('walks a video through storyboard first', () => {
    expect(reviewState(list(), 'broll_01_storyboard')).toBe('waiting');
    expect(reviewState(list(), 'broll_01')).toBe('locked');
    const added = list({ links: { broll_01_storyboard: 'asset_s' } });
    expect(reviewState(added, 'broll_01_storyboard')).toBe('review');
    expect(reviewState(added, 'broll_01')).toBe('locked');
    const approved = list({ links: { broll_01_storyboard: 'asset_s' }, approved: ['broll_01_storyboard'] });
    expect(reviewState(approved, 'broll_01_storyboard')).toBe('approved');
    expect(reviewState(approved, 'broll_01')).toBe('waiting');
  });

  it('unlocks the video when the storyboard is skipped', () => {
    expect(reviewState(list({ links: { broll_01_storyboard: 'dropped' } }), 'broll_01')).toBe('waiting');
  });

  it('counts approved and skipped as done, and an empty list as complete', () => {
    expect(reviewProgress(null)).toEqual({ done: 0, total: 0, complete: true });
    expect(reviewProgress(list())).toEqual({ done: 0, total: 2, complete: false });
    const half = list({ links: { broll_01_storyboard: 'asset_s', broll_01: 'dropped' } });
    expect(reviewProgress(half)).toEqual({ done: 1, total: 2, complete: false });
    const all = list({ links: { broll_01_storyboard: 'asset_s', broll_01: 'dropped' }, approved: ['broll_01_storyboard'] });
    expect(reviewProgress(all)).toEqual({ done: 2, total: 2, complete: true });
  });

  it('treats a linked file as ready right away when review is not required', () => {
    expect(reviewState(list({ review_required: false, links: { broll_01_storyboard: 'asset_s' } }), 'broll_01_storyboard')).toBe('approved');
  });
});

describe('merging a new plan into the saved list', () => {
  const previous = list({ links: { broll_01_storyboard: 'asset_s', broll_01: 'asset_v' }, approved: ['broll_01_storyboard'] });

  it('keeps files and approvals for unchanged requests', () => {
    const merged = mergePlanRequests(previous, [storyboard, video]);
    expect(merged.links).toEqual({ broll_01_storyboard: 'asset_s', broll_01: 'asset_v' });
    expect(merged.approved).toEqual(['broll_01_storyboard']);
    expect(merged.review_required).toBe(true);
  });

  it('asks for a new file when a prompt or kind changed, and drops removed requests', () => {
    const merged = mergePlanRequests(previous, [{ ...storyboard, prompt: 'A different grid' }, { ...video, id: 'broll_02' }]);
    expect(merged.links).toEqual({});
    expect(merged.approved).toEqual([]);
    expect(merged.requests.map((request) => request.id)).toEqual(['broll_01_storyboard', 'broll_02']);
  });

  it('drops a video whose storyboard changed, even though the video itself did not', () => {
    const merged = mergePlanRequests(previous, [{ ...storyboard, prompt: 'A new grid' }, video]);
    expect(merged.links).toEqual({}); // storyboard must be made again, so the video built on it is not ready either
    expect(merged.approved).toEqual([]);
    expect(reviewProgress(merged).complete).toBe(false);
  });

  it('keeps a video whose storyboard was skipped', () => {
    const skipped = list({ links: { broll_01_storyboard: 'dropped', broll_01: 'asset_v' }, approved: ['broll_01'] });
    const merged = mergePlanRequests(skipped, [storyboard, video]);
    expect(merged.links).toEqual({ broll_01_storyboard: 'dropped', broll_01: 'asset_v' });
    expect(merged.approved).toEqual(['broll_01']);
  });

  it('starts fresh with no previous list', () => {
    expect(mergePlanRequests(null, [video]).links).toEqual({});
  });
});
