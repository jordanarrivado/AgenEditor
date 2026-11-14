import React, { useCallback, useEffect, useMemo, useRef } from 'react';
import * as ContextMenu from '@radix-ui/react-context-menu';
import {
  Copy,
  Eye,
  EyeOff,
  Info,
  Magnet,
  Maximize2,
  Trash2,
  Volume2,
  VolumeX,
  ZoomIn,
  ZoomOut,
  Headphones,
  ClipboardCopy,
} from 'lucide-react';
import { useEditorStore, ZOOM_MAX, ZOOM_MIN } from '../../stores/editorStore';
import type { DemoRevision, DemoTimelineClip, DemoTimelineTrack, TrackKind } from '../../types';
import { framesToClock, framesToTimecode } from '../../utils/timecode';
import { frameToPx, pxToFrame, rulerStepSeconds, snapFrame } from '../../utils/timelineGeometry';
import { IconButton, cx } from '../ui/primitives';

export const TRACK_STYLE: Record<TrackKind, { bar: string; dot: string; text: string }> = {
  broll: { bar: 'bg-track-broll', dot: 'bg-track-broll', text: 'text-[#08121A]' },
  video: { bar: 'bg-track-video', dot: 'bg-track-video', text: 'text-[#08121A]' },
  dialogue: { bar: 'bg-track-audio', dot: 'bg-track-audio', text: 'text-[#08121A]' },
  music: { bar: 'bg-track-audio/70', dot: 'bg-track-audio', text: 'text-[#08121A]' },
  captions: { bar: 'bg-track-caption', dot: 'bg-track-caption', text: 'text-[#08121A]' },
};

const ROW_H = 36;
const RULER_H = 26;
const LABEL_W = 184;

interface Props {
  revision: DemoRevision;
  onToggleTrack: (trackId: string, key: 'muted' | 'hidden') => void;
  onDuplicateClip: (clipId: string) => void;
  onRemoveClip: (clipId: string) => void;
  onCopyClipInfo: (clipId: string) => void;
}

