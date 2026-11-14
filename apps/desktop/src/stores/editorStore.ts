/**
 * Transient editor UI state (spec §9 "State boundaries"): playhead, zoom,
 * selection, panels, caption preview style. NOT project data — revisions live
 * in the repository. Not persisted.
 */
import { useSyncExternalStore } from 'react';

export type LeftTab = 'media' | 'transcript' | 'brief' | 'agent';
export type InspectorTab = 'clip' | 'captions' | 'audio' | 'project';
export type NarrowPane = 'preview' | 'timeline' | 'inspector' | 'dock';

export interface CaptionStyle {
  presetId: string;
  fontSize: number;
  position: 'top' | 'middle' | 'bottom';
  color: string;
  uppercase: boolean;
  background: boolean;
}

export interface EditorUiState {
  playheadFrames: number;
  maxFrames: number;
  fps: number;
  isPlaying: boolean;
  pixelsPerSecond: number;
  snappingEnabled: boolean;
  selectedClipId: string | null;
  selectedTrackId: string | null;
  activeLeftTab: LeftTab;
  activeInspectorTab: InspectorTab;
  leftDockOpen: boolean;
  inspectorOpen: boolean;
  narrowPane: NarrowPane;
  timelineHeight: number;
  safeZonesVisible: boolean;
  gridGuidesVisible: boolean;
  soloTrackId: string | null;
  highlightedUtteranceId: string | null;
  captionStyle: CaptionStyle;
}

export const ZOOM_MIN = 10;
export const ZOOM_MAX = 200;
export const ZOOM_STEP = 15;
export const TIMELINE_MIN_H = 180;
export const TIMELINE_MAX_H = 440;

export const CAPTION_PRESET_STYLES: Record<string, Omit<CaptionStyle, 'presetId'>> = {
  preset_bold_yellow: { fontSize: 28, position: 'bottom', color: '#FFE600', uppercase: true, background: false },
  preset_clean_white: { fontSize: 24, position: 'bottom', color: '#FFFFFF', uppercase: false, background: true },
  preset_boxed_dark: { fontSize: 22, position: 'middle', color: '#F3F6FC', uppercase: true, background: true },
};

export function captionStyleFromPreset(presetId: string): CaptionStyle {
  const base = CAPTION_PRESET_STYLES[presetId] ?? CAPTION_PRESET_STYLES.preset_bold_yellow;
  return { presetId: CAPTION_PRESET_STYLES[presetId] ? presetId : 'preset_bold_yellow', ...base };
}

export function initialEditorState(overrides: Partial<EditorUiState> = {}): EditorUiState {
  return {
    playheadFrames: 0,
    maxFrames: 1200,
    fps: 30,
    isPlaying: false,
    pixelsPerSecond: 40,
    snappingEnabled: true,
    selectedClipId: null,
    selectedTrackId: null,
    activeLeftTab: 'media',
    activeInspectorTab: 'project',
    leftDockOpen: true,
    inspectorOpen: true,
    narrowPane: 'preview',
    // 260px default (spec §6); shorter on low-height windows so the preview keeps room.
    timelineHeight: typeof window !== 'undefined' && window.innerHeight < 800 ? 220 : 260,
    safeZonesVisible: true,
    gridGuidesVisible: false,
    soloTrackId: null,
    highlightedUtteranceId: null,
    captionStyle: captionStyleFromPreset('preset_bold_yellow'),
    ...overrides,
  };
}

type Listener = () => void;

export class EditorStore {
  private state: EditorUiState = initialEditorState();
  private listeners = new Set<Listener>();
  private timer: number | null = null;

  getState = (): EditorUiState => this.state;

  subscribe = (l: Listener): (() => void) => {
    this.listeners.add(l);
    return () => {
      this.listeners.delete(l);
    };
  };

  setState(patch: Partial<EditorUiState>): void {
    this.state = { ...this.state, ...patch };
    this.listeners.forEach((l) => l());
  }

  /** Called when the editor opens a project/revision. */
  reset(overrides: Partial<EditorUiState> = {}): void {
    this.pause();
    const keep = {
      leftDockOpen: this.state.leftDockOpen,
      inspectorOpen: this.state.inspectorOpen,
      timelineHeight: this.state.timelineHeight,
    };
    this.state = initialEditorState({ ...keep, ...overrides });
    this.listeners.forEach((l) => l());
  }

  setTimelineBounds(maxFrames: number, fps: number): void {
    const max = Math.max(1, Math.round(maxFrames));
    this.setState({ maxFrames: max, fps, playheadFrames: Math.min(this.state.playheadFrames, max) });
  }

