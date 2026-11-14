import React from 'react';
import { Info, MousePointerClick, Sliders, Type, Volume2 } from 'lucide-react';
import { useEditorStore, type InspectorTab } from '../../stores/editorStore';
import type { DemoAsset, DemoProject, DemoRevision, DemoTimelineClip, DemoTimelineTrack } from '../../types';
import { CAPTION_PRESETS } from '../../mocks/fixtures';
import { framesToTimecode } from '../../utils/timecode';
import { findClip } from '../../utils/timelineGeometry';
import { Badge, Segmented, Switch, Unavailable, cx } from '../ui/primitives';
import { EmptyState } from '../ui/StateViews';

interface Props {
  project: DemoProject;
  revision: DemoRevision;
  assets: DemoAsset[];
  onPatchClip: (clipId: string, patch: Partial<DemoTimelineClip>, label: string) => void;
  onSetClipDuration: (clipId: string, frames: number) => void;
  onSetClipStart: (clipId: string, frames: number) => void;
  onPatchTrack: (trackId: string, patch: Partial<DemoTimelineTrack>, label: string) => void;
}

const TABS: { id: InspectorTab; label: string; icon: React.ReactNode }[] = [
  { id: 'clip', label: 'Clip', icon: <Sliders className="w-3.5 h-3.5" /> },
  { id: 'captions', label: 'Captions', icon: <Type className="w-3.5 h-3.5" /> },
  { id: 'audio', label: 'Audio', icon: <Volume2 className="w-3.5 h-3.5" /> },
  { id: 'project', label: 'Project', icon: <Info className="w-3.5 h-3.5" /> },
];

export const InspectorPanel: React.FC<Props> = (props) => {
  const [ui, store] = useEditorStore();
  return (
    <aside aria-label="Inspector" className="h-full flex flex-col bg-sidebar text-fg min-w-0">
      <div role="tablist" aria-label="Inspector sections" className="h-10 shrink-0 px-1.5 flex items-center gap-0.5 border-b border-line bg-surface">
        {TABS.map((t) => (
          <button
            key={t.id}
            role="tab"
            id={`insp-tab-${t.id}`}
            aria-selected={ui.activeInspectorTab === t.id}
            aria-controls="insp-panel"
            onClick={() => store.setActiveInspectorTab(t.id)}
            className={cx(
              'flex-1 h-7 rounded-md text-xs font-medium inline-flex items-center justify-center gap-1',
              ui.activeInspectorTab === t.id ? 'bg-elevated text-accent' : 'text-fg-3 hover:text-fg'
            )}
          >
            {t.icon}
            <span className="hidden xl:inline">{t.label}</span>
            <span className="xl:hidden sr-only">{t.label}</span>
          </button>
        ))}
      </div>
      <div id="insp-panel" role="tabpanel" aria-labelledby={`insp-tab-${ui.activeInspectorTab}`} className="flex-1 overflow-y-auto p-3.5 text-[13px]">
        {ui.activeInspectorTab === 'clip' && <ClipSection {...props} />}
        {ui.activeInspectorTab === 'captions' && <CaptionSection revision={props.revision} />}
        {ui.activeInspectorTab === 'audio' && <AudioSection {...props} />}
        {ui.activeInspectorTab === 'project' && <ProjectSection {...props} />}
      </div>
    </aside>
  );
};

// ------------------------------------------------------------------ helpers

const Group: React.FC<{ title: string; children: React.ReactNode; aside?: React.ReactNode }> = ({ title, children, aside }) => (
  <section className="mb-4">
    <div className="flex items-center justify-between mb-2">
      <h3 className="text-[11px] font-semibold uppercase tracking-wide text-fg-3">{title}</h3>
      {aside}
    </div>
    <div className="flex flex-col gap-2.5">{children}</div>
  </section>
);