export const Timeline: React.FC<Props> = ({ revision, onToggleTrack, onDuplicateClip, onRemoveClip, onCopyClipInfo }) => {
  const [ui, store] = useEditorStore();
  const { playheadFrames, pixelsPerSecond: pps, snappingEnabled, selectedClipId, fps, soloTrackId, timelineHeight } = ui;
  const scrollRef = useRef<HTMLDivElement>(null);
  const labelsRef = useRef<HTMLDivElement>(null);

  const totalPx = frameToPx(revision.durationFrames, fps, pps);
  const playheadPx = frameToPx(playheadFrames, fps, pps);

  const ticks = useMemo(() => {
    const { minor, major } = rulerStepSeconds(pps);
    const seconds = revision.durationFrames / fps;
    const out: { s: number; x: number; major: boolean }[] = [];
    for (let s = 0; s <= seconds + 1e-6; s += minor) {
      const rounded = Math.round(s * 1000) / 1000;
      out.push({ s: rounded, x: rounded * pps, major: Math.abs(rounded % major) < 1e-6 });
    }
    return out;
  }, [pps, revision.durationFrames, fps]);

  const seekFromPointer = useCallback(
    (clientX: number, el: HTMLElement) => {
      const rect = el.getBoundingClientRect();
      let f = pxToFrame(clientX - rect.left, fps, pps);
      if (snappingEnabled) f = snapFrame(revision, f, Math.max(2, Math.round((8 / pps) * fps)));
      store.setPlayhead(f);
    },
    [fps, pps, snappingEnabled, revision, store]
  );

  const onRulerPointerDown = (e: React.PointerEvent<HTMLDivElement>) => {
    const el = e.currentTarget;
    el.setPointerCapture(e.pointerId);
    store.pause();
    seekFromPointer(e.clientX, el);
    const move = (ev: PointerEvent) => seekFromPointer(ev.clientX, el);
    const up = () => {
      el.removeEventListener('pointermove', move);
      el.removeEventListener('pointerup', up);
    };
    el.addEventListener('pointermove', move);
    el.addEventListener('pointerup', up);
  };

  // Vertical resize of the timeline dock (separate from clip selection).
  const onResizePointerDown = (e: React.PointerEvent<HTMLDivElement>) => {
    e.preventDefault();
    const startY = e.clientY;
    const startH = timelineHeight;
    const move = (ev: PointerEvent) => store.setTimelineHeight(startH + (startY - ev.clientY));
    const up = () => {
      window.removeEventListener('pointermove', move);
      window.removeEventListener('pointerup', up);
    };
    window.addEventListener('pointermove', move);
    window.addEventListener('pointerup', up);
  };

  const fit = useCallback(() => {
    if (scrollRef.current && scrollRef.current.clientWidth > 0) store.zoomFit(scrollRef.current.clientWidth);
  }, [store]);

  // Fit the whole revision into view when it is opened.
  useEffect(() => {
    fit();
  }, [revision.id, fit]);

  const selected = selectedClipId;

  return (
    <section
      aria-label="Timeline"
      className="relative shrink-0 border-t border-line bg-sidebar flex flex-col overflow-hidden"
      style={{ height: timelineHeight }}
    >
      {/* Resize handle */}
      <div
        role="separator"
        aria-orientation="horizontal"
        aria-label="Resize timeline"
        aria-valuemin={180}
        aria-valuemax={440}
        aria-valuenow={timelineHeight}
        tabIndex={0}
        onPointerDown={onResizePointerDown}
        onKeyDown={(e) => {
          if (e.key === 'ArrowUp') store.setTimelineHeight(timelineHeight + 20);
          if (e.key === 'ArrowDown') store.setTimelineHeight(timelineHeight - 20);
        }}
        className="absolute -top-1 left-0 right-0 h-2 cursor-row-resize z-40 hover:bg-accent/30 focus-visible:bg-accent/40"
      />

      {/* Header toolbar */}
      <div className="h-10 shrink-0 px-2 bg-surface border-b border-line flex items-center justify-between gap-2 text-xs">
        <div className="flex items-center gap-1">
          <IconButton label="Zoom out (−)" size="sm" onClick={() => store.zoomOut()} disabled={pps <= ZOOM_MIN}>
            <ZoomOut className="w-4 h-4" />
          </IconButton>
          <input
            type="range"
            aria-label="Timeline zoom"
            min={ZOOM_MIN}
            max={ZOOM_MAX}
            value={pps}
            onChange={(e) => store.setZoom(Number(e.target.value))}
            className="w-24 accent-accent"
          />
          <IconButton label="Zoom in (+)" size="sm" onClick={() => store.zoomIn()} disabled={pps >= ZOOM_MAX}>
            <ZoomIn className="w-4 h-4" />
          </IconButton>
          <IconButton label="Fit timeline to window" size="sm" onClick={fit}>
            <Maximize2 className="w-3.5 h-3.5" />
          </IconButton>
          <span className="w-px h-4 bg-line mx-1" />
          <button
            type="button"
            aria-pressed={snappingEnabled}
            onClick={() => store.toggleSnapping()}
            className={cx(
              'h-7 px-2 rounded-md inline-flex items-center gap-1 font-medium',
              snappingEnabled ? 'bg-accent/15 text-accent' : 'text-fg-3 hover:text-fg hover:bg-elevated'
            )}
          >
            <Magnet className="w-3.5 h-3.5" /> Snap
          </button>
          {selected && (
            <>
              <span className="w-px h-4 bg-line mx-1" />
              <button
                type="button"
                onClick={() => onDuplicateClip(selected)}
                className="h-7 px-2 rounded-md inline-flex items-center gap-1 text-fg-2 hover:text-fg hover:bg-elevated"
              >
                <Copy className="w-3.5 h-3.5" /> Duplicate
              </button>
              <button
                type="button"
                onClick={() => onRemoveClip(selected)}
                className="h-7 px-2 rounded-md inline-flex items-center gap-1 text-danger hover:bg-danger/10"
              >
                <Trash2 className="w-3.5 h-3.5" /> Remove
              </button>
            </>
          )}
        </div>
        <div className="flex items-center gap-3 font-mono">
          <span className="text-fg-3 hidden lg:inline">{pps}px/s</span>
          <span className="text-accent font-semibold" aria-label="Playhead timecode">
            {framesToTimecode(playheadFrames, fps)}
          </span>
        </div>
      </div>

      <div className="flex-1 flex min-h-0">
        {/* Track labels (sticky column) */}
        <div className="shrink-0 border-r border-line bg-sidebar flex flex-col" style={{ width: LABEL_W }}>
          <div className="shrink-0 border-b border-line px-2 flex items-center text-[11px] font-mono text-fg-3" style={{ height: RULER_H }}>
            TRACKS
          </div>
          <div ref={labelsRef} className="flex-1 overflow-hidden">
            {revision.tracks.map((t) => (
              <TrackLabel
                key={t.id}
                track={t}
                soloed={soloTrackId === t.id}
                onToggle={onToggleTrack}
                onSolo={() => store.toggleSolo(t.id)}
              />
            ))}
          </div>
        </div>

        {/* Scrollable lanes */}
        <div
          ref={scrollRef}
          data-testid="timeline-scroll"
          className="flex-1 overflow-auto relative bg-app"
          onScroll={(e) => {
            if (labelsRef.current) labelsRef.current.scrollTop = e.currentTarget.scrollTop;
          }}
        >
          <div className="relative" style={{ width: totalPx + 48, minHeight: '100%' }}>
            {/* Ruler */}
            <div
              role="slider"
              aria-label="Timeline ruler — click or drag to move playhead"
              aria-valuemin={0}
              aria-valuemax={revision.durationFrames}
              aria-valuenow={playheadFrames}
              aria-valuetext={framesToTimecode(playheadFrames, fps)}
              tabIndex={0}
              onPointerDown={onRulerPointerDown}
              className="sticky top-0 z-20 bg-surface border-b border-line cursor-ew-resize"
              style={{ height: RULER_H, width: totalPx + 48 }}
            >
              {ticks.map((t) => (
                <div key={t.s} className="absolute top-0 bottom-0 pointer-events-none" style={{ left: t.x }}>
                  <div className={cx('w-px', t.major ? 'h-full bg-fg-3/60' : 'h-2 bg-line')} />
                  {t.major && (
                    <span className="absolute top-1 left-1 text-[10px] font-mono text-fg-3 whitespace-nowrap">
                      {framesToClock(t.s * fps, fps)}
                    </span>
                  )}
                </div>
              ))}
              {/* end-of-timeline marker */}
              <div className="absolute top-0 bottom-0 w-px bg-warning/70" style={{ left: totalPx }} />
            </div>

            {/* Lanes */}
            {revision.tracks.map((track) => {
              const dimmed = track.hidden || (soloTrackId !== null && soloTrackId !== track.id);
              return (
                <div
                  key={track.id}
                  className={cx('relative border-b border-line-subtle', dimmed && 'opacity-35')}
                  style={{ height: ROW_H, width: totalPx }}
                  onPointerDown={(e) => {
                    if (e.target === e.currentTarget) store.selectClip(null);
                  }}
                >
                  {track.clips.map((clip) => (
                    <ClipBlock
                      key={clip.id}
                      clip={clip}
                      track={track}
                      fps={fps}
                      pps={pps}
                      selected={selected === clip.id}
                      onSelect={() => store.selectClip(clip.id, track.id)}
                      onInspect={() => {
                        store.selectClip(clip.id, track.id);
                        store.setActiveInspectorTab('clip');
                      }}
                      onDuplicate={() => onDuplicateClip(clip.id)}
                      onRemove={() => onRemoveClip(clip.id)}
                      onCopy={() => onCopyClipInfo(clip.id)}
                    />
                  ))}
                </div>
              );
            })}

            {/* Playhead */}
            <div
              aria-hidden
              data-testid="timeline-playhead"
              className="absolute top-0 bottom-0 z-30 pointer-events-none"
              style={{ left: playheadPx, transform: 'translateX(-50%)' }}
            >
              <div className="w-2.5 h-2.5 rotate-45 bg-accent mx-auto -mt-0.5" />
              <div className="w-0.5 h-full bg-accent mx-auto" />
            </div>
          </div>
        </div>
      </div>
    </section>
  );
};

