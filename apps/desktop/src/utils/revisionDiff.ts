import type { DemoRevision, DemoTimelineClip } from '../types';

export interface RevisionDiff {
  added: { track: string; clip: DemoTimelineClip }[];
  removed: { track: string; clip: DemoTimelineClip }[];
  changed: { track: string; before: DemoTimelineClip; after: DemoTimelineClip; what: string[] }[];
  durationDeltaFrames: number;
}

/** Compare two revisions by clip label+track (ids differ between revisions). */
export function diffRevisions(base: DemoRevision, next: DemoRevision): RevisionDiff {
  const key = (track: string, c: DemoTimelineClip) => `${track}::${c.label ?? c.assetId}::${c.assetId}`;
  const index = (rev: DemoRevision) => {
    const m = new Map<string, { track: string; clip: DemoTimelineClip }>();
    for (const t of rev.tracks) {
      const counts = new Map<string, number>();
      for (const c of t.clips) {
        const k0 = key(t.label, c);
        const n = counts.get(k0) ?? 0;
        counts.set(k0, n + 1);
        m.set(`${k0}#${n}`, { track: t.label, clip: c });
      }
    }
    return m;
  };
  const a = index(base);
  const b = index(next);
  const out: RevisionDiff = { added: [], removed: [], changed: [], durationDeltaFrames: next.durationFrames - base.durationFrames };
  for (const [k, v] of b) {
    const prev = a.get(k);
    if (!prev) {
      out.added.push(v);
      continue;
    }
    const what: string[] = [];
    if (prev.clip.timelineInFrames !== v.clip.timelineInFrames) what.push('position');
    if (prev.clip.durationFrames !== v.clip.durationFrames) what.push('duration');
    if (prev.clip.sourceInFrames !== v.clip.sourceInFrames || prev.clip.sourceOutFrames !== v.clip.sourceOutFrames) what.push('source range');
    if (what.length) out.changed.push({ track: v.track, before: prev.clip, after: v.clip, what });
  }
  for (const [k, v] of a) if (!b.has(k)) out.removed.push(v);
  return out;
}
