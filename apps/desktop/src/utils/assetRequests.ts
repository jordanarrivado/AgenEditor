/**
 * Asset preparation hand-off (spec §3.1). The planner lists visuals the footage
 * lacks as `asset_requests`; the user generates them in an EXTERNAL tool and
 * imports the files. AgenEditor never calls a generator itself.
 */

export type AssetRequestKind = 'image' | 'video';
export type Generator = 'chatgpt' | 'gemini' | 'flow';

export interface AssetRequest {
  id: string;
  kind: AssetRequestKind;
  purpose: string;
  prompt: string;
  aspect: string;
}

export type AssetRequestStatus = 'pending' | 'ready' | 'dropped';

export const GENERATOR_LABEL: Record<Generator, string> = {
  chatgpt: 'ChatGPT',
  gemini: 'Gemini',
  flow: 'Google Flow',
};

/** Image → ChatGPT by default (Gemini if out of quota); video → Google Flow. */
export function generatorsFor(kind: AssetRequestKind): Generator[] {
  return kind === 'image' ? ['chatgpt', 'gemini'] : ['flow'];
}

export const ASSET_REQUEST_FORMAT = `{"asset_requests": [{"id": "gen_storyboard_01", "kind": "image", "purpose": "hook frame", "prompt": "...", "aspect": "9:16"}]}`;

const ID_RE = /^[a-z0-9][a-z0-9_-]{0,63}$/;

export type ParseResult = { ok: true; requests: AssetRequest[] } | { ok: false; error: string };

/**
 * Parses the planner's asset_requests block. Accepts the raw planner reply:
 * the first JSON object containing "asset_requests" is used.
 */
export function parseAssetRequests(text: string): ParseResult {
  const start = text.indexOf('{');
  const key = text.indexOf('"asset_requests"');
  if (key < 0 || start < 0) return { ok: false, error: 'No "asset_requests" block found in the pasted text.' };
  // Walk back from the key to its enclosing "{", then forward to the matching "}".
  const open = text.lastIndexOf('{', key);
  let depth = 0;
  let end = -1;
  let inStr = false;
  for (let i = open; i < text.length; i++) {
    const ch = text[i];
    if (inStr) {
      if (ch === '\\') i++;
      else if (ch === '"') inStr = false;
    } else if (ch === '"') inStr = true;
    else if (ch === '{') depth++;
    else if (ch === '}' && --depth === 0) {
      end = i;
      break;
    }
  }
  if (end < 0) return { ok: false, error: 'The asset_requests JSON is incomplete.' };
  let data: unknown;
  try {
    data = JSON.parse(text.slice(open, end + 1));
  } catch {
    return { ok: false, error: 'The asset_requests block is not valid JSON.' };
  }
  const list = (data as { asset_requests?: unknown }).asset_requests;
  if (!Array.isArray(list)) return { ok: false, error: '"asset_requests" must be a list.' };
  if (list.length > 100) return { ok: false, error: 'A plan may request at most 100 assets.' };

  const seen = new Set<string>();
  const requests: AssetRequest[] = [];
  for (const [i, raw] of list.entries()) {
    if (!raw || typeof raw !== 'object' || Array.isArray(raw)) return { ok: false, error: `Request ${i + 1}: expected an object.` };
    const r = raw as Partial<Record<keyof AssetRequest, unknown>>;
    const where = `Request ${i + 1}`;
    if (typeof r.id !== 'string' || !ID_RE.test(r.id)) return { ok: false, error: `${where}: id must be letters, digits, _ or -.` };
    if (seen.has(r.id)) return { ok: false, error: `${where}: duplicate id ${r.id}.` };
    if (r.kind !== 'image' && r.kind !== 'video') return { ok: false, error: `${where}: kind must be "image" or "video".` };
    if (typeof r.prompt !== 'string' || !r.prompt.trim()) return { ok: false, error: `${where}: prompt is empty.` };
    if (r.prompt.trim().length > 4000) return { ok: false, error: `${where}: prompt is too long.` };
    if (typeof r.purpose === 'string' && r.purpose.length > 500) return { ok: false, error: `${where}: purpose is too long.` };
    if (typeof r.aspect === 'string' && r.aspect.length > 32) return { ok: false, error: `${where}: aspect is too long.` };
    seen.add(r.id);
    requests.push({
      id: r.id,
      kind: r.kind,
      purpose: typeof r.purpose === 'string' ? r.purpose : '',
      prompt: r.prompt.trim(),
      aspect: typeof r.aspect === 'string' && r.aspect ? r.aspect : '9:16',
    });
  }
  return { ok: true, requests };
}

/** Text to paste into the external generator. Contains no transcript or local paths. */
export function buildGeneratorPrompt(req: AssetRequest, generator: Generator): string {
  const what = req.kind === 'image' ? 'Create one image' : 'Create one short video clip (no voice, no music)';
  return [
    `${what} for a ${req.aspect} vertical video edit.`,
    req.purpose ? `Purpose: ${req.purpose}.` : '',
    `Shot: ${req.prompt}`,
    `Aspect ratio: ${req.aspect}. No text, logos, watermarks or real people's likenesses.`,
    generator === 'flow' ? 'Keep it under 8 seconds.' : '',
    `Save the file as ${req.id} so it can be imported into AgenEditor.`,
  ]
    .filter(Boolean)
    .join('\n');
}

/** True when Execute may start: every request is ready or dropped. */
export function assetsReady(requests: AssetRequest[], status: Record<string, AssetRequestStatus>): boolean {
  return requests.every((r) => status[r.id] === 'ready' || status[r.id] === 'dropped');
}

/** One prompt covering every image request, numbered as storyboard frames. */
export function buildStoryboardPrompt(requests: AssetRequest[], generator: Generator): string {
  const frames = requests.filter((r) => r.kind === 'image');
  return [
    `Create a storyboard of ${frames.length} separate images for a vertical video edit (${GENERATOR_LABEL[generator]}).`,
    'Make one image per frame, in order, with a consistent visual style. No text, logos, watermarks or real people\'s likenesses.',
    ...frames.map((f, i) => `Frame ${i + 1} (${f.id}, ${f.aspect})${f.purpose ? ` - ${f.purpose}` : ''}: ${f.prompt}`),
    'Name each file by its id so it can be imported into AgenEditor.',
  ].join('\n');
}