const TrackLabel: React.FC<{
  track: DemoTimelineTrack;
  soloed: boolean;
  onToggle: (id: string, key: 'muted' | 'hidden') => void;
  onSolo: () => void;
}> = ({ track, soloed, onToggle, onSolo }) => {
  const audio = track.type === 'dialogue' || track.type === 'music';
  return (
    <div className="flex items-center justify-between gap-1 px-2 border-b border-line-subtle" style={{ height: ROW_H }}>
      <div className="flex items-center gap-1.5 min-w-0">
        <span aria-hidden className={cx('w-2 h-2 rounded-full shrink-0', TRACK_STYLE[track.type].dot)} />
        <span className="text-xs font-medium text-fg truncate" title={track.label}>
          {track.label}
        </span>
      </div>
      <div className="flex items-center shrink-0">
        <IconButton
          size="sm"
          label={track.hidden ? `Show ${track.label}` : `Hide ${track.label}`}
          active={track.hidden}
          onClick={() => onToggle(track.id, 'hidden')}
          className="!w-6 !h-6"
        >
          {track.hidden ? <EyeOff className="w-3.5 h-3.5" /> : <Eye className="w-3.5 h-3.5" />}
        </IconButton>
        {audio && (
          <>
            <IconButton
              size="sm"
              label={track.muted ? `Unmute ${track.label}` : `Mute ${track.label}`}
              active={track.muted}
              onClick={() => onToggle(track.id, 'muted')}
              className="!w-6 !h-6"
            >
              {track.muted ? <VolumeX className="w-3.5 h-3.5" /> : <Volume2 className="w-3.5 h-3.5" />}
            </IconButton>
            <IconButton size="sm" label={`Solo ${track.label}`} active={soloed} onClick={onSolo} className="!w-6 !h-6">
              <Headphones className="w-3.5 h-3.5" />
            </IconButton>
          </>
        )}
      </div>
    </div>
  );
};