  // ---- Playhead / transport (simulated — no media playback)

  setPlayhead(frames: number): void {
    const clamped = Math.max(0, Math.min(this.state.maxFrames, Math.round(frames)));
    if (clamped !== this.state.playheadFrames) this.setState({ playheadFrames: clamped });
  }

  stepFrames(delta: number): void {
    this.setPlayhead(this.state.playheadFrames + delta);
  }

  togglePlay(): void {
    if (this.state.isPlaying) this.pause();
    else this.play();
  }

  play(): void {
    if (this.state.isPlaying) return;
    if (this.state.playheadFrames >= this.state.maxFrames) this.setPlayhead(0);
    this.setState({ isPlaying: true });
    const interval = 1000 / this.state.fps;
    this.timer = window.setInterval(() => {
      if (this.state.playheadFrames >= this.state.maxFrames) this.pause();
      else this.setPlayhead(this.state.playheadFrames + 1);
    }, interval);
  }

  pause(): void {
    if (this.timer !== null) {
      window.clearInterval(this.timer);
      this.timer = null;
    }
    if (this.state.isPlaying) this.setState({ isPlaying: false });
  }

  // ---- Zoom

  setZoom(pps: number): void {
    this.setState({ pixelsPerSecond: Math.max(ZOOM_MIN, Math.min(ZOOM_MAX, Math.round(pps))) });
  }
  zoomIn(): void {
    this.setZoom(this.state.pixelsPerSecond + ZOOM_STEP);
  }
  zoomOut(): void {
    this.setZoom(this.state.pixelsPerSecond - ZOOM_STEP);
  }
  /** Fit the whole timeline into `viewportWidth` pixels. */
  zoomFit(viewportWidth: number): void {
    const seconds = this.state.maxFrames / this.state.fps;
    this.setZoom(Math.floor((viewportWidth - 56) / Math.max(1, seconds)));
  }

  // ---- Selection & panels

  selectClip(clipId: string | null, trackId: string | null = null): void {
    this.setState({
      selectedClipId: clipId,
      selectedTrackId: clipId ? trackId : null,
      activeInspectorTab: clipId ? 'clip' : this.state.activeInspectorTab,
      inspectorOpen: clipId ? true : this.state.inspectorOpen,
    });
  }

  setActiveLeftTab(tab: LeftTab): void {
    this.setState({ activeLeftTab: tab, leftDockOpen: true });
  }
  setActiveInspectorTab(tab: InspectorTab): void {
    this.setState({ activeInspectorTab: tab });
  }
  toggleLeftDock(): void {
    this.setState({ leftDockOpen: !this.state.leftDockOpen });
  }
  toggleInspector(): void {
    this.setState({ inspectorOpen: !this.state.inspectorOpen });
  }
  setTimelineHeight(h: number): void {
    this.setState({ timelineHeight: Math.max(TIMELINE_MIN_H, Math.min(TIMELINE_MAX_H, Math.round(h))) });
  }
  toggleSafeZones(): void {
    this.setState({ safeZonesVisible: !this.state.safeZonesVisible });
  }
  toggleGridGuides(): void {
    this.setState({ gridGuidesVisible: !this.state.gridGuidesVisible });
  }
  toggleSnapping(): void {
    this.setState({ snappingEnabled: !this.state.snappingEnabled });
  }
  toggleSolo(trackId: string): void {
    this.setState({ soloTrackId: this.state.soloTrackId === trackId ? null : trackId });
  }
  setCaptionPreset(presetId: string): void {
    this.setState({ captionStyle: captionStyleFromPreset(presetId) });
  }
  patchCaptionStyle(patch: Partial<CaptionStyle>): void {
    this.setState({ captionStyle: { ...this.state.captionStyle, ...patch } });
  }
}

export const editorStore = new EditorStore();

/** Subscribe to the whole state (simple; the editor re-renders per frame while playing). */
export function useEditorStore(): [EditorUiState, EditorStore] {
  const state = useSyncExternalStore(editorStore.subscribe, editorStore.getState, editorStore.getState);
  return [state, editorStore];
}

/** Subscribe to a derived slice to avoid unnecessary re-renders. */
export function useEditorSelector<T>(selector: (s: EditorUiState) => T): T {
  return useSyncExternalStore(
    editorStore.subscribe,
    () => selector(editorStore.getState()),
    () => selector(editorStore.getState())
  );
}
