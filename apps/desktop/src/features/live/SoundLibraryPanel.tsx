import React, { useCallback, useEffect, useState } from 'react';
import { Check, Download, FolderInput, Loader2, Music2 } from 'lucide-react';
import {
  liveEngineRepository as engine,
  type SoundCatalogEntry,
  type SoundLicense,
  type SoundPackDetails,
  type SoundPackInstallResult,
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

/** Engine errors reach the page as JSON text (`{"code":…,"message":…}`); show just the message. */
const errorText = (error: unknown) => {
  const text = error instanceof Error ? error.message : String(error);
  try {
    const parsed: unknown = JSON.parse(text);
    if (parsed && typeof parsed === 'object' && typeof (parsed as { message?: unknown }).message === 'string') {
      return (parsed as { message: string }).message;
    }
  } catch { /* plain text */ }
  return text;
};
const megabytes = (bytes: number) => `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
const installedNotice = (result: SoundPackInstallResult) => [
  `Installed "${result.pack.name}" with ${result.pack.sound_count} sound${result.pack.sound_count === 1 ? '' : 's'}. It is active.`,
  ...result.skipped.map((item) => `Skipped ${item.file}: ${item.reason}`),
].join('\n');
const EMPTY: SoundPackDetails = { name: '', license: 'cc0', credit: '', source: '' };

/**
 * Sound-effect packs. A free CC0 starter pack comes with the app; more CC0 packs from a fixed
 * catalog install with one click (the engine downloads from a pinned URL and checks a pinned
 * SHA-256), and the person can install their own pack from a folder. Claude can only use packs
 * ticked Active and can never download anything itself.
 */
export const SoundLibraryPanel: React.FC<{ disabled?: boolean; onNotice: (text: string) => void }> = ({ disabled, onNotice }) => {
  const [packs, setPacks] = useState<SoundPackSummary[] | null>(null);
  const [catalog, setCatalog] = useState<SoundCatalogEntry[]>([]);
  const [form, setForm] = useState<SoundPackDetails | null>(null);
  const [installing, setInstalling] = useState(false);
  const [downloading, setDownloading] = useState<string | null>(null);
  const [pending, setPending] = useState<string | null>(null);
  const [confirmRemove, setConfirmRemove] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    // Listing packs installs the bundled starter pack on first use, so read the catalog after it.
    setPacks(await engine.listSoundPacks());
    setCatalog(await engine.listSoundCatalog());
  }, []);
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
        onNotice(installedNotice(result));
      })
      .catch((error) => onNotice(`Pack not installed: ${errorText(error)}`))
      .finally(() => setInstalling(false));
  };

  const download = (entry: SoundCatalogEntry) => {
    setDownloading(entry.id);
    void engine.downloadSoundPack(entry.id)
      .then(async (result) => { await refresh(); onNotice(installedNotice(result)); })
      .catch((error) => onNotice(`${entry.name} not installed: ${errorText(error)}`))
      .finally(() => setDownloading(null));
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
        {!form && <button className={button} disabled={disabled} onClick={() => setForm(EMPTY)}><FolderInput className="w-4 h-4" /> Install from folder…</button>}
      </div>
      <p className="text-xs text-fg-3">Claude only uses sounds from packs ticked <strong className="text-fg-2">Active</strong>, and only when you ask for sound effects.</p>

      {form && (
        <div className="space-y-2 rounded-lg border border-line bg-app/50 p-3">
          <p className="text-xs text-fg-2">Have your own pack? Unzip it, then pick its folder. AgenEditor copies the sounds. Good sources: Pixabay and Mixkit (royalty-free), Freesound (check each sound).</p>
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
            <input className={`${field} mt-1`} maxLength={500} placeholder="e.g. pixabay.com/sound-effects" value={form.source} onChange={(e) => setForm({ ...form, source: e.target.value })} />
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

      {packs && packs.length === 0 && !form && <p className="text-sm text-fg-3">No packs installed yet. Install a free pack below.</p>}
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

      {catalog.length > 0 && (
        <div className="space-y-1.5">
          <h4 className="text-xs font-semibold uppercase tracking-wide text-fg-3">Free packs · CC0 by Kenney</h4>
          <ul className="space-y-1.5" aria-label="Free sound packs">
            {catalog.map((entry) => (
              <li key={entry.id} className="flex flex-wrap items-center gap-x-3 gap-y-1 rounded-lg border border-line px-3 py-2 text-sm">
                <div className="min-w-0 flex-1">
                  <p className="font-medium">{entry.name}</p>
                  <p className="text-xs text-fg-3">{entry.description} {entry.sound_count} sounds · {entry.bundled_file ? 'included with AgenEditor' : megabytes(entry.size_bytes)}</p>
                </div>
                {entry.installed_pack_id
                  ? <span className="inline-flex items-center gap-1 text-xs text-fg-3"><Check className="w-4 h-4" /> Installed</span>
                  : (
                    <button className={button} disabled={disabled || downloading !== null} onClick={() => download(entry)} aria-label={`${entry.bundled_file ? 'Install' : 'Download and install'} ${entry.name}`}>
                      {downloading === entry.id
                        ? <><Loader2 className="w-4 h-4 animate-spin" /> Installing…</>
                        : <><Download className="w-4 h-4" /> {entry.bundled_file ? 'Install' : 'Download & install'}</>}
                    </button>
                  )}
              </li>
            ))}
          </ul>
          <p className="text-xs text-fg-3">Downloads come from OpenGameArt.org and must match a fixed fingerprint before they install. No credit needed.</p>
        </div>
      )}
    </section>
  );
};
