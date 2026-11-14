import React, { useMemo, useState } from 'react';
import { ChevronsLeft, ChevronsRight, Film, Grid3X3, Pause, Play, ShieldAlert, SkipBack, SkipForward, User } from 'lucide-react';
import { useEditorStore } from '../../stores/editorStore';
import type { DemoProject, DemoRevision } from '../../types';
import { framesToTimecode } from '../../utils/timecode';
import { clipAt } from '../../utils/timelineGeometry';
import { usePrefersReducedMotion } from '../../app/SettingsContext';
import { Badge, IconButton, Segmented, cx } from '../ui/primitives';

interface Props {
  project: DemoProject;
  revision: DemoRevision;
}

type StageScale = 'fit' | '75' | '50';

export const PreviewStage: React.FC<Props> = ({ project, revision }) => {
  const [ui, store] = useEditorStore();
  const { playheadFrames: f, isPlaying, safeZonesVisible, gridGuidesVisible, captionStyle: cap, fps } = ui;
  const reduced = usePrefersReducedMotion();
  const [scale, setScale] = useState<StageScale>('fit');

  const active = useMemo(() => {
    const visible = (type: string) => revision.tracks.find((t) => t.type === type && !t.hidden);
    return {
      broll: clipAt(visible('broll'), f),
      video: clipAt(visible('video'), f),
      caption: clipAt(visible('captions'), f),
    };
  }, [revision, f]);

  const portrait = project.height > project.width;
  const aspect = `${project.width} / ${project.height}`;
  const ratioLabel = portrait ? '9:16' : project.width === project.height ? '1:1' : '16:9';
  const scalePct = scale === 'fit' ? 100 : Number(scale);
  const animate = isPlaying && !reduced;
  const cropX = active.video?.cropPosition?.x ?? 50;

  return (
    <section aria-label="Preview" className="h-full flex flex-col bg-app min-w-0 min-h-0">
      {/* Quick settings */}
      <div className="h-10 shrink-0 px-3 flex items-center justify-between gap-2 border-b border-line text-xs">
        <div className="flex items-center gap-2 min-w-0">
          <Badge tone="accent">{ratioLabel}</Badge>
          <span className="font-mono text-fg-3 hidden md:inline">
            {project.width}×{project.height} · {fps} fps
          </span>
        </div>
        <div className="flex items-center gap-1">
          <Segmented
            size="sm"
            label="Preview scale"
            value={scale}
            onChange={setScale}
            options={[
              { value: 'fit', label: 'Fit', title: 'Fit preview to available space' },
              { value: '75', label: '75%' },
              { value: '50', label: '50%' },
            ]}
          />
          <IconButton label="Toggle safe-zone overlay (Z)" size="sm" active={safeZonesVisible} onClick={() => store.toggleSafeZones()}>
            <ShieldAlert className="w-4 h-4" />
          </IconButton>
          <IconButton label="Toggle framing guides (G)" size="sm" active={gridGuidesVisible} onClick={() => store.toggleGridGuides()}>
            <Grid3X3 className="w-4 h-4" />
          </IconButton>
        </div>
      </div>

      {/* Stage */}
      <div className="flex-1 min-h-0 flex items-center justify-center p-3 overflow-hidden">
        <div
          data-testid="preview-stage"
          className="relative rounded-lg overflow-hidden border border-line bg-[#0D131C] shadow-2xl [container-type:inline-size]"
          style={{
            aspectRatio: aspect,
            height: portrait ? `${scalePct}%` : undefined,
            width: portrait ? undefined : `${scalePct}%`,
            maxHeight: '100%',
            maxWidth: '100%',
          }}
        >
          {/* Placeholder art (CSS only — not a video frame) */}
          {active.broll ? (
            <div className="absolute inset-0 flex flex-col items-center justify-start pt-[24%] text-center p-6 bg-gradient-to-b from-[#132634] via-[#0F1E2B] to-[#0B1520]">
              <div className="absolute inset-0 opacity-20 bg-[radial-gradient(var(--track-broll)_1px,transparent_1px)] [background-size:16px_16px]" />
              <Film aria-hidden className="relative w-8 h-8 text-track-broll mb-3" />
              <span className="relative text-[10px] uppercase font-mono tracking-wider text-track-broll">V2 · B-roll placeholder</span>
              <span className="relative text-sm font-semibold text-white mt-1.5">{active.broll.label}</span>
            </div>
          ) : active.video ? (
            <div className="absolute inset-0 flex flex-col items-center justify-start pt-[22%] bg-gradient-to-b from-[#161F2C] via-[#101826] to-[#0A0E17]">
              <div className="absolute inset-0 opacity-15 bg-[radial-gradient(var(--track-video)_1px,transparent_1px)] [background-size:20px_20px]" />
              <div
                className="relative flex flex-col items-center"
                style={{
                  transform: `translateX(${(50 - cropX) * 0.6}%) ${animate ? `translateY(${Math.sin(f * 0.2) * 2}px)` : ''}`,
                }}
              >
                <div className="w-20 h-20 rounded-full bg-gradient-to-tr from-[#1E293B] to-[#334155] border-2 border-track-video/40 flex items-center justify-center">
                  <User aria-hidden className="w-10 h-10 text-track-video/70" />
                </div>
                <div className="w-36 h-24 -mt-2.5 rounded-t-3xl bg-gradient-to-t from-[#1C2634] to-[#1E293B]/70 border-t border-track-video/30" />
              </div>
              <span className="relative text-[10px] uppercase font-mono tracking-wider text-[#A7C2FF] mt-3">V1 · Speaker placeholder</span>
              <span className="relative text-xs text-white/80 mt-1">{active.video.label}</span>
            </div>
          ) : (
            <div className="absolute inset-0 flex items-center justify-center text-xs text-white/60 font-mono">No video clip at playhead</div>
          )}

          {gridGuidesVisible && (
            <div aria-hidden className="absolute inset-0 pointer-events-none grid grid-cols-3 grid-rows-3">
              {Array.from({ length: 9 }).map((_, i) => (
                <div key={i} className="border-r border-b border-white/15" />
              ))}
            </div>
          )}

          {safeZonesVisible && (
            <div aria-hidden data-testid="safe-zone-overlay" className="absolute inset-0 pointer-events-none">
              <div className="absolute top-0 inset-x-0 h-[8%] bg-[#F2BE6B]/10 border-b border-dashed border-[#F2BE6B]/60" />
              <div className="absolute bottom-0 inset-x-0 h-[20%] bg-[#F2BE6B]/10 border-t border-dashed border-[#F2BE6B]/60">
                <span className="absolute top-1 left-2 text-[9px] font-mono text-[#F2BE6B]">Platform UI zone</span>
              </div>
              <div className="absolute top-[8%] bottom-[20%] right-0 w-[12%] bg-[#F2BE6B]/5 border-l border-dashed border-[#F2BE6B]/40" />
            </div>
          )}

          {active.caption && (
            <div
              data-testid="caption-overlay"
              className={cx(
                'absolute inset-x-[6%] text-center pointer-events-none flex justify-center',
                cap.position === 'top' && 'top-[12%]',
                cap.position === 'middle' && 'top-1/2 -translate-y-1/2',
                cap.position === 'bottom' && 'bottom-[22%]'
              )}
            >
              <span
                className={cx('px-2 py-1 rounded-md font-bold leading-tight', cap.background && 'bg-black/70')}
                style={{
                  // caption size is defined for a 360-px-wide reference frame and scales with the stage
                  fontSize: `${(cap.fontSize / 360) * 100}cqw`,
                  color: cap.color,
                  textTransform: cap.uppercase ? 'uppercase' : 'none',
                  textShadow: cap.background ? undefined : '0 2px 6px rgba(0,0,0,.9)',
                }}
              >
                {active.caption.label}
              </span>
            </div>
          )}

          <span className="absolute top-2 left-2 text-[10px] font-mono text-white bg-black/60 px-1.5 py-0.5 rounded pointer-events-none">
            {framesToTimecode(f, fps)}
          </span>
        </div>
      </div>

      {/* Persistent honesty label (spec §4.6 / §10) */}
      <p data-testid="simulated-label" className="shrink-0 text-center text-[11px] text-fg-3 px-3 pb-1">
        Simulated timeline preview — no rendered video or audio
      </p>

      {/* Transport */}
      <div className="h-11 shrink-0 px-3 flex items-center justify-between gap-2 border-t border-line">
        <div className="flex items-center gap-0.5">
          <IconButton label="Go to start (Home)" size="sm" onClick={() => store.setPlayhead(0)}>
            <ChevronsLeft className="w-4 h-4" />
          </IconButton>
          <IconButton label="Back 1 frame (←)" size="sm" onClick={() => store.stepFrames(-1)}>
            <SkipBack className="w-4 h-4" />
          </IconButton>
          <button
            type="button"
            onClick={() => store.togglePlay()}
            aria-label={isPlaying ? 'Pause simulated playhead' : 'Play simulated playhead'}
            className="mx-1 h-8 px-3 rounded-md bg-accent text-on-accent hover:bg-accent-hover inline-flex items-center gap-1.5 text-xs font-semibold"
          >
            {isPlaying ? <Pause className="w-3.5 h-3.5 fill-current" /> : <Play className="w-3.5 h-3.5 fill-current" />}
            {isPlaying ? 'Pause' : 'Play'}
          </button>
          <IconButton label="Forward 1 frame (→)" size="sm" onClick={() => store.stepFrames(1)}>
            <SkipForward className="w-4 h-4" />
          </IconButton>
          <IconButton label="Go to end (End)" size="sm" onClick={() => store.setPlayhead(revision.durationFrames)}>
            <ChevronsRight className="w-4 h-4" />
          </IconButton>
        </div>
        <div className="font-mono text-xs">
          <span className="text-fg font-semibold" data-testid="preview-timecode">
            {framesToTimecode(f, fps)}
          </span>
          <span className="text-fg-3"> / {framesToTimecode(revision.durationFrames, fps)}</span>
        </div>
      </div>
    </section>
  );
};
