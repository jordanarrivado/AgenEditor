import React, { useCallback, useEffect, useState } from 'react';
import { Blend } from 'lucide-react';
import {
  liveEngineRepository as engine,
  type TransitionStyleSummary,
} from '../../services/liveEngineRepository';

const errorText = (error: unknown) => error instanceof Error ? error.message : String(error);

/**
 * Built-in transition styles. They come with the renderer (FFmpeg), so there is nothing to
 * install; ticking a style lets Claude use its transitions when you ask for them.
 */
export const TransitionsPanel: React.FC<{ disabled?: boolean; onNotice: (text: string) => void }> = ({ disabled, onNotice }) => {
  const [styles, setStyles] = useState<TransitionStyleSummary[] | null>(null);
  const [pending, setPending] = useState<string | null>(null);

  const refresh = useCallback(async () => setStyles(await engine.listTransitionStyles()), []);
  useEffect(() => { void refresh().catch((error) => onNotice(`Transitions unavailable: ${errorText(error)}`)); }, [refresh, onNotice]);

  const toggle = (style: TransitionStyleSummary) => {
    setPending(style.id);
    void engine.setTransitionStyleActive(style.id, !style.active)
      .then(setStyles)
      .catch((error) => onNotice(errorText(error)))
      .finally(() => setPending(null));
  };

  const total = styles?.reduce((sum, style) => sum + style.count, 0) ?? 0;
  const active = styles?.filter((style) => style.active).length ?? 0;

  return (
    <section data-testid="transitions" className="rounded-xl border border-line bg-surface p-4 space-y-3">
      <div className="flex items-center justify-between gap-2">
        <h3 className="flex items-center gap-2 font-semibold"><Blend className="w-4 h-4" /> Transitions <span className="text-xs font-normal text-fg-3">{styles ? `${active} of ${styles.length} styles active` : ''}</span></h3>
        <span className="text-xs text-fg-3">{total ? `${total} built in · free` : ''}</span>
      </div>
      <p className="text-xs text-fg-3">Built into AgenEditor's renderer, so there's nothing to install. Claude only uses styles ticked <strong className="text-fg-2">Active</strong>, and only when you ask for transitions. Cuts stay hard cuts otherwise.</p>
      {styles && (
        <ul className="grid grid-cols-1 sm:grid-cols-2 gap-2" aria-label="Transition styles">
          {styles.map((style) => (
            <li key={style.id} className={`rounded-lg border px-3 py-2 text-sm ${style.active ? 'border-accent/50 bg-accent/5' : 'border-line'}`}>
              <label className="flex items-center gap-2 font-medium">
                <input type="checkbox" checked={style.active} disabled={disabled || pending === style.id} onChange={() => toggle(style)} aria-label={`Active: ${style.label}`} />
                {style.label} <span className="text-xs font-normal text-fg-3">{style.count}</span>
              </label>
              <p className="mt-1 text-xs text-fg-2">{style.description}</p>
              <p className="mt-0.5 text-xs text-fg-3">e.g. {style.examples.join(', ')}</p>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
};