const Row: React.FC<{ label: string; children: React.ReactNode }> = ({ label, children }) => (
  <div className="flex items-center justify-between gap-2">
    <span className="text-fg-2 text-xs">{label}</span>
    <span className="font-mono text-xs text-fg text-right">{children}</span>
  </div>
);

const FrameInput: React.FC<{
  id: string;
  label: string;
  value: number;
  min: number;
  max: number;
  fps: number;
  onCommit: (v: number) => void;
}> = ({ id, label, value, min, max, fps, onCommit }) => {
  const [draft, setDraft] = React.useState(String(value));
  React.useEffect(() => setDraft(String(value)), [value]);
  const commit = () => {
    const n = Number(draft);
    if (Number.isFinite(n) && n !== value) onCommit(Math.max(min, Math.min(max, Math.round(n))));
    else setDraft(String(value));
  };
  return (
    <div className="flex items-center justify-between gap-2">
      <label htmlFor={id} className="text-xs text-fg-2">
        {label}
      </label>
      <div className="flex items-center gap-1.5">
        <input
          id={id}
          type="number"
          inputMode="numeric"
          min={min}
          max={max}
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          onBlur={commit}
          onKeyDown={(e) => e.key === 'Enter' && commit()}
          className="w-20 h-7 px-2 rounded-md bg-app border border-line font-mono text-xs text-fg text-right focus:outline-none focus:border-accent"
        />
        <span className="font-mono text-[11px] text-fg-3 w-[68px]">{framesToTimecode(Number(draft) || 0, fps)}</span>
      </div>
    </div>
  );
};

// ------------------------------------------------------------------ Clip

