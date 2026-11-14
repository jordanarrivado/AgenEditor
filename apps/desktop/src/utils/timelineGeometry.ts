/**
 * Pure timeline geometry + revision edit helpers (spec §10). All positions are
 * derived from integer output frames; nothing here touches media.
 */
import type { DemoRevision, DemoTimelineClip, DemoTimelineTrack } from '../types';

export const frameToPx = (frames: number, fps: number, pps: number) => (frames / fps) * pps;
export const pxToFrame = (px: number, fps: number, pps: number) => Math.round((px / pps) * fps);

export interface ClipRef {
  clip: DemoTimelineClip;
  track: DemoTimelineTrack;
}

export function findClip(rev: DemoRevision, clipId: string | null): ClipRef | null {
  if (!clipId) return null;
  for (const track of rev.tracks) {
    const clip = track.clips.find((c) => c.id === clipId);
    if (clip) return { clip, track };
  }
  return null;
}

export function clipAt(track: DemoTimelineTrack | undefined, frame: number): DemoTimelineClip | undefined {
  return track?.clips.find((c) => frame >= c.timelineInFrames && frame < c.timelineInFrames + c.durationFrames);
}

/** Snap a frame to the nearest clip boundary within `threshold` frames. */
export function snapFrame(rev: DemoRevision, frame: number, threshold = 10): number {
  let best = frame;
  let bestDiff = threshold + 1;
  for (const t of rev.tracks) {
    for (const c of t.clips) {
      for (const edge of [c.timelineInFrames, c.timelineInFrames + c.durationFrames]) {
        const d = Math.abs(edge - frame);
        if (d < bestDiff) {
          bestDiff = d;
          best = edge;
        }
      }
    }
  }
  return bestDiff <= threshold ? best : frame;
}

/** Ruler tick spacing (seconds) that keeps labels ≥ ~60 px apart. */
export function rulerStepSeconds(pps: number): { minor: number; major: number } {
  const candidates = [1, 2, 5, 10, 15, 30, 60];
  const major = candidates.find((s) => s * pps >= 60) ?? 60;
  const minor = major >= 10 ? major / 5 : major === 5 ? 1 : major / 2 >= 1 ? major / 2 : 1;
  return { minor, major };
}

// ---- Immutable revision edits

export function patchClip(rev: DemoRevision, clipId: string, patch: Partial<DemoTimelineClip>): DemoRevision {
  return {
    ...rev,
    tracks: rev.tracks.map((t) => ({
      ...t,
      clips: t.clips.map((c) => (c.id === clipId ? { ...c, ...patch } : c)),
    })),
  };
}

export function patchTrack(rev: DemoRevision, trackId: string, patch: Partial<DemoTimelineTrack>): DemoRevision {
  return { ...rev, tracks: rev.tracks.map((t) => (t.id === trackId ? { ...t, ...patch } : t)) };
}

export function removeClip(rev: DemoRevision, clipId: string): DemoRevision {
  return { ...rev, tracks: rev.tracks.map((t) => ({ ...t, clips: t.clips.filter((c) => c.id !== clipId) })) };
}

let dupSeq = 0;
/** Duplicate a clip directly after itself (demo timeline only; may overlap following clips). */
export function duplicateClip(rev: DemoRevision, clipId: string): { revision: DemoRevision; newClipId: string } | null {
  const ref = findClip(rev, clipId);
  if (!ref) return null;
  const newClipId = `${clipId}_dup${Date.now().toString(36)}${dupSeq++}`;
  const copy: DemoTimelineClip = {
    ...ref.clip,
    id: newClipId,
    timelineInFrames: Math.min(rev.durationFrames - 1, ref.clip.timelineInFrames + ref.clip.durationFrames),
    label: `${ref.clip.label ?? 'Clip'} (copy)`,
  };
  copy.durationFrames = Math.max(1, Math.min(copy.durationFrames, rev.durationFrames - copy.timelineInFrames));
  return {
    revision: {
      ...rev,
      tracks: rev.tracks.map((t) =>
        t.id === ref.track.id
          ? { ...t, clips: [...t.clips, copy].sort((a, b) => a.timelineInFrames - b.timelineInFrames) }
          : t
      ),
    },
    newClipId,
  };
}

/** Trim a clip's duration from the tail; keeps source in/out consistent. Clamped to timeline length. */
export function setClipDuration(rev: DemoRevision, clipId: string, durationFrames: number): DemoRevision {
  const ref = findClip(rev, clipId);
  if (!ref) return rev;
  const max = rev.durationFrames - ref.clip.timelineInFrames;
  const d = Math.max(1, Math.min(max, Math.round(durationFrames)));
  return patchClip(rev, clipId, { durationFrames: d, sourceOutFrames: ref.clip.sourceInFrames + d });
}

/** Move a clip on its track (visual demo only). Clamped to the timeline. */
export function setClipStart(rev: DemoRevision, clipId: string, startFrames: number): DemoRevision {
  const ref = findClip(rev, clipId);
  if (!ref) return rev;
  const s = Math.max(0, Math.min(rev.durationFrames - ref.clip.durationFrames, Math.round(startFrames)));
  return patchClip(rev, clipId, { timelineInFrames: s });
}

export function describeClip(ref: ClipRef, fps: number, tc: (f: number, fps: number) => string): string {
  const { clip, track } = ref;
  return [
    `Clip: ${clip.label ?? clip.id} (${clip.id})`,
    `Track: ${track.label}`,
    `Asset: ${clip.assetId}`,
    `Timeline: ${tc(clip.timelineInFrames, fps)} → ${tc(clip.timelineInFrames + clip.durationFrames, fps)} (${clip.durationFrames} frames)`,
    `Source: ${tc(clip.sourceInFrames, fps)} → ${tc(clip.sourceOutFrames, fps)}`,
    '[AgenEditor UI prototype · demo timeline data]',
  ].join('\n');
}
