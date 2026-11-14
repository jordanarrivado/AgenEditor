import React from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { LiveWorkspace } from '../../apps/desktop/src/features/live/LiveWorkspace';

const engine = vi.hoisted(() => Object.fromEntries([
  'listProjects', 'summary', 'timeline', 'listJobs', 'transcripts', 'connection',
  'range', 'validate', 'commit', 'startRender', 'runQA', 'saveExport', 'importMedia',
  'getChecklist', 'saveChecklist', 'linkChecklistAsset', 'importDroppedMedia', 'onMediaDropped', 'acceptChecklistIssue', 'getBrief', 'saveBrief',
].map((name) => [name, vi.fn()])));

vi.mock('../../apps/desktop/src/services/liveEngineRepository', () => ({ liveEngineRepository: engine }));

const project = { id: 'project_test', name: 'Real project', current_revision: 1 };
const asset = { id: 'asset_test', role: 'primary', original_filename: 'footage.mp4', video: {
  width: 1920, height: 1080, duration_frames: 1800, frame_rate: { num: 30, den: 1 },
}, audio: null };
const timeline = { project_id: project.id, revision: 1, target_duration_frames: 1200,
  render_profile: { width: 1080, height: 1920, frame_rate: { num: 30, den: 1 } }, captions: [],
  clips: [{ clip_id: 'cut1', asset_id: asset.id, track: 'V1', source_in_frames: 0,
    source_out_frames: 1200, timeline_in_frames: 0, timeline_duration_frames: 1200, rationale: 'Strongest hook' }],
  provenance: { author: 'user', summary: 'Initial cut' },
};
let storedChecklist = { schema_version: 1, project_id: project.id, version: 0,
  configured: false, requests: [] as { id: string; kind: 'image' | 'video'; purpose: string; prompt: string; aspect: string }[],
  links: {} as Record<string, string>,
  accepted: {} as Record<string, { asset_id: string; note: string }>,
};

let storedBrief = { schema_version: 1, project_id: project.id, version: 0, brief_text: '', script_text: '',
  reference_note: '', checklist: [] as { id: string; text: string; status: 'open' | 'met' | 'waived'; note: string }[] };

let emitDrop: (drop: { count: number; x: number; y: number }) => void = () => undefined;

beforeEach(() => {
  vi.resetAllMocks();
  engine.onMediaDropped.mockImplementation(async (handler) => { emitDrop = handler; return () => undefined; });
  storedChecklist = { schema_version: 1, project_id: project.id, version: 0,
    configured: false, requests: [], links: {}, accepted: {} };
  storedBrief = { schema_version: 1, project_id: project.id, version: 0, brief_text: '', script_text: '',
    reference_note: '', checklist: [] };
  engine.getBrief.mockImplementation(async () => storedBrief);
  engine.saveBrief.mockImplementation(async (_projectId, next, expectedVersion) => {
    if (expectedVersion !== storedBrief.version) throw new Error('REVISION_CONFLICT: brief changed');
    storedBrief = { ...storedBrief, ...next, version: storedBrief.version + 1 };
    return storedBrief;
  });
  engine.listProjects.mockResolvedValue([project]);
  engine.summary.mockResolvedValue({ ...project, assets: [asset] });
  engine.timeline.mockResolvedValue(timeline);
  engine.listJobs.mockResolvedValue([]);
  engine.transcripts.mockResolvedValue([]);
  engine.connection.mockResolvedValue({ connected: true, command: 'engine mcp --shared-service' });
  engine.range.mockResolvedValue({ output_frames: 1170 });
  engine.validate.mockResolvedValue({ valid: true, issues: [] });
  engine.getChecklist.mockImplementation(async () => storedChecklist);
  engine.saveChecklist.mockImplementation(async (_projectId, next, expectedVersion) => {
    if (expectedVersion !== storedChecklist.version) throw new Error('REVISION_CONFLICT');
    storedChecklist = { ...storedChecklist, version: storedChecklist.version + 1,
      configured: next !== null, requests: next?.requests ?? [], links: next?.links ?? {}, accepted: next?.accepted ?? {} };
    return storedChecklist;
  });
  engine.linkChecklistAsset.mockImplementation(async (_projectId, requestId, assetId, expectedVersion) => {
    if (expectedVersion !== storedChecklist.version) throw new Error('REVISION_CONFLICT');
    storedChecklist = { ...storedChecklist, version: storedChecklist.version + 1,
      links: { ...storedChecklist.links, [requestId]: assetId } };
    return storedChecklist;
  });
  engine.acceptChecklistIssue.mockImplementation(async (_projectId, requestId, note, expectedVersion) => {
    if (expectedVersion !== storedChecklist.version) throw new Error('REVISION_CONFLICT');
    const accepted = { ...storedChecklist.accepted };
    if (note === null) delete accepted[requestId];
    else accepted[requestId] = { asset_id: storedChecklist.links[requestId], note };
    storedChecklist = { ...storedChecklist, version: storedChecklist.version + 1, accepted };
    return storedChecklist;
  });
});
afterEach(() => vi.unstubAllGlobals());

