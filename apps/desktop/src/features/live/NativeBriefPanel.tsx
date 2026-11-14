import React, { useEffect, useState } from 'react';
import type { EngineBrief, EngineBriefItem } from '../../services/liveEngineRepository';

const button = 'rounded-lg border border-line px-2.5 py-1.5 text-xs font-medium text-fg hover:bg-elevated disabled:opacity-40 disabled:cursor-not-allowed';
const field = 'w-full rounded-lg border border-line bg-app px-2 py-1.5 text-sm text-fg';

export type BriefDraft = Pick<EngineBrief, 'brief_text' | 'script_text' | 'reference_note' | 'checklist'>;
const toDraft = (brief: EngineBrief): BriefDraft => ({
  brief_text: brief.brief_text,
  script_text: brief.script_text,
  reference_note: brief.reference_note,
  checklist: brief.checklist,
});

/**
 * Client brief, script, reference note and requirement checklist. A person writes
 * and confirms all of it; agents read it through MCP. Nothing here checks the video.
 */
export const NativeBriefPanel: React.FC<{
  brief: EngineBrief | null;
  busy: boolean;
  onSave: (draft: BriefDraft) => void;
}> = ({ brief, busy, onSave }) => {
  const [draft, setDraft] = useState<BriefDraft | null>(brief ? toDraft(brief) : null);
  const [newItem, setNewItem] = useState('');
  useEffect(() => { setDraft(brief ? toDraft(brief) : null); }, [brief]);
  if (!brief || !draft) return null;

  const dirty = JSON.stringify(draft) !== JSON.stringify(toDraft(brief));
  const setItems = (checklist: EngineBriefItem[]) => setDraft({ ...draft, checklist });
  const patchItem = (id: string, patch: Partial<EngineBriefItem>) =>
    setItems(draft.checklist.map((item) => (item.id === id ? { ...item, ...patch } : item)));
  const addItem = () => {
    const text = newItem.trim();
    if (!text) return;
    setItems([...draft.checklist, { id: `item_${crypto.randomUUID().replace(/-/g, '').slice(0, 12)}`, text, status: 'open', note: '' }]);
    setNewItem('');
  };
  const confirmed = draft.checklist.filter((item) => item.status !== 'open').length;

  return (
    <section data-testid="brief-panel" className="rounded-xl border border-line bg-surface p-4 space-y-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h3 className="font-semibold">Instructions for your AI editor</h3>
        <span className="text-xs text-fg-3">{brief.version === 0 ? 'Not saved yet' : `Saved version ${brief.version}`}{dirty && ' · unsaved changes'}</span>
      </div>
      <p className="text-xs text-fg-3">Your AI editor reads this through MCP. Only you can change it or mark a requirement met. AgenEditor does not check the video against it.</p>
      <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
        <label className="text-xs text-fg-2">Instructions
          <textarea className={`${field} mt-1`} rows={4} placeholder="What should the edit say, for whom, how long, what style? Anything the editor must keep or avoid." maxLength={20000} value={draft.brief_text} onChange={(e) => setDraft({ ...draft, brief_text: e.target.value })} />
        </label>
        <label className="text-xs text-fg-2">Script (optional)
          <textarea className={`${field} mt-1`} rows={4} maxLength={20000} value={draft.script_text} onChange={(e) => setDraft({ ...draft, script_text: e.target.value })} />
        </label>
      </div>
      <label className="text-xs text-fg-2 block">Reference video (a description or link; it is not imported)
        <input className={`${field} mt-1`} maxLength={1000} value={draft.reference_note} onChange={(e) => setDraft({ ...draft, reference_note: e.target.value })} />
      </label>

      <div className="space-y-1.5">
        <h3 className="text-sm font-medium">Requirements <span className="text-xs text-fg-3" data-testid="brief-confirmed">{confirmed} of {draft.checklist.length} confirmed</span></h3>
        <ul className="space-y-1.5" aria-label="Brief requirements">
          {draft.checklist.map((item) => (
            <li key={item.id} data-testid={`brief-item-${item.id}`} className="flex flex-wrap items-center gap-2 rounded-lg border border-line p-2">
              <span className="flex-1 min-w-[10rem] text-sm">{item.text}</span>
              <select aria-label={`Status of ${item.text}`} className="rounded border border-line bg-app p-1 text-xs" value={item.status}
                onChange={(e) => patchItem(item.id, { status: e.target.value as EngineBriefItem['status'] })}>
                <option value="open">Open</option>
                <option value="met">Met</option>
                <option value="waived">Waived</option>
              </select>
              <button className={button} onClick={() => setItems(draft.checklist.filter((other) => other.id !== item.id))} aria-label={`Remove ${item.text}`}>Remove</button>
            </li>
          ))}
        </ul>
        <div className="flex gap-2 items-end">
          <label className="text-xs text-fg-2 flex-1">New requirement
            <input className={`${field} mt-1`} maxLength={300} value={newItem} onChange={(e) => setNewItem(e.target.value)}
              onKeyDown={(e) => { if (e.key === 'Enter') { e.preventDefault(); addItem(); } }} />
          </label>
          <button className={button} disabled={!newItem.trim() || draft.checklist.length >= 50} onClick={addItem}>Add requirement</button>
        </div>
      </div>
      <div className="flex gap-2">
        <button className={button} disabled={busy || !dirty} onClick={() => onSave(draft)}>Save brief</button>
        <button className={button} disabled={busy || !dirty} onClick={() => setDraft(toDraft(brief))}>Discard changes</button>
      </div>
    </section>
  );
};
