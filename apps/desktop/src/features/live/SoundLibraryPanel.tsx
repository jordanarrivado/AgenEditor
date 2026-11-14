import React, { useCallback, useEffect, useState } from 'react';
import { FolderInput, Loader2, Music2 } from 'lucide-react';
import {
  liveEngineRepository as engine,
  type SoundLicense,
  type SoundPackDetails,
  type SoundPackSummary,
} from '../../services/liveEngineRepository';

const button = 'inline-flex items-center justify-center gap-1.5 rounded-lg border border-line px-3 py-2 text-sm font-medium text-fg hover:bg-elevated disabled:opacity-40 disabled:cursor-not-allowed';
const primary = `${button} border-accent bg-accent text-on-accent hover:bg-accent-hover`;
const field = 'w-full rounded-lg border border-line bg-app px-3 py-1.5 text-sm text-fg outline-none focus:border-accent';

export const LICENSE_LABELS: Record<SoundLicense, string> = {
  cc0: 'CC0 · free, no credit',
  royalty_free: 'Royalty-free site licence (e.g. Pixabay, Mixkit)',
  cc_by: 'CC BY · free with credit',
  other: 'Other · check the terms',
};
const SHORT_LICENSE: Record<SoundLicense, string> = { cc0: 'CC0', royalty_free: 'Royalty-free', cc_by: 'CC BY', other: 'Other licence' };

const errorText = (error: unknown) => error instanceof Error ? error.message : String(error);
const EMPTY: SoundPackDetails = { name: '', license: 'cc0', credit: '', source: '' };

/**
 * Sound-effect packs the person installs from a folder they already downloaded. AgenEditor
 * copies the files; it never downloads sounds. Claude can only use packs ticked Active.
 */
