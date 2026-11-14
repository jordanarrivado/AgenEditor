import type { AspectRatio, DemoAsset } from '../types';

export interface WizardDetails {
  name: string;
  aspectRatio: AspectRatio | string;
  targetDurationSeconds: number;
  fps: number;
}

export type DetailErrors = Partial<Record<'name' | 'aspectRatio' | 'targetDurationSeconds' | 'fps', string>>;

export const DURATION_MIN = 15;
export const DURATION_MAX = 180;
export const ASPECT_RATIOS: AspectRatio[] = ['9:16', '16:9', '1:1'];

export function validateDetails(d: WizardDetails): DetailErrors {
  const e: DetailErrors = {};
  if (!d.name.trim()) e.name = 'Enter a project name.';
  else if (d.name.trim().length > 80) e.name = 'Keep the name under 80 characters.';
  if (!ASPECT_RATIOS.includes(d.aspectRatio as AspectRatio)) e.aspectRatio = 'Choose a supported aspect ratio.';
  if (!Number.isFinite(d.targetDurationSeconds) || !Number.isInteger(d.targetDurationSeconds))
    e.targetDurationSeconds = 'Enter a whole number of seconds.';
  else if (d.targetDurationSeconds < DURATION_MIN || d.targetDurationSeconds > DURATION_MAX)
    e.targetDurationSeconds = `Target must be between ${DURATION_MIN} and ${DURATION_MAX} seconds.`;
  if (![24, 30, 60].includes(d.fps)) e.fps = 'Choose 24, 30 or 60 fps.';
  return e;
}

export function validateAssets(selectedIds: string[], library: DemoAsset[]): string | undefined {
  const chosen = library.filter((a) => selectedIds.includes(a.id));
  if (!chosen.some((a) => a.kind === 'video')) return 'Select at least one primary video asset.';
  return undefined;
}