describe('live desktop revision and export safeguards', () => {
  it('shows the agent edit read-only and offers no manual editing controls', async () => {
    render(<LiveWorkspace />);
    expect(await screen.findByTestId('agent-edit')).toHaveTextContent('Edit made by your AI agent');
    const cuts = screen.getByRole('list', { name: 'Cuts' });
    expect(cuts).toHaveTextContent('footage.mp4');
    expect(cuts).toHaveTextContent(/source frames 0.1200/);
    expect(cuts).toHaveTextContent('Why: Strongest hook');
    // The only number field left is the new-project length, not a cut or overlay control.
    expect(screen.queryAllByRole('spinbutton').map((field) => field.closest('label')?.textContent)).toEqual(['Target seconds']);
    for (const name of [/Add cut/, /Add image/, /Validate and save revision/, /Discard draft/, /Move cut/, /Remove cut/, /Validate proposed cuts/]) {
      expect(screen.queryByRole('button', { name })).toBeNull();
    }
    expect(screen.queryByText(/Paste a structured agent edit plan/)).toBeNull();
    expect(engine.commit).not.toHaveBeenCalled();
  });

  it('shows a revision the agent commits through MCP without a manual refresh', async () => {
    render(<LiveWorkspace />);
    await screen.findByTestId('agent-edit');
    expect(screen.getByTestId('agent-edit')).toHaveTextContent('Revision 1');
    engine.timeline.mockResolvedValue({
      ...timeline, revision: 2,
      clips: [{ ...timeline.clips[0], source_out_frames: 600, timeline_duration_frames: 600, rationale: 'Tighter opening' }],
      provenance: { author: 'agent', summary: 'Tightened the hook' },
    });
    await waitFor(() => expect(screen.getByTestId('agent-edit')).toHaveTextContent('Revision 2'), { timeout: 8000 });
    expect(screen.getByTestId('agent-edit')).toHaveTextContent('Why: Tighter opening');
    expect(screen.getByTestId('agent-edit')).toHaveTextContent('Last change by agent: Tightened the hook');
    expect(screen.getByText('Your agent saved revision 2.')).toBeInTheDocument();
    expect(engine.commit).not.toHaveBeenCalled();
  }, 15000);

  it('requires QA for the specific completed final render before saving', async () => {
    const job = { id: 'job_first', kind: 'final_render', status: 'succeeded', timeline_revision: 1, progress: 1 };
    engine.listJobs.mockResolvedValue([job]);
    engine.runQA.mockResolvedValue({ overall: 'pass', checks: [] });
    render(<LiveWorkspace />);
    await screen.findByTestId('agent-edit');
    const save = screen.getByRole('button', { name: /Save verified MP4/ });
    expect(save).toBeDisabled();
    fireEvent.click(screen.getByRole('button', { name: 'Run technical QA' }));
    await waitFor(() => expect(save).toBeEnabled());
    engine.listJobs.mockResolvedValue([{ ...job, id: 'job_second', timeline_revision: 2 }, job]);
    await waitFor(() => expect(save).toBeDisabled(), { timeout: 2500 });
    expect(engine.saveExport).not.toHaveBeenCalled();
  });
});

