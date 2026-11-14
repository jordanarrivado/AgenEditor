import { describe, expect, it } from 'vitest';
import { framesToTimecode, framesToClock, formatBytes } from '../../apps/desktop/src/utils/timecode';
import {
  duplicateClip,
  findClip,
  frameToPx,
  pxToFrame,
  removeClip,
  rulerStepSeconds,
  setClipDuration,
  setClipStart,
  snapFrame,
} from '../../apps/desktop/src/utils/timelineGeometry';
import { validateAssets, validateDetails } from '../../apps/desktop/src/utils/validation';
import { diffRevisions } from '../../apps/desktop/src/utils/revisionDiff';
import { buildAgentBrief, DEMO_DISCLAIMER } from '../../apps/desktop/src/utils/agentBrief';
import { buildRolePrompt, claudeCodeCommand, DEFAULT_ROLE_PROFILE } from '../../apps/desktop/src/utils/agentRoles';
import {
  assetsReady,
  buildGeneratorPrompt,
  buildStoryboardPrompt,
  generatorsFor,
  parseAssetRequests,
} from '../../apps/desktop/src/utils/assetRequests';
import { INITIAL_ASSETS, INITIAL_PROJECTS, INITIAL_REVISIONS } from '../../apps/desktop/src/mocks/fixtures';

const [v1, v2] = INITIAL_REVISIONS.reel_001;

describe('timecode', () => {
  it('formats integer frames as MM:SS:FF', () => {
    expect(framesToTimecode(0, 30)).toBe('00:00:00');
    expect(framesToTimecode(376, 30)).toBe('00:12:16');
    expect(framesToTimecode(1200, 30)).toBe('00:40:00');
    expect(framesToClock(1200, 30)).toBe('00:40');
    expect(formatBytes(1024)).toBe('1 KB');
  });
});

describe('timeline geometry', () => {
  it('converts frames ↔ pixels deterministically', () => {
    expect(frameToPx(30, 30, 50)).toBe(50);
    expect(frameToPx(1200, 30, 50)).toBe(2000);
    expect(pxToFrame(2000, 30, 50)).toBe(1200);
  });

  it('snaps to nearby clip boundaries only within threshold', () => {
    const edge = v2.tracks[1].clips[1].timelineInFrames;
    expect(snapFrame(v2, edge + 3, 5)).toBe(edge);
    expect(snapFrame(v2, edge + 3, 1)).toBe(edge + 3);
  });

  it('picks readable ruler steps', () => {
    expect(rulerStepSeconds(50).major).toBe(2);
    expect(rulerStepSeconds(10).major).toBe(10);
  });

  it('removes and duplicates clips immutably', () => {
    const clipId = v2.tracks[0].clips[0].id;
    const removed = removeClip(v2, clipId);
    expect(findClip(removed, clipId)).toBeNull();
    expect(findClip(v2, clipId)).not.toBeNull(); // original untouched

    const dup = duplicateClip(v2, clipId)!;
    const src = findClip(v2, clipId)!.clip;
    const copy = findClip(dup.revision, dup.newClipId)!.clip;
    expect(copy.timelineInFrames).toBe(src.timelineInFrames + src.durationFrames);
    expect(dup.revision.tracks[0].clips).toHaveLength(v2.tracks[0].clips.length + 1);
  });

  it('clamps duration and start to the timeline', () => {
    const clip = v2.tracks[1].clips[0];
    const longer = setClipDuration(v2, clip.id, 99999);
    expect(findClip(longer, clip.id)!.clip.durationFrames).toBe(v2.durationFrames - clip.timelineInFrames);
    const moved = setClipStart(v2, clip.id, -50);
    expect(findClip(moved, clip.id)!.clip.timelineInFrames).toBe(0);
  });
});

describe('validation', () => {
  it('requires a non-blank name and 15–180 s duration', () => {
    expect(validateDetails({ name: '  ', aspectRatio: '9:16', targetDurationSeconds: 40, fps: 30 }).name).toBeTruthy();
    expect(validateDetails({ name: 'A', aspectRatio: '9:16', targetDurationSeconds: 10, fps: 30 }).targetDurationSeconds).toBeTruthy();
    expect(validateDetails({ name: 'A', aspectRatio: '4:3', targetDurationSeconds: 40, fps: 30 }).aspectRatio).toBeTruthy();
    expect(validateDetails({ name: 'A', aspectRatio: '9:16', targetDurationSeconds: 40, fps: 30 })).toEqual({});
  });

  it('requires at least one primary video asset', () => {
    expect(validateAssets(['asset_005_music_ambient_pulse'], INITIAL_ASSETS)).toMatch(/video/);
    expect(validateAssets(['asset_001_speaker_main'], INITIAL_ASSETS)).toBeUndefined();
  });
});

describe('revision diff', () => {
  it('reports clips added between v1 and v2', () => {
    const d = diffRevisions(v1, v2);
    expect(d.added.length).toBeGreaterThan(0);
    expect(d.added.some((a) => a.track.includes('B-Roll') || a.track.includes('B-roll'))).toBe(true);
  });
  it('reports no changes against itself', () => {
    const d = diffRevisions(v2, v2);
    expect(d.added.length + d.removed.length + d.changed.length).toBe(0);
  });
});

