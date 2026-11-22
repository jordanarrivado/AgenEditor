/**
 * Checklist helpers (spec §3.1). The live checklist is stored in the project
 * database; localStorage helpers only migrate earlier WebView-only checklists.
 * A request is "ready" only while its linked asset remains registered.
 */
import { parseAssetRequests, type AssetRequest, type AssetRequestStatus } from './assetRequests';

export interface AssetChecklist {
  requests: AssetRequest[];
  /** request id → registered asset id, or 'dropped'. Absent = pending. */
  links: Record<string, string>;
  /** Known-issues log: request id → the linked asset the user accepted, and why. */
  accepted?: Record<string, { asset_id: string; note: string }>;
  /** When true a linked file is ready only after the person approved it (B-roll review). */
  review_required?: boolean;
  /** Request ids the person approved. */
  approved?: string[];
  /** The person's review note on an approved file (checked against the brief); agents read it. */
  review_notes?: Record<string, { asset_id: string; text: string }>;
}

/** Notes still valid for these links and approvals (a note belongs to the approved file it was written on). */
export function keptNotes(notes: AssetChecklist['review_notes'], links: Record<string, string>, approved: string[]): NonNullable<AssetChecklist['review_notes']> {
  return Object.fromEntries(
    Object.entries(notes ?? {}).filter(([id, note]) => approved.includes(id) && links[id] === note.asset_id),
  );
}

export const DROPPED = 'dropped';
const key = (projectId: string) => `ageneditor.assetChecklist.v1.${projectId}`;

export function loadChecklist(projectId: string, storage: Pick<Storage, 'getItem'> = localStorage): AssetChecklist | null {
  try {
    const raw = storage.getItem(key(projectId));
    if (!raw) return null;
    const data = JSON.parse(raw) as { requests?: unknown; links?: unknown };
    // Re-validate through the same parser used for pasted planner replies.
    const parsed = parseAssetRequests(JSON.stringify({ asset_requests: data.requests }));
    if (!parsed.ok) return null;
    const links: Record<string, string> = {};
    const ids = new Set(parsed.requests.map((r) => r.id));
    if (data.links && typeof data.links === 'object') {
      for (const [id, value] of Object.entries(data.links)) {
        if (ids.has(id) && typeof value === 'string') links[id] = value;
      }
    }
    return { requests: parsed.requests, links };
  } catch {
    return null;
  }
}

export function saveChecklist(projectId: string, list: AssetChecklist | null, storage: Pick<Storage, 'setItem' | 'removeItem'> = localStorage): void {
  try {
    if (list) storage.setItem(key(projectId), JSON.stringify(list));
    else storage.removeItem(key(projectId));
  } catch {
    /* storage unavailable: the checklist simply stays in memory */
  }
}

export function requestStatus(list: AssetChecklist, id: string, registeredAssetIds: ReadonlySet<string>): AssetRequestStatus {
  const link = list.links[id];
  if (link === DROPPED) return 'dropped';
  return link && registeredAssetIds.has(link) ? 'ready' : 'pending';
}

export function checklistReady(list: AssetChecklist, registeredAssetIds: ReadonlySet<string>): boolean {
  return list.requests.every((r) => requestStatus(list, r.id, registeredAssetIds) !== 'pending');
}

/** Suffix that marks a storyboard image made before its video (the Plan stage writes these). */
export const STORYBOARD_SUFFIX = '_storyboard';
export const isStoryboard = (requestId: string) => requestId.endsWith(STORYBOARD_SUFFIX);
export const videoOfStoryboard = (requestId: string) => requestId.slice(0, -STORYBOARD_SUFFIX.length);

export type ReviewState = 'waiting' | 'locked' | 'review' | 'approved' | 'skipped';

/**
 * Where a B-roll request is in its review: no file yet (waiting), a video whose storyboard
 * is not approved yet (locked), file added but not approved (review), approved, or skipped.
 */
export function reviewState(list: AssetChecklist, requestId: string): ReviewState {
  const link = list.links[requestId];
  if (link === DROPPED) return 'skipped';
  if (link) return !list.review_required || list.approved?.includes(requestId) ? 'approved' : 'review';
  const request = list.requests.find((item) => item.id === requestId);
  if (list.review_required && request?.kind === 'video') {
    const storyboard = `${requestId}${STORYBOARD_SUFFIX}`;
    if (list.requests.some((item) => item.id === storyboard) && reviewState(list, storyboard) !== 'approved' && reviewState(list, storyboard) !== 'skipped') {
      return 'locked';
    }
  }
  return 'waiting';
}

/** How many requests are done (approved or skipped) out of all of them. */
export function reviewProgress(list: AssetChecklist | null): { done: number; total: number; complete: boolean } {
  if (!list || list.requests.length === 0) return { done: 0, total: 0, complete: true };
  const done = list.requests.filter((request) => {
    const state = reviewState(list, request.id);
    return state === 'approved' || state === 'skipped';
  }).length;
  return { done, total: list.requests.length, complete: done === list.requests.length };
}

/**
 * Applies a new plan's requests to the saved list. A request keeps its file and approval only
 * when its id, kind, prompt and (for voice lines) words are unchanged; otherwise the person must make and
 * approve it again.
 */
export function mergePlanRequests(previous: AssetChecklist | null, requests: AssetChecklist['requests']): AssetChecklist {
  const links: Record<string, string> = {};
  const approved: string[] = [];
  for (const request of requests) {
    const before = previous?.requests.find((item) => item.id === request.id);
    const link = previous?.links[request.id];
    if (!before || !link || before.kind !== request.kind || before.prompt !== request.prompt || (before.line ?? null) !== (request.line ?? null) || (before.variant_id ?? null) !== (request.variant_id ?? null)) continue;
    links[request.id] = link;
    if (previous?.approved?.includes(request.id)) approved.push(request.id);
  }
  // A video made from a storyboard that is no longer approved (or skipped) must be made again.
  for (const request of requests) {
    const storyboard = `${request.id}${STORYBOARD_SUFFIX}`;
    if (request.kind !== 'video' || !requests.some((item) => item.id === storyboard) || !links[request.id] || links[request.id] === DROPPED) continue;
    if (links[storyboard] !== DROPPED && !approved.includes(storyboard)) {
      delete links[request.id];
      const at = approved.indexOf(request.id);
      if (at >= 0) approved.splice(at, 1);
    }
  }
  return { requests, links, approved, review_required: true, accepted: {}, review_notes: keptNotes(previous?.review_notes, links, approved) };
}