const ClipBlock: React.FC<{
  clip: DemoTimelineClip;
  track: DemoTimelineTrack;
  fps: number;
  pps: number;
  selected: boolean;
  onSelect: () => void;
  onInspect: () => void;
  onDuplicate: () => void;
  onRemove: () => void;
  onCopy: () => void;
}> = ({ clip, track, fps, pps, selected, onSelect, onInspect, onDuplicate, onRemove, onCopy }) => {
  const left = frameToPx(clip.timelineInFrames, fps, pps);
  const width = Math.max(6, frameToPx(clip.durationFrames, fps, pps));
  const style = TRACK_STYLE[track.type];
  const name = clip.label ?? 'Clip';
  return (
    <ContextMenu.Root>
      <ContextMenu.Trigger asChild>
        <button
          type="button"
          data-clip-id={clip.id}
          aria-pressed={selected}
          aria-label={`${track.label}: ${name}, ${framesToTimecode(clip.timelineInFrames, fps)} for ${(clip.durationFrames / fps).toFixed(1)} seconds`}
          onClick={(e) => {
            e.stopPropagation();
            onSelect();
          }}
          onContextMenu={onSelect}
          className={cx(
            'absolute top-1 bottom-1 rounded-[5px] overflow-hidden text-left flex items-center',
            style.bar,
            selected ? 'ring-2 ring-fg ring-offset-1 ring-offset-app z-10' : 'hover:brightness-110'
          )}
          style={{ left, width }}
        >
          {/* trimmed-edge handles (visual only) */}
          <span aria-hidden className="absolute left-0 top-0 bottom-0 w-1 bg-black/25" />
          <span aria-hidden className="absolute right-0 top-0 bottom-0 w-1 bg-black/25" />
          {width > 36 && (
            <span className={cx('px-2 text-[11px] font-semibold truncate leading-tight', style.text)}>
              {name}
              {width > 140 && (
                <span className="block text-[9px] font-mono opacity-70 truncate">{clip.assetId.replace(/^asset_/, '#')}</span>
              )}
            </span>
          )}
        </button>
      </ContextMenu.Trigger>
      <ContextMenu.Portal>
        <ContextMenu.Content className="z-[70] min-w-[220px] rounded-lg border border-line bg-elevated p-1 text-[13px] text-fg shadow-xl">
          <MenuItem icon={<Info className="w-3.5 h-3.5" />} onSelect={onInspect}>
            Inspect
          </MenuItem>
          <MenuItem icon={<Copy className="w-3.5 h-3.5" />} onSelect={onDuplicate}>
            Duplicate in demo timeline
          </MenuItem>
          <MenuItem icon={<ClipboardCopy className="w-3.5 h-3.5" />} onSelect={onCopy}>
            Copy clip info
          </MenuItem>
          <ContextMenu.Separator className="h-px bg-line my-1" />
          <MenuItem icon={<Trash2 className="w-3.5 h-3.5" />} onSelect={onRemove} danger>
            Remove from demo timeline
          </MenuItem>
        </ContextMenu.Content>
      </ContextMenu.Portal>
    </ContextMenu.Root>
  );
};

const MenuItem: React.FC<{ icon: React.ReactNode; onSelect: () => void; danger?: boolean; children: React.ReactNode }> = ({
  icon,
  onSelect,
  danger,
  children,
}) => (
  <ContextMenu.Item
    onSelect={onSelect}
    className={cx(
      'flex items-center gap-2 px-2 py-1.5 rounded-md outline-none cursor-default data-[highlighted]:bg-surface',
      danger ? 'text-danger' : 'text-fg'
    )}
  >
    {icon}
    {children}
  </ContextMenu.Item>
);
