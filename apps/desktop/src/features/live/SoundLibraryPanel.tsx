import React, { useCallback, useEffect, useRef, useState } from 'react';
import { Download, FolderPlus, Loader2, Music2, ShieldCheck } from 'lucide-react';
import {
  liveEngineRepository as engine,
  type SoundCatalogEntry,
  type SoundLicense,
  type SoundPackDetails,
  type SoundPackInstallResult,
  type SoundPackSummary,
} from '../../services/liveEngineRepository';
import { Modal } from '../../components/ui/Modal';
import { Button } from '../../components/ui/primitives';
import { SectionLabel } from './SectionLabel';

const field = 'w-full rounded-lg border border-line bg-app px-3 py-2 text-sm text-fg outline-none focus:border-accent';

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
const plural = (count: number, word: string) => `${count} ${word}${count === 1 ? '' : 's'}`;
const EMPTY: SoundPackDetails = { name: '', license: 'cc0', credit: '', source: '' };

/**
 * Sound-effect packs. A free CC0 starter pack comes with the app; more CC0 packs from a fixed
 * catalog install with one click (the engine downloads from a pinned URL and checks a pinned
 * SHA-256), and the person can install their own pack from a folder. Claude can only use packs
 * that are on, and can never download anything itself.
 */
export const SoundLibraryPanel: React.FC<{ disabled?: boolean; onNotice: (text: string) => void; onActiveCount?: (count: number) => void }> = ({ disabled, onNotice, onActiveCount }) => {
  const [packs, setPacks] = useState<SoundPackSummary[] | null>(null);
  const [catalog, setCatalog] = useState<SoundCatalogEntry[]>([]);
  const [open, setOpen] = useState(false);
  const [form, setForm] = useState<SoundPackDetails | null>(null);
  const [installing, setInstalling] = useState(false);
  const [downloading, setDownloading] = useState<string | null>(null);
  const [pending, setPending] = useState<string | null>(null);
  const [confirmRemove, setConfirmRemove] = useState<string | null>(null);
  // Shown inside the dialog, which sits above the page's own notice line.
  const [message, setMessage] = useState<string | null>(null);

  const report = useCallback((text: string) => { setMessage(text); onNotice(text); }, [onNotice]);
  // Open at the top of the list; the dialog body otherwise keeps a stale scroll offset.
  const bodyTop = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!open) return;
    const frame = requestAnimationFrame(() => { const scroller = bodyTop.current?.parentElement; if (scroller) scroller.scrollTop = 0; });
    return () => cancelAnimationFrame(frame);
  }, [open]);

  const refresh = useCallback(async () => {
    // Listing packs installs the bundled starter pack on first use, so read the catalog after it.
    setPacks(await engine.listSoundPacks());
    setCatalog(await engine.listSoundCatalog());
  }, []);
  useEffect(() => { void refresh().catch((error) => onNotice(`Sound library unavailable: ${errorText(error)}`)); }, [refresh, onNotice]);

  const entryFor = (pack: SoundPackSummary) => catalog.find((entry) => entry.id === pack.catalog_id);
  const displayName = (pack: SoundPackSummary) => entryFor(pack)?.name ?? pack.name;
  const installedNotice = (result: SoundPackInstallResult) => [
    `Installed "${catalog.find((entry) => entry.id === result.pack.catalog_id)?.name ?? result.pack.name}" with ${plural(result.pack.sound_count, 'sound')}. It is on.`,
    ...result.skipped.map((item) => `Skipped ${item.file}: ${item.reason}`),
  ].join('\n');

  const formProblem = !form ? null
    : !form.name.trim() ? 'Name the pack.'
      : form.license === 'cc_by' && !form.credit.trim() ? 'CC BY sounds need the credit line you must publish.'
        : null;

  const install = () => {
    if (!form || formProblem) return;
    setInstalling(true);
    setMessage(null);
    void engine.installSoundPack({ ...form, name: form.name.trim(), credit: form.credit.trim(), source: form.source.trim() })
      .then(async (result) => {
        if (!result) return; // picker cancelled
        await refresh();
        setForm(null);
        report(installedNotice(result));
      })
      .catch((error) => report(`Pack not installed: ${errorText(error)}`))
      .finally(() => setInstalling(false));
  };

  const download = (entry: SoundCatalogEntry) => {
    setDownloading(entry.id);
    setMessage(null);
    void engine.downloadSoundPack(entry.id)
      .then(async (result) => { await refresh(); report(installedNotice(result)); })
      .catch((error) => report(`${entry.name} not installed: ${errorText(error)}`))
      .finally(() => setDownloading(null));
  };

  const toggle = (pack: SoundPackSummary) => {
    setPending(pack.id);
    void engine.setSoundPackActive(pack.id, !pack.active)
      .then(() => refresh())
      .catch((error) => report(errorText(error)))
      .finally(() => setPending(null));
  };

  const remove = (pack: SoundPackSummary) => {
    if (confirmRemove !== pack.id) { setConfirmRemove(pack.id); return; }
    setConfirmRemove(null);
    setPending(pack.id);
    void engine.removeSoundPack(pack.id)
      .then(async () => { await refresh(); report(`Removed "${displayName(pack)}". Sounds already used in a project stay in that project.`); })
      .catch((error) => report(errorText(error)))
      .finally(() => setPending(null));
  };

  const activeCount = packs?.filter((pack) => pack.active).length ?? 0;
  useEffect(() => { if (packs) onActiveCount?.(activeCount); }, [packs, activeCount, onActiveCount]);
  const available = catalog.filter((entry) => !entry.installed_pack_id);

  return (
    <section data-testid="sound-library" aria-labelledby="sound-library-title" className="space-y-2.5">
      <SectionLabel id="sound-library-title" title="Sound effects" meta={packs ? `${activeCount} of ${plural(packs.length, 'pack')} on` : undefined} />
      <div className="rounded-xl border border-line bg-surface p-3 space-y-2.5">
        {packs && packs.length === 0 && <p className="text-xs text-fg-3">No packs yet. Free packs install with one click.</p>}
        {packs && packs.length > 0 && (
          <ul className="space-y-1" aria-label="Sound packs summary">
            {packs.map((pack) => (
              <li key={pack.id} className="flex items-center gap-2 text-sm">
                <Music2 className="h-3.5 w-3.5 shrink-0 text-fg-3" aria-hidden />
                <span className="min-w-0 flex-1 truncate">{displayName(pack)}</span>
                <span className={`text-xs ${pack.active ? 'text-success' : 'text-fg-3'}`}>{pack.active ? 'On' : 'Off'}</span>
              </li>
            ))}
          </ul>
        )}
        <Button className="w-full" icon={<Music2 className="h-4 w-4" />} disabled={disabled || !packs} onClick={() => { setMessage(null); setOpen(true); }}>
          Manage sound packs{available.length ? ` · ${available.length} free` : ''}
        </Button>
      </div>

      <Modal
        open={open}
        onOpenChange={(next) => { setOpen(next); if (!next) { setForm(null); setConfirmRemove(null); } }}
        title="Sound effect packs"
        description="Claude only uses sounds from packs that are on, and only when you ask for sound effects."
        footer={(
          <>
            <Button variant="ghost" className="mr-auto" icon={<FolderPlus className="h-4 w-4" />} disabled={disabled || installing || form !== null} onClick={() => { setMessage(null); setForm(EMPTY); }}>Install from folder</Button>
            <Button variant="primary" size="lg" onClick={() => setOpen(false)}>Done</Button>
          </>
        )}
      >
        <div ref={bodyTop} className="space-y-6">
          {message && <p role="status" className="whitespace-pre-line rounded-lg border border-accent/40 bg-accent/10 px-3 py-2 text-sm">{message}</p>}

          {form && (
            <div className="space-y-2.5 rounded-xl border border-line bg-app/50 p-4">
              <p className="text-sm font-semibold">Install your own pack</p>
              <p className="text-xs text-fg-2">Unzip it first, then pick its folder. AgenEditor copies the sounds. Good sources: Pixabay and Mixkit (royalty-free), Freesound (check each sound).</p>
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
                <Button variant="primary" disabled={installing || Boolean(formProblem)} onClick={install} icon={installing ? <Loader2 className="h-4 w-4 animate-spin" /> : undefined}>
                  {installing ? 'Installing…' : 'Choose folder & install'}
                </Button>
                <Button disabled={installing} onClick={() => setForm(null)}>Cancel</Button>
              </div>
              <p className="text-xs text-fg-3">The licence is what you tell AgenEditor; it doesn't check it. Only clips up to 60 seconds are installed.</p>
            </div>
          )}

          <section aria-labelledby="installed-packs-title" className="space-y-2.5">
            <SectionLabel id="installed-packs-title" title={`Installed · ${packs?.length ?? 0}`} />
            {packs && packs.length === 0 && <p className="text-sm text-fg-3">Nothing installed yet. Pick a free pack below.</p>}
            {packs && packs.length > 0 && (
              <ul className="space-y-2" aria-label="Installed sound packs">
                {packs.map((pack) => {
                  const entry = entryFor(pack);
                  const name = displayName(pack);
                  const busy = disabled || pending === pack.id;
                  const meta = [entry?.author, plural(pack.sound_count, 'sound'), SHORT_LICENSE[pack.license], entry?.bundled_file ? 'included with AgenEditor' : null].filter(Boolean).join(' · ');
                  return (
                    <li key={pack.id} className="flex items-start gap-3 rounded-xl border border-line bg-app/40 px-4 py-3">
                      <div className="min-w-0 flex-1">
                        <p className="font-semibold">{name}</p>
                        {entry && <p className="text-sm text-fg-2">{entry.description}</p>}
                        <p className="text-xs text-fg-3">{meta}</p>
                        {pack.credit && <p className="mt-0.5 text-xs text-fg-2">Credit: {pack.credit}</p>}
                        <button type="button" className="mt-1 text-xs text-fg-3 hover:text-danger disabled:opacity-40" disabled={busy} onClick={() => remove(pack)} onBlur={() => setConfirmRemove(null)}>
                          {confirmRemove === pack.id ? 'Click again to remove' : 'Remove'}
                        </button>
                      </div>
                      <button type="button" role="switch" aria-checked={pack.active} aria-label={`On: ${name}`} disabled={busy} onClick={() => toggle(pack)}
                        className={`shrink-0 rounded-full border px-3 py-1 text-sm font-semibold transition-colors disabled:opacity-50 ${pack.active ? 'border-success/40 bg-success/15 text-success' : 'border-line bg-app text-fg-3 hover:text-fg'}`}>
                        {pending === pack.id ? <Loader2 className="h-4 w-4 animate-spin" /> : pack.active ? 'On' : 'Off'}
                      </button>
                    </li>
                  );
                })}
              </ul>
            )}
          </section>

          {catalog.length > 0 && (
            <section aria-labelledby="free-packs-title" className="space-y-2.5">
              <SectionLabel id="free-packs-title" title="Free packs · CC0 by Kenney" meta="No credit needed" />
              {available.length === 0
                ? <p className="text-sm text-fg-3">All free packs are installed.</p>
                : (
                  <ul className="divide-y divide-line overflow-hidden rounded-xl border border-line" aria-label="Free sound packs">
                    {available.map((entry) => (
                      <li key={entry.id} className="flex items-center gap-3 px-4 py-3">
                        <span aria-hidden className="flex h-10 w-10 shrink-0 items-center justify-center rounded-lg bg-elevated text-fg-2"><Music2 className="h-5 w-5" /></span>
                        <div className="min-w-0 flex-1">
                          <p className="font-semibold">{entry.name}</p>
                          <p className="text-sm text-fg-2">{entry.description}</p>
                          <p className="text-xs text-fg-3">{plural(entry.sound_count, 'sound')} · {entry.bundled_file ? 'included with AgenEditor' : megabytes(entry.size_bytes)}</p>
                        </div>
                        <Button size="lg" className="shrink-0" disabled={disabled || downloading !== null} onClick={() => download(entry)} aria-label={`Install ${entry.name}`}
                          icon={downloading === entry.id ? <Loader2 className="h-4 w-4 animate-spin" /> : <Download className="h-4 w-4" />}>
                          {downloading === entry.id ? 'Installing…' : 'Install'}
                        </Button>
                      </li>
                    ))}
                  </ul>
                )}
              <p className="flex items-start gap-1.5 text-xs text-fg-3"><ShieldCheck className="mt-px h-3.5 w-3.5 shrink-0" aria-hidden /> Downloaded from OpenGameArt.org. Each pack must match a fixed fingerprint before it installs.</p>
            </section>
          )}
        </div>
      </Modal>
    </section>
  );
};