describe('live asset request checklist', () => {
  const requests = JSON.stringify({ asset_requests: [
    { id: 'gen_road', kind: 'video', purpose: 'b-roll', prompt: 'a road at dusk', aspect: '9:16' },
    { id: 'gen_frame', kind: 'image', purpose: 'hook', prompt: 'a frame', aspect: '9:16' },
  ] });

  it('links an imported generated file to its request and persists per project', async () => {
    localStorage.clear();
    const generated = { id: 'asset_gen', kind: 'video', role: 'broll', original_filename: 'road.mp4', video: asset.video, audio: null,
      provenance: { origin: 'generated-external', tool_name: 'Google Flow' } };
    const generatedImage = { id: 'asset_image', kind: 'image', role: 'broll', original_filename: 'frame.png', video: asset.video, audio: null,
      provenance: { origin: 'generated-external', tool_name: 'ChatGPT' } };
    const imported = [asset];
    engine.importMedia.mockImplementation(async (_projectId, _role, toolName) => {
      const next = toolName === 'ChatGPT' ? generatedImage : generated;
      imported.push(next);
      engine.summary.mockResolvedValue({ ...project, assets: [...imported] });
      return next;
    });
    const { unmount } = render(<LiveWorkspace />);
    fireEvent.change(await screen.findByLabelText(/Paste the planner/), { target: { value: requests } });
    fireEvent.click(screen.getByRole('button', { name: 'Load requests' }));
    await waitFor(() => expect(screen.getByTestId('native-status-gen_road')).toHaveTextContent('pending'));
    expect(screen.getByTestId('native-checklist-gate')).toHaveTextContent('still pending');
    expect(screen.getByRole('button', { name: 'Render final MP4' })).toBeDisabled();
    expect(screen.getByRole('button', { name: /Import ChatGPT file for gen_frame/ })).toBeEnabled();

    fireEvent.click(screen.getByRole('button', { name: /Import Google Flow file for gen_road/ }));
    await waitFor(() => expect(screen.getByTestId('native-status-gen_road')).toHaveTextContent('ready'));
    expect(engine.importMedia).toHaveBeenCalledWith(project.id, 'broll', 'Google Flow');
    fireEvent.click(screen.getByRole('button', { name: /Import ChatGPT file for gen_frame/ }));
    await waitFor(() => expect(screen.getByTestId('native-status-gen_frame')).toHaveTextContent('ready'));
    expect(engine.importMedia).toHaveBeenCalledWith(project.id, 'broll', 'ChatGPT');
    await waitFor(() => expect(screen.getByTestId('native-checklist-gate')).toHaveTextContent('imported or dropped'));
    expect(screen.getByRole('button', { name: 'Render final MP4' })).toBeEnabled();

    fireEvent.click(screen.getAllByRole('button', { name: 'Reset' })[0]);
    await waitFor(() => expect(screen.getByTestId('native-status-gen_road')).toHaveTextContent('pending'));
    fireEvent.change(screen.getByLabelText('Link an existing imported asset'), { target: { value: generated.id } });
    fireEvent.click(screen.getByRole('button', { name: 'Link' }));
    await waitFor(() => expect(screen.getByTestId('native-status-gen_road')).toHaveTextContent('ready'));
    expect(engine.linkChecklistAsset).toHaveBeenCalledWith(project.id, 'gen_road', generated.id, expect.any(Number));

    fireEvent.click(screen.getAllByRole('button', { name: 'Reset' })[1]);
    await waitFor(() => expect(screen.getByTestId('native-status-gen_frame')).toHaveTextContent('pending'));
    fireEvent.click(screen.getByRole('button', { name: 'Drop' }));
    await waitFor(() => expect(screen.getByTestId('native-status-gen_frame')).toHaveTextContent('dropped'));

    unmount();
    render(<LiveWorkspace />);
    await waitFor(() => expect(screen.getByTestId('native-status-gen_road')).toHaveTextContent('ready'));
    expect(screen.getByTestId('native-status-gen_frame')).toHaveTextContent('dropped');
  });

  it('imports a file dropped on a pending request using the entered tool name', async () => {
    const generated = { id: 'asset_drop', kind: 'video', role: 'broll', original_filename: 'road.mp4', video: asset.video, audio: null,
      provenance: { origin: 'generated-external', tool_name: 'Google Flow' } };
    engine.importDroppedMedia.mockImplementation(async () => {
      engine.summary.mockResolvedValue({ ...project, assets: [asset, generated] });
      return generated;
    });
    const hit = vi.fn();
    Object.defineProperty(document, 'elementFromPoint', { configurable: true, value: hit });
    render(<LiveWorkspace />);
    fireEvent.change(await screen.findByLabelText(/Paste the planner/), { target: { value: requests } });
    fireEvent.click(screen.getByRole('button', { name: 'Load requests' }));
    await waitFor(() => expect(screen.getByTestId('native-status-gen_road')).toHaveTextContent('pending'));
    fireEvent.change(screen.getByLabelText('External tool'), { target: { value: 'Google Flow' } });

    hit.mockReturnValue(document.body); // not over a request card
    emitDrop({ count: 1, x: 5, y: 5 });
    hit.mockReturnValue(screen.getByTestId('native-request-gen_road'));
    emitDrop({ count: 2, x: 5, y: 5 }); // several files are refused
    expect(engine.importDroppedMedia).not.toHaveBeenCalled();
    expect(await screen.findByText('Drop exactly one file on a request.')).toBeInTheDocument();

    emitDrop({ count: 1, x: 5, y: 5 });
    await waitFor(() => expect(screen.getByTestId('native-status-gen_road')).toHaveTextContent('ready'));
    expect(engine.importDroppedMedia).toHaveBeenCalledWith(project.id, 'broll', 'Google Flow');
    expect(engine.importMedia).not.toHaveBeenCalled();

    emitDrop({ count: 1, x: 5, y: 5 }); // card is no longer pending
    expect(engine.importDroppedMedia).toHaveBeenCalledTimes(1);
  });

  it('logs a known issue for a linked file and clears it when the request is reset', async () => {
    const generated = { id: 'asset_gen', kind: 'video', role: 'broll', original_filename: 'road.mp4', video: asset.video, audio: null,
      provenance: { origin: 'generated-external', tool_name: 'Google Flow' } };
    engine.importMedia.mockImplementation(async () => {
      engine.summary.mockResolvedValue({ ...project, assets: [asset, generated] });
      return generated;
    });
    render(<LiveWorkspace />);
    fireEvent.change(await screen.findByLabelText(/Paste the planner/), { target: { value: requests } });
    fireEvent.click(screen.getByRole('button', { name: 'Load requests' }));
    await waitFor(() => expect(screen.getByTestId('native-status-gen_road')).toHaveTextContent('pending'));
    expect(screen.queryByLabelText(/Known issue you accept/)).toBeNull(); // only for linked files

    fireEvent.click(screen.getByRole('button', { name: /Import Google Flow file for gen_road/ }));
    await waitFor(() => expect(screen.getByTestId('native-status-gen_road')).toHaveTextContent('ready'));
    const note = screen.getByLabelText('Known issue you accept for gen_road');
    expect(screen.getByRole('button', { name: 'Accept with issue' })).toBeDisabled(); // a note is required
    fireEvent.change(note, { target: { value: ' road sign is garbled ' } });
    fireEvent.click(screen.getByRole('button', { name: 'Accept with issue' }));
    await waitFor(() => expect(screen.getByTestId('native-issue-gen_road')).toHaveTextContent('road sign is garbled'));
    expect(engine.acceptChecklistIssue).toHaveBeenCalledWith(project.id, 'gen_road', 'road sign is garbled', expect.any(Number));
    expect(screen.getByTestId('native-status-gen_road')).toHaveTextContent('ready');

    fireEvent.click(screen.getAllByRole('button', { name: 'Reset' })[0]);
    await waitFor(() => expect(screen.getByTestId('native-status-gen_road')).toHaveTextContent('pending'));
    expect(screen.queryByTestId('native-issue-gen_road')).toBeNull();
    expect(storedChecklist.accepted).toEqual({});
  });

  it('lists overlays the agent placed, labelled, with no way to place them by hand', async () => {
    const image = { id: 'asset_image', kind: 'image', role: 'broll', original_filename: 'frame.png',
      video: { width: 270, height: 480, duration_frames: 1, frame_rate: { num: 25, den: 1 } },
      audio: null, provenance: { origin: 'generated-external', tool_name: 'ChatGPT' } };
    engine.summary.mockResolvedValue({ ...project, assets: [asset, image] });
    engine.timeline.mockResolvedValue({ ...timeline, clips: [...timeline.clips, {
      clip_id: 'overlay1', asset_id: image.id, track: 'V2', source_in_frames: 0, source_out_frames: 1,
      timeline_in_frames: 15, timeline_duration_frames: 30, rationale: 'Shows the product at the hook',
    }] });
    render(<LiveWorkspace />);
    const view = await screen.findByTestId('agent-edit');
    await waitFor(() => expect(view).toHaveTextContent('frame.png'));
    expect(view).toHaveTextContent(/still image . frames 15.45/);
    expect(view).toHaveTextContent('AI-generated (external)');
    expect(view).toHaveTextContent('Why: Shows the product at the hook');
    expect(screen.queryByRole('button', { name: /Add image|Remove image overlay/ })).toBeNull();
    // The only number field left is the new-project length, not a cut or overlay control.
    expect(screen.queryAllByRole('spinbutton').map((field) => field.closest('label')?.textContent)).toEqual(['Target seconds']);
  });
});

