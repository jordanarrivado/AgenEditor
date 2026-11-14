/**
 * Timecode and timeline geometry utilities for AgenEditor.
 * Uses integer frames for deterministic, drift-free calculations.
 */

export function framesToTimecode(frames: number, fps: number = 30, includeHours: boolean = false): string {
  const safeFrames = Math.max(0, Math.floor(frames));
  const totalSeconds = Math.floor(safeFrames / fps);
  const remainingFrames = safeFrames % fps;

  const hours = Math.floor(totalSeconds / 3600);
  const minutes = Math.floor((totalSeconds % 3600) / 60);
  const seconds = totalSeconds % 60;

  const pad = (n: number, width: number = 2) => n.toString().padStart(width, '0');

  if (includeHours || hours > 0) {
    return `${pad(hours)}:${pad(minutes)}:${pad(seconds)}:${pad(remainingFrames)}`;
  }
  return `${pad(minutes)}:${pad(seconds)}:${pad(remainingFrames)}`;
}

export function framesToClock(frames: number, fps: number = 30): string {
  const safeFrames = Math.max(0, Math.floor(frames));
  const totalSeconds = Math.floor(safeFrames / fps);
  const minutes = Math.floor(totalSeconds / 60);
  const seconds = totalSeconds % 60;
  const pad = (n: number) => n.toString().padStart(2, '0');
  return `${pad(minutes)}:${pad(seconds)}`;
}

export function framesToSeconds(frames: number, fps: number = 30): number {
  return frames / fps;
}

export function secondsToFrames(seconds: number, fps: number = 30): number {
  return Math.round(seconds * fps);
}

export function framesToPixels(frames: number, fps: number = 30, pixelsPerSecond: number = 50): number {
  return (frames / fps) * pixelsPerSecond;
}

export function pixelsToFrames(pixels: number, fps: number = 30, pixelsPerSecond: number = 50): number {
  return Math.round((pixels / pixelsPerSecond) * fps);
}

export function formatBytes(bytes: number): string {
  if (bytes === 0) return '0 B';
  const k = 1024;
  const sizes = ['B', 'KB', 'MB', 'GB'];
  const i = Math.floor(Math.log(bytes) / Math.log(k));
  return `${parseFloat((bytes / Math.pow(k, i)).toFixed(1))} ${sizes[i]}`;
}
