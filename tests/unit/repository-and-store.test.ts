import { describe, expect, it } from 'vitest';
import { MockProjectRepository } from '../../apps/desktop/src/mocks/mockProjectRepository';
import { INITIAL_PROJECTS } from '../../apps/desktop/src/mocks/fixtures';
import { EditorStore } from '../../apps/desktop/src/stores/editorStore';

const memoryStorage = () => {
  const m = new Map<string, string>();
  return { getItem: (k: string) => m.get(k) ?? null, setItem: (k: string, v: string) => void m.set(k, v) };
};

describe('MockProjectRepository', () => {
  it('serves deterministic fixtures', async () => {
    const repo = new MockProjectRepository({ storage: null });
    const projects = await repo.listProjects();
    expect(projects.map((p) => p.id)).toEqual(INITIAL_PROJECTS.map((p) => p.id));
    expect((await repo.listRevisions('reel_001')).length).toBe(2);
    expect((await repo.getTranscript('reel_001')).some((u) => /friction/i.test(u.text))).toBe(true);
  });

  it('creates, duplicates and archives demo projects', async () => {
    const repo = new MockProjectRepository({ storage: null });
    const p = await repo.createProject({
      name: 'Test reel',
      description: '',
      platform: 'tiktok',
      aspectRatio: '9:16',
      targetDurationSeconds: 30,
      fps: 30,
      brief: 'b',
      selectedAssetIds: ['asset_001_speaker_main'],
    });
    expect(p.targetDurationFrames).toBe(900);
    expect(p.activeRevisionId).toBeNull();
    const dup = await repo.duplicateProject('reel_001');
    expect(dup.name).toMatch(/\(Copy\)$/);
    expect((await repo.listRevisions(dup.id)).length).toBe(2);
    await repo.archiveProject(p.id);
    expect((await repo.listProjects()).some((x) => x.id === p.id)).toBe(false);
  });

  it('creates a sample 5-track revision for projects without one', async () => {
    const repo = new MockProjectRepository({ storage: null });
    const rev = await repo.createSampleRevision('reel_002');
    expect(rev.tracks.map((t) => t.type)).toEqual(['broll', 'video', 'dialogue', 'music', 'captions']);
    expect(rev.durationFrames).toBe(1050);
    expect((await repo.getProject('reel_002'))!.activeRevisionId).toBe(rev.id);
  });

  it('only ever creates simulation jobs', async () => {
    const repo = new MockProjectRepository({ storage: null });
    const job = await repo.createSimulationJob('reel_001', 'export');
    expect(job.isSimulation).toBe(true);
    expect(job.status).toBe('queued');
    expect(job.message).toMatch(/Simulation/);
  });

  it('persists to storage and Reset Demo Data restores fixtures', async () => {
    const storage = memoryStorage();
    const a = new MockProjectRepository({ storage });
    await a.updateProject('reel_001', { name: 'Renamed' });
    const b = new MockProjectRepository({ storage });
    expect((await b.getProject('reel_001'))!.name).toBe('Renamed');
    await b.resetDemoData();
    expect((await b.getProject('reel_001'))!.name).toBe(INITIAL_PROJECTS[0].name);
    expect(new MockProjectRepository({ storage }).persistsLocally).toBe(true);
  });
});

describe('EditorStore', () => {
  it('clamps the playhead to the revision and zoom to bounds', () => {
    const s = new EditorStore();
    s.setTimelineBounds(1200, 30);
    s.setPlayhead(5000);
    expect(s.getState().playheadFrames).toBe(1200);
    s.setPlayhead(-10);
    expect(s.getState().playheadFrames).toBe(0);
    s.stepFrames(1);
    expect(s.getState().playheadFrames).toBe(1);
    s.setZoom(9999);
    expect(s.getState().pixelsPerSecond).toBe(200);
    s.zoomFit(1256);
    expect(s.getState().pixelsPerSecond).toBe(30);
  });

  it('selecting a clip opens the clip inspector', () => {
    const s = new EditorStore();
    s.setActiveInspectorTab('audio');
    s.selectClip('c1', 't1');
    expect(s.getState()).toMatchObject({ selectedClipId: 'c1', selectedTrackId: 't1', activeInspectorTab: 'clip' });
    s.selectClip(null);
    expect(s.getState().selectedClipId).toBeNull();
  });
});
