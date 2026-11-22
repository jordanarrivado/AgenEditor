import React, { useCallback, useEffect, useState } from 'react';
import { Loader2, Save, Trash2 } from 'lucide-react';
import {
  liveEngineRepository as engine,
  type EngineEditorPresetLibrary,
} from '../../services/liveEngineRepository';
import { engineErrorText } from '../../utils/engineError';
import { Button } from '../../components/ui/primitives';
import { SectionLabel } from './SectionLabel';

const field = 'w-full rounded-lg border border-line bg-app px-2.5 py-1.5 text-sm text-fg outline-none placeholder:text-fg-3 focus:border-accent';

const describe = (preset: EngineEditorPresetLibrary['presets'][number]) => [
  `captions ${preset.caption_preset.replace(/_/g, ' ')}`,
  preset.color_preset === 'none' ? 'no grade' : `${preset.color_preset} grade`,
  preset.broll_color_match ? 'B-roll matched' : '',
  preset.audio.speech_repair !== 'none' ? `${preset.audio.speech_repair} speech repair` : '',
].filter(Boolean).join(' · ');

export interface EditorPresetsPanelProps {
  projectId: string;
  /** The project's current revision (a preset is saved from it). */
  revision: number;
  disabled?: boolean;
  onNotice: (text: string) => void;
}

/**
 * Editor presets (Phase 6): finishing settings shared across projects. The person saves and picks them;
 * Claude applies the picked one with apply_editor_preset when it edits (the app makes no edits itself).
 */
export const EditorPresetsPanel: React.FC<EditorPresetsPanelProps> = ({ projectId, revision, disabled, onNotice }) => {
  const [library, setLibrary] = useState<EngineEditorPresetLibrary | null>(null);
  const [chosen, setChosen] = useState<string | null>(null);
  const [name, setName] = useState('');
  const [pending, setPending] = useState<string | null>(null);
  const [confirmDelete, setConfirmDelete] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    const [presets, settings] = await Promise.all([engine.listEditorPresets(), engine.projectSettings(projectId)]);
    setLibrary(presets);
    setChosen(settings.editor_preset_id);
  }, [projectId]);
  useEffect(() => { void refresh().catch((error) => onNotice(`Editor presets unavailable: ${engineErrorText(error)}`)); }, [refresh, onNotice]);

  const run = (key: string, task: () => Promise<void>) => {
    setPending(key);
    void task().catch((error) => onNotice(engineErrorText(error))).finally(() => setPending(null));
  };

  const save = () => run('save', async () => {
    if (!library || !name.trim()) return;
    setLibrary(await engine.saveEditorPreset(projectId, name.trim(), library.version));
    onNotice(`Saved this project's caption, colour and audio settings as "${name.trim()}".`);
    setName('');
  });

  const choose = (presetId: string | null) => run('choose', async () => {
    await engine.chooseEditorPreset(projectId, presetId);
    setChosen(presetId);
  });

  const remove = (presetId: string) => {
    if (confirmDelete !== presetId) { setConfirmDelete(presetId); return; }
    setConfirmDelete(null);
    run(presetId, async () => {
      if (!library) return;
      setLibrary(await engine.deleteEditorPreset(presetId, library.version));
      if (chosen === presetId) await choose(null);
    });
  };

  const busy = disabled || pending !== null;
  return (
    <section data-testid="editor-presets" aria-labelledby="presets-title" className="space-y-2.5">
      <SectionLabel id="presets-title" title="Editor presets" meta={library ? `${library.presets.length} saved · captions, colour, audio` : undefined} />
      {library && library.presets.length > 0 && (
        <fieldset className="space-y-1.5" disabled={busy}>
          <legend className="sr-only">Preset for this project</legend>
          <label className="flex items-center gap-2 rounded-lg border border-line bg-app px-2.5 py-2 text-sm">
            <input type="radio" name="editor-preset" checked={chosen === null} onChange={() => choose(null)} />
            <span>No preset for this project</span>
          </label>
          {library.presets.map((preset) => (
            <div key={preset.id} className={`flex items-start gap-2 rounded-lg border bg-app px-2.5 py-2 text-sm ${chosen === preset.id ? 'border-accent/60' : 'border-line'}`}>
              <label className="flex min-w-0 flex-1 items-start gap-2">
                <input type="radio" name="editor-preset" className="mt-1" checked={chosen === preset.id} onChange={() => choose(preset.id)} />
                <span className="min-w-0">
                  <span className="block truncate font-medium">{preset.name}</span>
                  <span className="block text-xs text-fg-3">{describe(preset)}</span>
                </span>
              </label>
              <Button size="sm" variant={confirmDelete === preset.id ? 'danger' : 'ghost'} aria-label={confirmDelete === preset.id ? `Confirm deleting ${preset.name}` : `Delete ${preset.name}`}
                onClick={() => remove(preset.id)} onBlur={() => setConfirmDelete(null)}
                icon={pending === preset.id ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Trash2 className="h-3.5 w-3.5" />}>
                {confirmDelete === preset.id ? 'Delete?' : null}
              </Button>
            </div>
          ))}
          {chosen && <p className="text-xs text-fg-3">Claude applies this preset the next time it edits this project.</p>}
        </fieldset>
      )}
      <form className="flex items-center gap-2" onSubmit={(e) => { e.preventDefault(); save(); }}>
        <input aria-label="New preset name" className={field} placeholder="Save current settings as…" maxLength={60} value={name}
          disabled={busy || revision === 0} onChange={(e) => setName(e.target.value)} />
        <Button type="submit" size="sm" disabled={busy || revision === 0 || !name.trim()}
          icon={pending === 'save' ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Save className="h-3.5 w-3.5" />}>Save</Button>
      </form>
      {revision === 0 && <p className="text-xs text-fg-3">Presets are saved from an edit; there is none in this project yet.</p>}
    </section>
  );
};
