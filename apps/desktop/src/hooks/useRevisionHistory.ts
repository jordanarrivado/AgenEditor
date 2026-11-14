/**
 * Undo/redo for demo timeline edits. Holds the working copy of the open
 * revision; each commit/undo/redo persists through the repository.
 */
import { useCallback, useEffect, useRef, useState } from 'react';
import type { DemoRevision } from '../types';

interface Entry {
  revision: DemoRevision;
  label: string;
}

interface HistoryState {
  current: DemoRevision | null;
  past: Entry[];
  future: Entry[];
  lastLabel: string | null;
}

const LIMIT = 50;

export function useRevisionHistory(source: DemoRevision | null, persist: (rev: DemoRevision) => Promise<void> | void) {
  const [h, setH] = useState<HistoryState>({ current: source, past: [], future: [], lastLabel: null });
  const stateRef = useRef(h);
  stateRef.current = h;
  const persistRef = useRef(persist);
  persistRef.current = persist;

  // A different revision (or a fresh copy of it after Reset Demo Data) replaces history.
  useEffect(() => {
    const cur = stateRef.current;
    if (source?.id !== cur.current?.id || (cur.past.length === 0 && cur.future.length === 0)) {
      setH({ current: source, past: [], future: [], lastLabel: null });
    }
  }, [source]);

  const commit = useCallback((next: DemoRevision, label: string) => {
    const cur = stateRef.current;
    const nextState: HistoryState = {
      current: next,
      past: cur.current ? [...cur.past.slice(-(LIMIT - 1)), { revision: cur.current, label }] : cur.past,
      future: [],
      lastLabel: label,
    };
    stateRef.current = nextState;
    setH(nextState);
    void persistRef.current(next);
  }, []);

  const undo = useCallback(() => {
    const cur = stateRef.current;
    const entry = cur.past[cur.past.length - 1];
    if (!entry || !cur.current) return;
    const nextState: HistoryState = {
      current: entry.revision,
      past: cur.past.slice(0, -1),
      future: [...cur.future, { revision: cur.current, label: entry.label }],
      lastLabel: `Undid: ${entry.label}`,
    };
    stateRef.current = nextState;
    setH(nextState);
    void persistRef.current(entry.revision);
  }, []);

  const redo = useCallback(() => {
    const cur = stateRef.current;
    const entry = cur.future[cur.future.length - 1];
    if (!entry || !cur.current) return;
    const nextState: HistoryState = {
      current: entry.revision,
      past: [...cur.past, { revision: cur.current, label: entry.label }],
      future: cur.future.slice(0, -1),
      lastLabel: `Redid: ${entry.label}`,
    };
    stateRef.current = nextState;
    setH(nextState);
    void persistRef.current(entry.revision);
  }, []);

  return {
    revision: h.current,
    commit,
    undo,
    redo,
    canUndo: h.past.length > 0,
    canRedo: h.future.length > 0,
    undoLabel: h.past[h.past.length - 1]?.label ?? null,
    redoLabel: h.future[h.future.length - 1]?.label ?? null,
    lastLabel: h.lastLabel,
  };
}