export const SoundLibraryPanel: React.FC<{ disabled?: boolean; onNotice: (text: string) => void }> = ({ disabled, onNotice }) => {
  const [packs, setPacks] = useState<SoundPackSummary[] | null>(null);
  const [form, setForm] = useState<SoundPackDetails | null>(null);
  const [installing, setInstalling] = useState(false);
  const [pending, setPending] = useState<string | null>(null);
  const [confirmRemove, setConfirmRemove] = useState<string | null>(null);

  const refresh = useCallback(async () => setPacks(await engine.listSoundPacks()), []);
  useEffect(() => { void refresh().catch((error) => onNotice(`Sound library unavailable: ${errorText(error)}`)); }, [refresh, onNotice]);

  const formProblem = !form ? null
    : !form.name.trim() ? 'Name the pack.'
      : form.license === 'cc_by' && !form.credit.trim() ? 'CC BY sounds need the credit line you must publish.'
        : null;

  const install = () => {
    if (!form || formProblem) return;
    setInstalling(true);
    void engine.installSoundPack({ ...form, name: form.name.trim(), credit: form.credit.trim(), source: form.source.trim() })
      .then(async (result) => {
        if (!result) return; // picker cancelled
        await refresh();
        setForm(null);
        const skipped = result.skipped.map((item) => `Skipped ${item.file}: ${item.reason}`);
        onNotice([`Installed "${result.pack.name}" with ${result.pack.sound_count} sound${result.pack.sound_count === 1 ? '' : 's'}. It is active.`, ...skipped].join('\n'));
      })
      .catch((error) => onNotice(`Pack not installed: ${errorText(error)}`))
      .finally(() => setInstalling(false));
  };

  const toggle = (pack: SoundPackSummary) => {
    setPending(pack.id);
    void engine.setSoundPackActive(pack.id, !pack.active)
      .then(() => refresh())
      .catch((error) => onNotice(errorText(error)))
      .finally(() => setPending(null));
  };

  const remove = (pack: SoundPackSummary) => {
    if (confirmRemove !== pack.id) { setConfirmRemove(pack.id); return; }
    setConfirmRemove(null);
    setPending(pack.id);
    void engine.removeSoundPack(pack.id)
      .then(async () => { await refresh(); onNotice(`Removed "${pack.name}". Sounds already used in a project stay in that project.`); })
      .catch((error) => onNotice(errorText(error)))
      .finally(() => setPending(null));
  };

  const activeCount = packs?.filter((pack) => pack.active).length ?? 0;

  return (
    <section data-testid="sound-library" className="rounded-xl border border-line bg-surface p-4 space-y-3">
      <div className="flex items-center justify-between gap-2">
        <h3 className="flex items-center gap-2 font-semibold"><Music2 className="w-4 h-4" /> Sound effects <span className="text-xs font-normal text-fg-3">{packs ? `${activeCount} of ${packs.length} active` : ''}</span></h3>
        {!form && <button className={button} disabled={disabled} onClick={() => setForm(EMPTY)}><FolderInput className="w-4 h-4" /> Install pack…</button>}
      </div>
      <p className="text-xs text-fg-3">Claude only uses sounds from packs ticked <strong className="text-fg-2">Active</strong>, and only when you ask for sound effects.</p>

      {form && (
        <div className="space-y-2 rounded-lg border border-line bg-app/50 p-3">
          <p className="text-xs text-fg-2">Download a free pack yourself and unzip it, then pick its folder. AgenEditor copies the sounds and never downloads anything. Free sources: Kenney.nl (CC0), Pixabay and Mixkit (royalty-free), Freesound (check each sound).</p>
          <label className="block text-xs text-fg-2">Pack name
            <input className={`${field} mt-1`} maxLength={80} placeholder="e.g. Whooshes and pops" value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} />
          </label>
          <label className="block text-xs text-fg-2">Licence
            <select className={`${field} mt-1`} value={form.license} onChange={(e) => setForm({ ...form, license: e.target.value as SoundLicense })}>
              {(Object.keys(LICENSE_LABELS) as SoundLicense[]).map((key) => <option key={key} value={key}>{LICENSE_LABELS[key]}</option>)}
            </select>
          </label>
          {(form.license === 'cc_by' || form.license === 'other') && (
            <label className="block text-xs text-fg-2">Credit line to publish with your video{form.license === 'cc_by' ? '' : ' (if the terms ask for one)'}
              <input className={`${field} mt-1`} maxLength={300} placeholder="e.g. Sounds by alice on Freesound (CC BY 4.0)" value={form.credit} onChange={(e) => setForm({ ...form, credit: e.target.value })} />
            </label>
          )}
          <label className="block text-xs text-fg-2">Where it's from (optional)
            <input className={`${field} mt-1`} maxLength={500} placeholder="e.g. kenney.nl/assets/ui-audio" value={form.source} onChange={(e) => setForm({ ...form, source: e.target.value })} />
          </label>
          {formProblem && form.name && <p className="text-xs text-warning">{formProblem}</p>}
          <div className="flex gap-2">
            <button className={primary} disabled={installing || Boolean(formProblem)} onClick={install}>
              {installing ? <><Loader2 className="w-4 h-4 animate-spin" /> Installing…</> : 'Choose folder & install'}
            </button>
            <button className={button} disabled={installing} onClick={() => setForm(null)}>Cancel</button>
          </div>
          <p className="text-xs text-fg-3">The licence is what you tell AgenEditor; it doesn't check it. Only clips up to 60 seconds are installed.</p>
        </div>
      )}

      {packs && packs.length === 0 && !form && <p className="text-sm text-fg-3">No packs installed yet.</p>}
      {packs && packs.length > 0 && (
        <ul className="space-y-1.5" aria-label="Installed sound packs">
          {packs.map((pack) => (
            <li key={pack.id} className="flex flex-wrap items-center gap-x-3 gap-y-1 rounded-lg border border-line px-3 py-2 text-sm">
              <label className="flex items-center gap-2 font-medium">
                <input type="checkbox" checked={pack.active} disabled={disabled || pending === pack.id} onChange={() => toggle(pack)} aria-label={`Active: ${pack.name}`} />
                {pack.name}
              </label>
              <span className="text-xs text-fg-3">{pack.sound_count} sounds · {SHORT_LICENSE[pack.license]}{pack.active ? '' : ' · not active'}</span>
              <button className="ml-auto text-xs text-fg-3 hover:text-danger disabled:opacity-40" disabled={disabled || pending === pack.id} onClick={() => remove(pack)} onBlur={() => setConfirmRemove(null)}>
                {confirmRemove === pack.id ? 'Click again to remove' : 'Remove'}
              </button>
              {pack.credit && <p className="basis-full text-xs text-fg-2">Credit: {pack.credit}</p>}
            </li>
          ))}
        </ul>
      )}
    </section>
  );
};
