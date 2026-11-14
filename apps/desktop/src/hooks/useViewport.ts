import { useEffect, useState } from 'react';

/** Current window width, updated on resize. */
export function useViewportWidth(): number {
  const [w, setW] = useState(() => (typeof window === 'undefined' ? 1440 : window.innerWidth));
  useEffect(() => {
    const on = () => setW(window.innerWidth);
    window.addEventListener('resize', on);
    return () => window.removeEventListener('resize', on);
  }, []);
  return w;
}

/** Spec §6 breakpoints. */
export type EditorLayout = 'wide' | 'default' | 'compact' | 'narrow';
export function editorLayoutFor(width: number): EditorLayout {
  if (width >= 1600) return 'wide';
  if (width >= 1280) return 'default';
  if (width >= 960) return 'compact';
  return 'narrow';
}

/** True when keyboard focus is in a text-entry control (shortcuts must not fire). */
export function isTypingTarget(t: EventTarget | null): boolean {
  if (!(t instanceof HTMLElement)) return false;
  if (t.isContentEditable) return true;
  const tag = t.tagName;
  if (tag === 'TEXTAREA' || tag === 'SELECT') return true;
  if (tag === 'INPUT') {
    const type = (t as HTMLInputElement).type;
    return !['range', 'checkbox', 'radio', 'button', 'color'].includes(type);
  }
  return false;
}