const ClipSection: React.FC<Props> = ({ project, revision, assets, onPatchClip, onSetClipDuration, onSetClipStart, onPatchTrack }) => {
  const [ui] = useEditorStore();
  const ref = findClip(revision, ui.selectedClipId);
  const fps = project.fpsNum;
  if (!ref) {
    return (
      <EmptyState
        compact
        icon={<MousePointerClick className="w-5 h-5" />}
        title="Nothing selected"
        message="Select a clip to inspect its details."
      />
    );
  }
  const { clip, track } = ref;
  const asset = assets.find((a) => a.id === clip.assetId);
  const isVisual = track.type === 'video' || track.type === 'broll';

  return (
    <div>
      <div className="mb-4">
        <Badge tone="accent">{track.label}</Badge>
        <h2 className="text-[15px] font-semibold text-fg mt-1.5 leading-snug">{clip.label ?? 'Clip'}</h2>
        <p className="text-xs text-fg-3 mt-0.5 font-mono break-all">{asset?.filename ?? clip.assetId}</p>
      </div>

      {clip.rationale && (
        <Group title={track.type === 'broll' ? 'Why this B-roll' : 'Edit rationale'} aside={<Badge>Demo rationale</Badge>}>
          <p className="text-xs text-fg-2 leading-relaxed">{clip.rationale}</p>
        </Group>
      )}

      <Group title="Timing (demo frames)">
        <FrameInput
          id="clip-start"
          label="Timeline start"
          value={clip.timelineInFrames}
          min={0}
          max={revision.durationFrames - clip.durationFrames}
          fps={fps}
          onCommit={(v) => onSetClipStart(clip.id, v)}
        />
        <FrameInput
          id="clip-duration"
          label="Duration"
          value={clip.durationFrames}
          min={1}
          max={revision.durationFrames - clip.timelineInFrames}
          fps={fps}
          onCommit={(v) => onSetClipDuration(clip.id, v)}
        />
        <Row label="Timeline end">{framesToTimecode(clip.timelineInFrames + clip.durationFrames, fps)}</Row>
        <Row label="Source in">{framesToTimecode(clip.sourceInFrames, fps)}</Row>
        <Row label="Source out">{framesToTimecode(clip.sourceOutFrames, fps)}</Row>
        <p className="text-[11px] text-fg-3 leading-relaxed">
          Frame values edit the demo timeline only. Source-accurate trimming requires the media engine.
        </p>
      </Group>

      {isVisual && (
        <Group title="Framing">
          <div className="flex items-center justify-between gap-2">
            <span className="text-xs text-fg-2">Fit mode</span>
            <Segmented
              size="sm"
              label="Fit mode"
              value={clip.fitMode ?? 'cover'}
              onChange={(v) => onPatchClip(clip.id, { fitMode: v }, `Fit mode → ${v}`)}
              options={[
                { value: 'cover', label: 'Cover' },
                { value: 'contain', label: 'Contain' },
                { value: 'fill', label: 'Fill' },
              ]}
            />
          </div>
          <div>
            <div className="flex items-center justify-between">
              <label htmlFor="crop-x" className="text-xs text-fg-2">
                Crop position (horizontal)
              </label>
              <span className="font-mono text-xs text-fg">{clip.cropPosition?.x ?? 50}%</span>
            </div>
            <CommitRange
              id="crop-x"
              min={0}
              max={100}
              value={clip.cropPosition?.x ?? 50}
              onCommit={(x) => onPatchClip(clip.id, { cropPosition: { x, y: clip.cropPosition?.y ?? 50 } }, 'Adjust crop')}
            />
          </div>
          <div className="flex items-center justify-between gap-2">
            <span className="text-xs text-fg-2">Speed</span>
            <Unavailable>
              <select aria-label="Clip speed" className="h-7 px-2 rounded-md bg-app border border-line text-xs">
                <option>1.0×</option>
              </select>
            </Unavailable>
          </div>
        </Group>
      )}

      {track.type === 'broll' && (
        <Group title="B-roll overlay">
          <Row label="Media ID">{clip.assetId}</Row>
          <Row label="Overlay">{`${((clip.durationFrames) / fps).toFixed(1)} s on V2`}</Row>
          <Switch
            label="Track visible"
            description="Hides the whole V2 B-roll track in the preview."
            checked={!track.hidden}
            onChange={(v) => onPatchTrack(track.id, { hidden: !v }, v ? 'Show B-roll track' : 'Hide B-roll track')}
          />
        </Group>
      )}

      {(track.type === 'dialogue' || track.type === 'music') && (
        <Group title="Clip level">
          <VolumeSlider
            id="clip-vol"
            label="Clip volume"
            value={clip.volumePercent ?? 100}
            onChange={(v) => onPatchClip(clip.id, { volumePercent: v }, 'Clip volume')}
          />
        </Group>
      )}
    </div>
  );
};

// ------------------------------------------------------------------ Captions

