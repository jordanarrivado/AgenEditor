import { describe, expect, it } from 'vitest';
import { DROPPED, checklistReady, loadChecklist, mergePlanRequests, requestStatus, saveChecklist, type AssetChecklist } from '../../apps/desktop/src/utils/assetChecklist';

function memoryStorage() {
  const data = new Map<string, string>();
  return {
    getItem: (k: string) => data.get(k) ?? null,
    setItem: (k: string, v: string) => void data.set(k, v),
    removeItem: (k: string) => void data.delete(k),
  };
}

const list: AssetChecklist = {
  requests: [
    { id: 'gen_a', kind: 'video', purpose: 'b-roll', prompt: 'a road', aspect: '9:16' },
    { id: 'gen_b', kind: 'image', purpose: 'hook', prompt: 'a frame', aspect: '9:16' },
  ],
  links: { gen_a: 'asset_1', gen_b: DROPPED },
};

describe('asset checklist', () => {
  it('round-trips through storage per project', () => {
    const s = memoryStorage();
    saveChecklist('p1', list, s);
    expect(loadChecklist('p1', s)).toEqual(list);
    expect(loadChecklist('p2', s)).toBeNull();
    saveChecklist('p1', null, s);
    expect(loadChecklist('p1', s)).toBeNull();
  });

  it('ignores corrupt data and links to unknown requests', () => {
    const s = memoryStorage();
    s.setItem('ageneditor.assetChecklist.v1.p1', '{not json');
    expect(loadChecklist('p1', s)).toBeNull();
    s.setItem('ageneditor.assetChecklist.v1.p1', JSON.stringify({ requests: list.requests, links: { ghost: 'x', gen_a: 'asset_1' } }));
    expect(loadChecklist('p1', s)?.links).toEqual({ gen_a: 'asset_1' });
  });

  it('is ready only while the linked asset is still registered', () => {
    expect(requestStatus(list, 'gen_a', new Set(['asset_1']))).toBe('ready');
    expect(requestStatus(list, 'gen_a', new Set())).toBe('pending');
    expect(requestStatus(list, 'gen_b', new Set())).toBe('dropped');
    expect(checklistReady(list, new Set(['asset_1']))).toBe(true);
    expect(checklistReady(list, new Set())).toBe(false);
  });
});

describe('review notes across a re-plan', () => {
  it('keeps a note only while its request keeps the same approved file', () => {
    const keep = { id: 'a', kind: 'image' as const, purpose: '', prompt: 'same', aspect: '9:16' };
    const changed = { id: 'b', kind: 'image' as const, purpose: '', prompt: 'old', aspect: '9:16' };
    const previous: AssetChecklist = {
      requests: [keep, changed], links: { a: 'asset_a', b: 'asset_b' }, approved: ['a', 'b'],
      review_notes: { a: { asset_id: 'asset_a', text: 'first 3 s' }, b: { asset_id: 'asset_b', text: 'stale' } },
    };
    const merged = mergePlanRequests(previous, [keep, { ...changed, prompt: 'new' }]);
    expect(merged.review_notes).toEqual({ a: { asset_id: 'asset_a', text: 'first 3 s' } });
  });
});
