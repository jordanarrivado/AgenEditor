import React, { useCallback, useEffect, useState } from 'react';
import { Check } from 'lucide-react';
import {
  liveEngineRepository as engine,
  type TransitionStyleSummary,
} from '../../services/liveEngineRepository';
import { SectionLabel } from './SectionLabel';

const errorText = (error: unknown) => error instanceof Error ? error.message : String(error);

/**
 * Built-in transition styles. They come with the renderer (FFmpeg), so there is nothing to
 * install; ticking a style lets Claude use its transitions when you ask for them.
 */
export const TransitionsPanel: React.FC<{ disabled?: boolean; onNotice: (text: string) => void; onActiveCount?: (count: number) => void }> = ({ disabled, onNotice, onActiveCount }) => {
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

  const active = styles?.filter((style) => style.active).length ?? 0;
  useEffect(() => { if (styles) onActiveCount?.(active); }, [styles, active, onActiveCount]);

  return (
    <section data-testid="transitions" aria-labelledby="transitions-title" className="space-y-2.5">
      <SectionLabel id="transitions-title" title="Transitions" meta={styles ? `${active} of ${styles.length} styles on · free, built in` : undefined} />
      {styles && (
        <ul className="grid grid-cols-2 gap-2" aria-label="Transition styles">
          {styles.map((style) => {
            const busy = disabled || pending === style.id;
            return (
              <li key={style.id}>
                <label className={`relative block h-full cursor-pointer rounded-xl border p-3 transition-colors has-[:focus-visible]:outline has-[:focus-visible]:outline-2 has-[:focus-visible]:outline-accent ${style.active ? 'border-accent/60 bg-accent/8' : 'border-line bg-surface hover:bg-elevated'} ${busy ? 'cursor-not-allowed opacity-60' : ''}`}>
                  <input type="checkbox" className="peer sr-only" checked={style.active} disabled={busy} onChange={() => toggle(style)} aria-label={`Active: ${style.label}`} />
                  <span aria-hidden className={`absolute right-2.5 top-2.5 flex h-5 w-5 items-center justify-center rounded-md border ${style.active ? 'border-accent bg-accent text-on-accent' : 'border-line bg-app'}`}>
                    {style.active && <Check className="h-3.5 w-3.5" strokeWidth={3} />}
                  </span>
                  <span className="block pr-7 text-sm font-semibold text-fg">{style.label} <span className="font-normal text-fg-3">{style.count}</span></span>
                  <span className="mt-1 block text-xs leading-snug text-fg-2">{style.description}</span>
                  <span className="mt-1 block text-[11px] leading-snug text-fg-3">e.g. {style.examples.join(', ')}</span>
                </label>
              </li>
            );
          })}
        </ul>
      )}
      <p className="text-xs text-fg-3">Claude uses these only when you ask for transitions; otherwise cuts stay hard cuts.</p>
    </section>
  );
};