const CaptionSection: React.FC<{ revision: DemoRevision }> = ({ revision }) => {
  const [ui, store] = useEditorStore();
  const s = ui.captionStyle;
  const capTrack = revision.tracks.find((t) => t.type === 'captions');
  const cue = capTrack?.clips.find(
    (c) => ui.playheadFrames >= c.timelineInFrames && ui.playheadFrames < c.timelineInFrames + c.durationFrames
  );
  return (
    <div>
      <Group title="Template">
        <div className="flex flex-col gap-1.5" role="radiogroup" aria-label="Caption template">
          {CAPTION_PRESETS.map((p) => (
            <button
              key={p.id}
              role="radio"
              aria-checked={s.presetId === p.id}
              onClick={() => store.setCaptionPreset(p.id)}
              className={cx(
                'text-left p-2.5 rounded-lg border transition-colors',
                s.presetId === p.id ? 'border-accent bg-accent/10' : 'border-line bg-surface hover:border-fg-3'
              )}
            >
              <span className="text-[13px] font-medium text-fg">{p.name}</span>
              <span className="block text-[11px] text-fg-2 mt-0.5 leading-snug">{p.description}</span>
            </button>
          ))}
        </div>
      </Group>
      <Group title="Style">
        <div>
          <div className="flex items-center justify-between">
            <label htmlFor="cap-size" className="text-xs text-fg-2">
              Font size
            </label>
            <span className="font-mono text-xs">{s.fontSize}px</span>
          </div>
          <input
            id="cap-size"
            type="range"
            min={14}
            max={40}
            value={s.fontSize}
            onChange={(e) => store.patchCaptionStyle({ fontSize: Number(e.target.value) })}
            className="w-full accent-accent"
          />
        </div>
        <div className="flex items-center justify-between gap-2">
          <span className="text-xs text-fg-2">Position</span>
          <Segmented
            size="sm"
            label="Caption position"
            value={s.position}
            onChange={(v) => store.patchCaptionStyle({ position: v })}
            options={[
              { value: 'top', label: 'Top' },
              { value: 'middle', label: 'Middle' },
              { value: 'bottom', label: 'Bottom' },
            ]}
          />
        </div>
        <div className="flex items-center justify-between gap-2">
          <label htmlFor="cap-color" className="text-xs text-fg-2">
            Text colour
          </label>
          <div className="flex items-center gap-2">
            <span className="font-mono text-xs text-fg-3">{s.color.toUpperCase()}</span>
            <input
              id="cap-color"
              type="color"
              value={s.color}
              onChange={(e) => store.patchCaptionStyle({ color: e.target.value })}
              className="w-8 h-7 rounded border border-line bg-app"
            />
          </div>
        </div>
        <Switch label="Uppercase" checked={s.uppercase} onChange={(v) => store.patchCaptionStyle({ uppercase: v })} />
        <Switch label="Background box" checked={s.background} onChange={(v) => store.patchCaptionStyle({ background: v })} />
      </Group>
      <Group title="Current cue">
        <p className="text-xs text-fg-2">{cue ? `“${cue.label}”` : 'No caption cue at the playhead.'}</p>
      </Group>
      <Group title="Safe margins">
        <p className="text-xs text-fg-2 leading-relaxed">
          Keep captions above the bottom ~20% of a 9:16 frame where platform UI (caption, audio name, buttons) sits.
          Toggle <strong className="text-fg">Safe zone</strong> on the preview to check placement.
        </p>
        {s.position === 'bottom' && s.fontSize > 32 && (
          <Badge tone="warning">Large bottom captions may collide with platform UI</Badge>
        )}
      </Group>
    </div>
  );
};

// ------------------------------------------------------------------ Audio

/** Range input that previews locally while dragging and commits once on release (one undo step). */
export const CommitRange: React.FC<{
  id: string;
  value: number;
  min: number;
  max: number;
  onCommit: (v: number) => void;
  onDraft?: (v: number) => void;
  disabled?: boolean;
}> = ({ id, value, min, max, onCommit, onDraft, disabled }) => {
  const [draft, setDraft] = React.useState(value);
  React.useEffect(() => setDraft(value), [value]);
  const commit = () => {
    if (draft !== value) onCommit(draft);
  };
  return (
    <input
      id={id}
      type="range"
      min={min}
      max={max}
      value={draft}
      disabled={disabled}
      onChange={(e) => {
        const v = Number(e.target.value);
        setDraft(v);
        onDraft?.(v);
      }}
      onPointerUp={commit}
      onKeyUp={commit}
      onBlur={commit}
      className="w-full accent-accent"
    />
  );
};

const VolumeSlider: React.FC<{ id: string; label: string; value: number; onChange: (v: number) => void; disabled?: boolean }> = ({
  id,
  label,
  value,
  onChange,
  disabled,
}) => {
  const [shown, setShown] = React.useState(value);
  React.useEffect(() => setShown(value), [value]);
  const db = shown <= 0 ? '−∞ dB' : `${(20 * Math.log10(shown / 100)).toFixed(1)} dB`;
  return (
    <div className={cx(disabled && 'opacity-50')}>
      <div className="flex items-center justify-between">
        <label htmlFor={id} className="text-xs text-fg-2">
          {label}
        </label>
        <span className="font-mono text-xs text-fg">
          {shown}% <span className="text-fg-3">({db})</span>
        </span>
      </div>
      <CommitRange id={id} min={0} max={100} value={value} disabled={disabled} onDraft={setShown} onCommit={onChange} />
    </div>
  );
};

