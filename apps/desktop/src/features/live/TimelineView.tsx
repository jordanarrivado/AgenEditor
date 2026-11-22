import React from 'react';
import type { EngineAsset, EngineTimeline } from '../../services/liveEngineRepository';

const TRACKS: { id: string; label: string; tone: string }[] = [
  { id: 'V1', label: 'V1 main', tone: 'bg-accent/70' },
  { id: 'V2', label: 'V2 overlays', tone: 'bg-sky-500/70' },
  { id: 'A1', label: 'A1 dialogue', tone: 'bg-emerald-500/60' },
  { id: 'A2', label: 'A2 music', tone: 'bg-violet-500/60' },
  { id: 'A3', label: 'A3 effects', tone: 'bg-amber-500/70' },
  { id: 'A4', label: 'A4 voice', tone: 'bg-rose-500/60' },
];

interface Cue { cue_id: string; text: string; timeline_in_frames: number; timeline_out_frames: number }

export interface TimelineViewProps {
  timeline: EngineTimeline;
  assets: EngineAsset[];
  /** Playhead position of the preview video, in seconds. */
  currentTime: number;
  /** Revision the preview was rendered from; seeking is only offered when it matches. */
  previewRevision: number | null;
  onSeek: (seconds: number) => void;
}

const span = (clip: { source_in_frames: number; source_out_frames: number; timeline_duration_frames?: number | null }) =>
  clip.timeline_duration_frames ?? clip.source_out_frames - clip.source_in_frames;

/**
 * A read-only multitrack view of the current revision, drawn to scale. The playhead follows the rendered preview
 * and clicking seeks it; this is navigation over the render, not live multitrack playback.
 */
export const TimelineView: React.FC<TimelineViewProps> = ({ timeline, assets, currentTime, previewRevision, onSeek }) => {
  const fps = timeline.render_profile.frame_rate.num / timeline.render_profile.frame_rate.den;
  const v1End = Math.max(0, ...timeline.clips.filter((c) => c.track === 'V1').map((c) => c.timeline_in_frames + span(c)));
  const total = Math.max(1, v1End, ...timeline.clips.map((c) => c.timeline_in_frames + span(c)));
  const names = Object.fromEntries(assets.map((asset) => [asset.id, asset.original_filename]));
  const synced = previewRevision === timeline.revision;
  const cues = (timeline.captions as Cue[]).filter((cue) => typeof cue?.timeline_in_frames === 'number');
  const used = TRACKS.filter((track) => track.id === 'V1' || timeline.clips.some((c) => c.track === track.id));
  const pct = (frames: number) => `${(frames / total) * 100}%`;

  const seek = (event: React.MouseEvent<HTMLDivElement>) => {
    if (!synced) return;
    const box = event.currentTarget.getBoundingClientRect();
    const share = Math.min(1, Math.max(0, (event.clientX - box.left) / box.width));
    onSeek((share * total) / fps);
  };

  return (
    <section aria-labelledby="timeline-title" className="space-y-1.5 rounded-2xl border border-line bg-surface p-3 text-xs">
      <div className="flex items-center justify-between gap-2">
        <h3 id="timeline-title" className="font-semibold">Timeline · revision {timeline.revision}</h3>
        <span className="text-fg-3">{(v1End / fps).toFixed(1)}s{synced ? ' · click to jump the preview' : previewRevision === null ? ' · no preview yet' : ` · preview is of revision ${previewRevision}`}</span>
      </div>
      <div className="relative space-y-1" data-testid="timeline-tracks">
        {used.map((track) => (
          <div key={track.id} className="flex items-center gap-2">
            <span className="w-20 shrink-0 text-fg-3">{track.label}</span>
            <div role={synced ? 'button' : undefined} aria-label={synced ? `Seek on ${track.label}` : undefined} tabIndex={synced ? 0 : undefined}
              onClick={seek} className={`relative h-6 flex-1 overflow-hidden rounded bg-app ${synced ? 'cursor-pointer' : ''}`}>
              {timeline.clips.filter((c) => c.track === track.id).map((clip) => (
                <span key={clip.clip_id} title={`${clip.clip_id} · ${names[clip.asset_id] ?? clip.asset_id}${clip.rationale ? ` · ${clip.rationale}` : ''}`}
                  data-testid={`clip-${clip.clip_id}`}
                  className={`absolute top-0.5 bottom-0.5 truncate rounded px-1 leading-5 text-white ${track.tone}`}
                  style={{ left: pct(clip.timeline_in_frames), width: pct(span(clip)) }}>
                  {names[clip.asset_id] ?? clip.clip_id}
                </span>
              ))}
            </div>
          </div>
        ))}
        {cues.length > 0 && (
          <div className="flex items-center gap-2">
            <span className="w-20 shrink-0 text-fg-3">T1 captions</span>
            <div onClick={seek} className={`relative h-5 flex-1 overflow-hidden rounded bg-app ${synced ? 'cursor-pointer' : ''}`}>
              {cues.map((cue) => (
                <span key={cue.cue_id} title={cue.text} className="absolute top-0.5 bottom-0.5 rounded bg-fg-3/40"
                  style={{ left: pct(cue.timeline_in_frames), width: pct(cue.timeline_out_frames - cue.timeline_in_frames) }} />
              ))}
            </div>
          </div>
        )}
        {synced && (
          <span aria-hidden data-testid="playhead" className="pointer-events-none absolute top-0 bottom-0 w-px bg-danger"
            style={{ left: `calc(5.5rem + (100% - 5.5rem) * ${Math.min(1, (currentTime * fps) / total)})` }} />
        )}
      </div>
    </section>
  );
};