describe('live three-step flow', () => {
  it('is organised as import with instructions, AI edit, then preview', async () => {
    render(<LiveWorkspace />);
    const one = await screen.findByTestId('step-1');
    const two = screen.getByTestId('step-2');
    const three = screen.getByTestId('step-3');
    expect(screen.getAllByRole('heading', { level: 2 }).map((heading) => heading.textContent)).toEqual(expect.arrayContaining([
      expect.stringContaining('Step 1 of 3'), expect.stringContaining('Step 2 of 3'), expect.stringContaining('Step 3 of 3'),
    ]));
    expect(one).toHaveTextContent('Import assets and instructions');
    expect(two).toHaveTextContent('AI edit');
    expect(three).toHaveTextContent('Preview');
    expect(screen.getByTestId('step-1-status')).toHaveTextContent('1 asset · instructions not saved yet');
    expect(screen.getByTestId('step-2-status')).toHaveTextContent('revision 1 · 1 cut');
    expect(screen.getByTestId('step-3-status')).toHaveTextContent('not rendered yet');

    // Step 1: bring everything in, with instructions. Nothing about editing or rendering.
    expect(within(one).getByRole('button', { name: 'Import main footage' })).toBeInTheDocument();
    expect(within(one).getByLabelText('Instructions')).toBeInTheDocument();
    expect(within(one).queryByRole('button', { name: /Render/ })).toBeNull();
    // Step 2: the agent's edit and the files it asks for.
    expect(within(two).getByTestId('agent-edit')).toBeInTheDocument();
    expect(within(two).getByTestId('native-asset-checklist')).toBeInTheDocument();
    expect(within(two).getByRole('button', { name: 'Copy MCP setup for your agent' })).toBeInTheDocument();
    // Step 3: review it, render the final, check and export.
    expect(within(three).getByRole('button', { name: 'Render fast proxy' })).toBeInTheDocument();
    expect(within(three).getByRole('button', { name: 'Render final MP4' })).toBeInTheDocument();
    expect(within(three).getByRole('button', { name: 'Run technical QA' })).toBeInTheDocument();
    expect(screen.queryByLabelText('Direction for the agent')).toBeNull();
  });

  it('tells the agent to read the saved instructions and commit the edit itself', async () => {
    const write = vi.fn().mockResolvedValue(undefined);
    Object.defineProperty(navigator, 'clipboard', { configurable: true, value: { writeText: write } });
    storedBrief = { ...storedBrief, version: 1, brief_text: 'PRIVATE-INSTRUCTION-TEXT' };
    render(<LiveWorkspace />);
    fireEvent.click(await screen.findByRole('button', { name: 'Copy MCP setup for your agent' }));
    await waitFor(() => expect(write).toHaveBeenCalledTimes(1));
    const message = write.mock.calls[0][0] as string;
    expect(message).toContain('engine mcp --shared-service');
    expect(message).toContain(project.id);
    expect(message).toContain('get_brief');
    expect(message).toContain('commit_timeline');
    expect(message).toContain('do not mark any as met');
    expect(message).not.toContain('PRIVATE-INSTRUCTION-TEXT'); // the agent reads it through MCP; it is not pasted around
  });
});

