export type ProjectStatus = 'draft' | 'review' | 'approved';
export type MediaKind = 'video' | 'audio' | 'image' | 'subtitle';
export type TrackKind = 'video' | 'broll' | 'dialogue' | 'music' | 'captions';
export type JobStatus = 'queued' | 'running' | 'completed' | 'failed' | 'canceled';
export type AspectRatio = '9:16' | '16:9' | '1:1';
export type PlatformTarget = 'instagram' | 'tiktok' | 'youtube' | 'custom';

export interface BriefOptions {
  strongHook: boolean;
  cleanCaptions: boolean;
  useBroll: boolean;
  balanceMusic: boolean;
}

export const DEFAULT_BRIEF_OPTIONS: BriefOptions = {
  strongHook: true,
  cleanCaptions: true,
  useBroll: true,
  balanceMusic: true,
};

export interface DemoProject {
  id: string;
  name: string;
  description: string;
  status: ProjectStatus;
  platform: PlatformTarget;
  targetDurationFrames: number; // 1200 frames = 40s at 30 fps
  fpsNum: number;
  fpsDen: number;
  width: number;
  height: number;
  brief: string;
  briefOptions?: BriefOptions;
  stylePresetId?: string;
  assetIds: string[];
  activeRevisionId: string | null;
  updatedAtIso: string;
}

export interface DemoAsset {
  id: string;
  projectId: string;
  kind: MediaKind;
  name: string;
  filename: string;
  durationFrames?: number;
  frameRateNum?: number;
  frameRateDen?: number;
  width?: number;
  height?: number;
  fileSizeBytes: number;
  thumbnailColor?: string;
  description?: string;
  isMock: true;
}

export interface DemoTimelineClip {
  id: string;
  trackId: string;
  assetId: string;
  timelineInFrames: number;
  durationFrames: number;
  sourceInFrames: number;
  sourceOutFrames: number;
  label?: string;
  rationale?: string;
  // Clip specific inspector controls
  cropPosition?: { x: number; y: number };
  fitMode?: 'contain' | 'cover' | 'fill';
  speed?: number;
  volumePercent?: number;
}

export interface DemoTimelineTrack {
  id: string;
  type: TrackKind;
  label: string;
  clips: DemoTimelineClip[];
  muted?: boolean;
  hidden?: boolean;
  volume?: number;
}

export interface DemoRevision {
  id: string;
  projectId: string;
  revisionNumber: number;
  createdAtIso: string;
  durationFrames: number;
  tracks: DemoTimelineTrack[];
  notes: string;
  changeSummary?: string;
}

export interface DemoJob {
  id: string;
  projectId: string;
  kind: 'analyze' | 'preview' | 'export' | 'qa';
  status: JobStatus;
  progressPercent: number;
  isSimulation: true;
  message: string;
  errorDetail?: string;
  createdAtIso: string;
}

export interface DemoQAItem {
  id: string;
  title: string;
  category: 'dimensions' | 'audio' | 'captions' | 'visual';
  state: 'sample_pass' | 'sample_warning' | 'untested';
  detail: string;
}

export interface DemoTranscriptUtterance {
  id: string;
  speaker: string;
  text: string;
  startFrame: number;
  endFrame: number;
  startSeconds: number;
  endSeconds: number;
}

export interface DemoReviewNote {
  id: string;
  author: string;
  timecode: string;
  frame: number;
  text: string;
  createdAtIso: string;
  resolved?: boolean;
}

export interface CaptionPreset {
  id: string;
  name: string;
  description: string;
  fontFamily: string;
  fontSize: number;
  color: string;
  backgroundColor?: string;
  hasBackground: boolean;
  textTransform: 'uppercase' | 'none';
  positionY: 'bottom' | 'middle';
}

export interface EditingStylePreset {
  id: string;
  name: string;
  headline: string;
  pacing: string;
  bRollUsage: string;
  captionStyle: string;
  recommendedFormats: string[];
}

export interface NewProjectInput {
  name: string;
  description: string;
  platform: PlatformTarget;
  aspectRatio?: AspectRatio;
  targetDurationSeconds: number;
  fps: number;
  brief: string;
  briefOptions?: BriefOptions;
  stylePresetId?: string;
  selectedAssetIds: string[];
}

export type JobKind = DemoJob['kind'];