const AudioSection: React.FC<Props> = ({ revision, onPatchTrack }) => {
  const [ui, store] = useEditorStore();
  const audioTracks = revision.tracks.filter((t) => t.type === 'dialogue' || t.type === 'music');
  return (
    <div>
      {audioTracks.map((t) => (
        <Group key={t.id} title={t.label}>
          <VolumeSlider
            id={`vol-${t.id}`}
            label="Track volume"
            value={t.volume ?? (t.type === 'music' ? 30 : 100)}
            onChange={(v) => onPatchTrack(t.id, { volume: v }, `${t.label} volume`)}
          />
          <div className="flex gap-2">
            <button
              type="button"
              aria-pressed={!!t.muted}
              onClick={() => onPatchTrack(t.id, { muted: !t.muted }, t.muted ? `Unmute ${t.label}` : `Mute ${t.label}`)}
              className={cx(
                'flex-1 h-7 rounded-md text-xs font-semibold border',
                t.muted ? 'bg-danger/15 text-danger border-danger/30' : 'border-line text-fg-2 hover:bg-elevated'
              )}
            >
              {t.muted ? 'Muted' : 'Mute'}
            </button>
            <button
              type="button"
              aria-pressed={ui.soloTrackId === t.id}
              onClick={() => store.toggleSolo(t.id)}
              className={cx(
                'flex-1 h-7 rounded-md text-xs font-semibold border',
                ui.soloTrackId === t.id ? 'bg-warning/15 text-warning border-warning/30' : 'border-line text-fg-2 hover:bg-elevated'
              )}
            >
              {ui.soloTrackId === t.id ? 'Soloed' : 'Solo'}
            </button>
          </div>
          <Row label="Fades">{t.type === 'music' ? 'in 0.5 s · out 1.0 s' : 'none'}</Row>
        </Group>
      ))}
      <Group title="Loudness target">
        <Row label="Integrated">≈ −16 LUFS</Row>
        <Row label="True peak">≤ −1 dBTP</Row>
        <p className="text-[11px] text-fg-3 leading-relaxed">
          Informational target only — not measured. Loudness analysis requires engine integration.
        </p>
      </Group>
    </div>
  );
};

// ------------------------------------------------------------------ Project

const ProjectSection: React.FC<Props> = ({ project, revision }) => {
  const fps = project.fpsNum / project.fpsDen;
  const ar = project.width > project.height ? '16:9' : project.width === project.height ? '1:1' : '9:16';
  return (
    <div>
      <Group title="Project">
        <p className="text-[13px] font-medium text-fg leading-snug">{project.name}</p>
      </Group>
      <Group title="Canvas">
        <Row label="Aspect ratio">{ar}</Row>
        <Row label="Resolution">{`${project.width} × ${project.height}`}</Row>
        <Row label="Frame rate">{`${fps} fps`}</Row>
        <Row label="Target duration">{`${(project.targetDurationFrames / fps).toFixed(1)} s (${project.targetDurationFrames} f)`}</Row>
      </Group>
      <Group title="Revision">
        <Row label="Current">{`v${revision.revisionNumber}`}</Row>
        <Row label="Timeline length">{framesToTimecode(revision.durationFrames, fps)}</Row>
        <Row label="Clips">{revision.tracks.reduce((n, t) => n + t.clips.length, 0)}</Row>
        {revision.changeSummary && <p className="text-xs text-fg-2 leading-relaxed">{revision.changeSummary}</p>}
      </Group>
      <Group title="Engine">
        <p className="text-xs text-fg-2 leading-relaxed">
          UI prototype — timeline edits change demo data only. No media is decoded, rendered or exported.
        </p>
      </Group>
    </div>
  );
};
