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
