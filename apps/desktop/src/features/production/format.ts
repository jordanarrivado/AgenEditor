/** Display helpers for the Production workspace. Domain values stay integers; these only format them. */

import type { ClipStatus, CostKind, TaskStatus } from '../../services/productionRepository';

export type Tone = 'neutral' | 'accent' | 'success' | 'warning' | 'danger';

export const CLIP_STATUS: Record<ClipStatus, { label: string; tone: Tone }> = {
  discovered: { label: 'Discovered', tone: 'neutral' },
  planned: { label: 'Planned', tone: 'neutral' },
  waiting_for_assets: { label: 'Waiting for assets', tone: 'warning' },
  ready: { label: 'Ready', tone: 'neutral' },
  editing: { label: 'Editing', tone: 'accent' },
  preview_rendering: { label: 'Rendering preview', tone: 'accent' },
  technical_qa: { label: 'Technical QA', tone: 'accent' },
  editorial_qa: { label: 'Editorial QA', tone: 'accent' },
  needs_revision: { label: 'Needs revision', tone: 'warning' },
  review_ready: { label: 'Ready for your review', tone: 'success' },
  human_approved: { label: 'Approved', tone: 'success' },
  exported: { label: 'Exported', tone: 'success' },
  delivered: { label: 'Delivered', tone: 'success' },
  blocked: { label: 'Blocked', tone: 'danger' },
  retry_wait: { label: 'Retry wait', tone: 'warning' },
  needs_human: { label: 'Needs a person', tone: 'danger' },
  failed: { label: 'Failed', tone: 'danger' },
  cancelled: { label: 'Cancelled', tone: 'neutral' },
};

export const TASK_STATUS: Record<TaskStatus, { label: string; tone: Tone }> = {
  created: { label: 'Created', tone: 'neutral' },
  blocked: { label: 'Blocked', tone: 'warning' },
  ready: { label: 'Ready', tone: 'neutral' },
  leased: { label: 'Leased', tone: 'accent' },
  running: { label: 'Running', tone: 'accent' },
  succeeded: { label: 'Succeeded', tone: 'success' },
  retry_wait: { label: 'Retry wait', tone: 'warning' },
  needs_human: { label: 'Needs a person', tone: 'danger' },
  failed: { label: 'Failed', tone: 'danger' },
  cancelled: { label: 'Cancelled', tone: 'neutral' },
};

/** Clip states a person should look at first (spec §16 observability rules). */
export const ATTENTION: ClipStatus[] = ['needs_human', 'failed', 'blocked', 'waiting_for_assets', 'retry_wait'];
/** Clips that a person may approve or package. */
export const REVIEWABLE: ClipStatus[] = ['review_ready', 'needs_human', 'needs_revision'];
export const DELIVERABLE: ClipStatus[] = ['human_approved', 'exported'];

export const COST_KIND: Record<CostKind, string> = {
  measured: 'Measured',
  provider_reported: 'Reported by provider',
  estimated: 'Estimated',
  unknown: 'Unknown',
};

export const taskTypeLabel = (type: string) => type.replace(/_/g, ' ');

export const seconds = (us: number) => `${(us / 1_000_000).toFixed(1)} s`;

export const usd = (micros: number) => `$${(micros / 1_000_000).toFixed(micros > 0 && micros < 10_000 ? 4 : 2)}`;

export function bytes(value: number | null): string {
  if (value === null) return 'not measured';
  const units = ['B', 'KB', 'MB', 'GB', 'TB'];
  let size = value;
  let unit = 0;
  while (size >= 1024 && unit < units.length - 1) {
    size /= 1024;
    unit += 1;
  }
  return `${size.toFixed(unit === 0 ? 0 : 1)} ${units[unit]}`;
}

export function clock(iso: string | null): string {
  if (!iso) return '—';
  const date = new Date(iso);
  return Number.isNaN(date.getTime()) ? '—' : new Intl.DateTimeFormat(undefined, { dateStyle: 'short', timeStyle: 'medium' }).format(date);
}

/** Real elapsed time between a recorded timestamp and now. Never a percentage. */
export function elapsed(iso: string, now: number = Date.now()): string {
  const start = new Date(iso).getTime();
  if (Number.isNaN(start)) return '—';
  const total = Math.max(0, Math.round((now - start) / 1000));
  const h = Math.floor(total / 3600);
  const m = Math.floor((total % 3600) / 60);
  const s = total % 60;
  return h ? `${h}h ${m}m` : m ? `${m}m ${s}s` : `${s}s`;
}

/** A measured duration in milliseconds, for display. */
export function durationMs(ms: number | null): string {
  if (ms === null) return '—';
  if (ms < 1000) return `${ms} ms`;
  const total = Math.round(ms / 1000);
  const h = Math.floor(total / 3600);
  const m = Math.floor((total % 3600) / 60);
  const s = total % 60;
  return h ? `${h}h ${m}m` : m ? `${m}m ${s}s` : `${s}s`;
}

const NAME_KEY ='ageneditor.production.reviewer';

/** The person's name for approvals, remembered on this computer only. */
export function rememberedName(): string {
  try {
    return window.localStorage.getItem(NAME_KEY) ?? '';
  } catch {
    return '';
  }
}

export function rememberName(name: string): void {
  try {
    window.localStorage.setItem(NAME_KEY, name);
  } catch {
    /* storage unavailable; the name still works for this session */
  }
}