describe('agent brief', () => {
  it('is labelled Demo Mode and lists source assets by id', () => {
    const project = INITIAL_PROJECTS[0];
    const assets = INITIAL_ASSETS.filter((a) => project.assetIds.includes(a.id));
    const text = buildAgentBrief({ project, assets, revision: v2, client: 'claude-code' });
    expect(text.startsWith(DEMO_DISCLAIMER)).toBe(true);
    expect(text).toContain('not connected');
    expect(text).toContain('asset_001_speaker_main');
    expect(text).toContain('Do not generate video');
    expect(text).toContain('Claude Code');
  });
});

describe('agent role prompts', () => {
  it('defaults to Opus plan, Sonnet execute, Opus review at medium effort', () => {
    expect(DEFAULT_ROLE_PROFILE.planner).toEqual({ model: 'claude-opus-5-5', effort: 'medium' });
    expect(DEFAULT_ROLE_PROFILE.executor.model).toBe('claude-sonnet-5-5');
    expect(DEFAULT_ROLE_PROFILE.reviewer).toEqual({ model: 'claude-opus-5-5', effort: 'medium' });
    expect(claudeCodeCommand(DEFAULT_ROLE_PROFILE.executor)).toBe('claude --model claude-sonnet-5-5');
  });

  it('builds a demo-labelled, role-specific prompt on top of the brief', () => {
    const project = INITIAL_PROJECTS[0];
    const assets = INITIAL_ASSETS.filter((a) => project.assetIds.includes(a.id));
    for (const role of ['planner', 'executor', 'reviewer'] as const) {
      const text = buildRolePrompt({ role, profile: DEFAULT_ROLE_PROFILE, project, assets, revision: v2 });
      expect(text.startsWith(DEMO_DISCLAIMER)).toBe(true);
      expect(text).toContain(`ROLE: ${role.toUpperCase()}`);
      expect(text).toContain('Do not generate video');
    }
    expect(buildRolePrompt({ role: 'planner', profile: DEFAULT_ROLE_PROFILE, project, assets, revision: v2 })).toContain(
      'Claude Opus 5.5 (claude-opus-5-5), effort medium'
    );
  });
});

describe('asset requests (external generation hand-off)', () => {
  const reply = `Here is the plan.
\`\`\`json
{"asset_requests": [
  {"id": "gen_storyboard_01", "kind": "image", "purpose": "hook frame", "prompt": "A desk at sunrise {calm}", "aspect": "9:16"},
  {"id": "gen_broll_02", "kind": "video", "purpose": "cutaway", "prompt": "Slow pan over a city"}
]}
\`\`\``;

  it('parses the planner reply and defaults aspect', () => {
    const res = parseAssetRequests(reply);
    expect(res.ok).toBe(true);
    if (!res.ok) return;
    expect(res.requests.map((r) => r.id)).toEqual(['gen_storyboard_01', 'gen_broll_02']);
    expect(res.requests[1].aspect).toBe('9:16');
    expect(res.requests[0].prompt).toContain('{calm}');
  });

  it('rejects missing blocks, bad kinds and duplicate ids', () => {
    expect(parseAssetRequests('no json here').ok).toBe(false);
    expect(parseAssetRequests('{"asset_requests": [{"id": "a", "kind": "music", "prompt": "x"}]}').ok).toBe(false);
    expect(
      parseAssetRequests('{"asset_requests": [{"id": "a", "kind": "image", "prompt": "x"}, {"id": "a", "kind": "image", "prompt": "y"}]}').ok
    ).toBe(false);
  });

  it('routes images to ChatGPT then Gemini and video to Google Flow', () => {
    expect(generatorsFor('image')).toEqual(['chatgpt', 'gemini']);
    expect(generatorsFor('video')).toEqual(['flow']);
  });

  it('gates Execute until every request is ready or dropped', () => {
    const res = parseAssetRequests(reply);
    if (!res.ok) throw new Error(res.error);
    expect(assetsReady(res.requests, { gen_storyboard_01: 'ready', gen_broll_02: 'pending' })).toBe(false);
    expect(assetsReady(res.requests, { gen_storyboard_01: 'ready', gen_broll_02: 'dropped' })).toBe(true);
    expect(assetsReady([], {})).toBe(true);
  });

  it('builds generator and storyboard prompts without transcript text', () => {
    const res = parseAssetRequests(reply);
    if (!res.ok) throw new Error(res.error);
    const img = buildGeneratorPrompt(res.requests[0], 'chatgpt');
    expect(img).toContain('gen_storyboard_01');
    expect(buildStoryboardPrompt(res.requests, 'chatgpt')).toContain('storyboard of 1 separate images');
  });

  it('lists ready generated assets in the executor prompt and asks the planner for requests', () => {
    const res = parseAssetRequests(reply);
    if (!res.ok) throw new Error(res.error);
    const project = INITIAL_PROJECTS[0];
    const assets = INITIAL_ASSETS.filter((a) => project.assetIds.includes(a.id));
    const exec = buildRolePrompt({ role: 'executor', profile: DEFAULT_ROLE_PROFILE, project, assets, revision: v2, readyAssets: [res.requests[0]] });
    expect(exec).toContain('Step 3 of 4');
    expect(exec).toContain('gen_storyboard_01 [image]');
    expect(buildRolePrompt({ role: 'planner', profile: DEFAULT_ROLE_PROFILE, project, assets, revision: v2 })).toContain('"asset_requests"');
  });
});