describe('live instructions for the AI editor', () => {
  it('saves a person-written brief with a version check and counts confirmed requirements', async () => {
    render(<LiveWorkspace />);
    expect(await screen.findByText('Not saved yet')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Save brief' })).toBeDisabled(); // nothing to save yet

    fireEvent.change(screen.getByLabelText('Instructions'), { target: { value: '30 s travel adapter ad' } });
    fireEvent.change(screen.getByLabelText('New requirement'), { target: { value: 'Product on screen by 0:05' } });
    fireEvent.click(screen.getByRole('button', { name: 'Add requirement' }));
    expect(screen.getByTestId('brief-confirmed')).toHaveTextContent('0 of 1 confirmed');
    fireEvent.change(screen.getByLabelText('Status of Product on screen by 0:05'), { target: { value: 'met' } });
    expect(screen.getByTestId('brief-confirmed')).toHaveTextContent('1 of 1 confirmed');

    fireEvent.click(screen.getByRole('button', { name: 'Save brief' }));
    await waitFor(() => expect(screen.getByText(/Saved version 1/)).toBeInTheDocument());
    expect(engine.saveBrief).toHaveBeenCalledWith(project.id, expect.objectContaining({
      brief_text: '30 s travel adapter ad',
      checklist: [expect.objectContaining({ text: 'Product on screen by 0:05', status: 'met' })],
    }), 0);
    expect(screen.getByRole('button', { name: 'Save brief' })).toBeDisabled();
  });

  it('shows the latest saved brief and says edits were not saved when another editor won', async () => {
    render(<LiveWorkspace />);
    fireEvent.change(await screen.findByLabelText('Instructions'), { target: { value: 'my edit' } });
    storedBrief = { ...storedBrief, version: 3, brief_text: 'someone else saved this' }; // another session saved first
    fireEvent.click(screen.getByRole('button', { name: 'Save brief' }));
    expect(await screen.findByText(/Your edits were not saved/)).toBeInTheDocument();
    await waitFor(() => expect(screen.getByLabelText('Instructions')).toHaveValue('someone else saved this'));
  });
});
