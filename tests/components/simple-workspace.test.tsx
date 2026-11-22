import React from 'react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { SimpleWorkspace } from '../../apps/desktop/src/features/live/SimpleWorkspace';

const engine = vi.hoisted(() => Object.fromEntries([
  'listProjects', 'summary', 'timeline', 'getBrief', 'saveBrief', 'listJobs', 'listVariants', 'connection', 'createProject',
  'importFiles', 'importDroppedFiles', 'onMediaDropped', 'onAgentEvent', 'runAgent', 'stopAgent',
  'newAgentConversation', 'polishInstructions', 'removeAsset', 'claudeUsage', 'refreshClaudeUsage', 'onClaudeUsage', 'getChecklist', 'saveChecklist', 'linkChecklistAsset', 'approveChecklistRequest', 'resetChecklistRequest', 'replaceChecklistAsset', 'openGenerator', 'assetUrl', 'importMedia', 'importDroppedMedia', 'claudeStatus', 'speechModelStatus', 'installClaudeCode', 'openClaudeSignIn', 'listSoundPacks', 'listSoundCatalog', 'listTransitionStyles', 'mediaUrl', 'previewQA', 'listEditorPresets', 'projectSettings', 'renameProject', 'setProjectArchived', 'saveDelivery', 'chooseEditorPreset', 'saveEditorPreset', 'deleteEditorPreset', 'exportOtio', 'importOtio', 'startRender', 'runQA', 'saveExport', 'cacheModel',
].map((name) => [name, vi.fn()])));

vi.mock('../../apps/desktop/src/services/liveEngineRepository', () => ({ liveEngineRepository: engine }));

const project = { id: 'project_a', name: 'Teaser', current_revision: 0, created_at: '' };
const video = { width: 1080, height: 1920, duration_frames: 300, frame_rate: { num: 30, den: 1 } };
const asset = (id: string, role: string, name: string, generated = false) => ({
  id, role, kind: 'video', original_filename: name, sha256: '', size_bytes: 1, video, audio: null,
  provenance: generated ? { origin: 'generated-external', tool_name: 'Google Flow' } : { origin: 'original', tool_name: null },
});
const emptyChecklist = { schema_version: 1, project_id: 'project_a', version: 0, configured: false, requests: [], links: {}, accepted: {}, review_required: false, approved: [] };
const brief = { schema_version: 1, project_id: project.id, version: 1, brief_text: 'Vertical', script_text: 's', reference_note: 'r', checklist: [] };

let emitDrop: (drop: { count: number; x: number; y: number }) => void = () => undefined;
let emitAgent: (projectId: string, event: unknown) => void = () => undefined;

beforeEach(() => {
  vi.resetAllMocks();
  engine.listProjects.mockResolvedValue([project]);
  engine.summary.mockResolvedValue({ ...project, assets: [
    asset('a1', 'primary', 'interview.mp4'), asset('a2', 'reference', 'style.mp4'), asset('a3', 'broll', 'city.mp4', true),
  ] });
  engine.timeline.mockResolvedValue({ revision: 0 });
  engine.getBrief.mockResolvedValue(brief);
  engine.saveBrief.mockImplementation(async (_id, next) => ({ ...brief, ...next, version: 2 }));
  engine.listJobs.mockResolvedValue([]);
  engine.listVariants.mockResolvedValue([]);
  engine.previewQA.mockResolvedValue({ report: null, gridUrl: null });
  engine.listEditorPresets.mockResolvedValue({ version: 0, presets: [] });
  engine.projectSettings.mockResolvedValue({ archived: false, editor_preset_id: null });
  engine.listSoundPacks.mockResolvedValue([]);
  engine.listSoundCatalog.mockResolvedValue([]);
  engine.claudeStatus.mockResolvedValue({ installed: true, version: '2.1.318 (Claude Code)', signed_in: true, plan: 'team', method: 'claude.ai' });
  engine.speechModelStatus.mockResolvedValue({ tiny: true, base: false, small: false });
  engine.claudeUsage.mockResolvedValue(null);
  engine.getChecklist.mockResolvedValue(emptyChecklist);
  engine.onClaudeUsage.mockResolvedValue(() => undefined);
  engine.listTransitionStyles.mockResolvedValue([]);
  engine.connection.mockResolvedValue({ connected: true, command: '', workspace: '', claude_available: true });
  engine.onMediaDropped.mockImplementation(async (handler) => { emitDrop = handler; return () => undefined; });
  engine.onAgentEvent.mockImplementation(async (handler) => { emitAgent = handler; return () => undefined; });
});

/** The app starts on Home; open the sample project like a person would. */
const openProject = async () => {
  render(<SimpleWorkspace />);
  fireEvent.click(await screen.findByRole('button', { name: 'Open Teaser' }));
};

describe('home', () => {
  it('opens a project that was just created even if the project list lags behind', async () => {
    engine.createProject.mockResolvedValue({ project_id: 'project_new' });
    engine.listProjects.mockResolvedValue([project]); // the refreshed list does not include it yet
    render(<SimpleWorkspace />);
    fireEvent.click(await screen.findByRole('button', { name: 'New project' }));
    fireEvent.change(screen.getByLabelText('New project name'), { target: { value: 'Fresh' } });
    fireEvent.click(screen.getByRole('button', { name: 'Create' }));
    await waitFor(() => expect(engine.summary).toHaveBeenCalledWith('project_new'));
    expect(await screen.findByTestId('bucket-main')).toBeInTheDocument();
  });

  it('starts on Home with the project list instead of opening a project', async () => {
    render(<SimpleWorkspace />);
    expect(await screen.findByRole('heading', { name: 'Your projects' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Open Teaser' })).toHaveTextContent('Not edited yet');
    expect(screen.queryByTestId('bucket-main')).toBeNull();
    expect(engine.summary).not.toHaveBeenCalled();
  });

  it('opens a project and goes back Home from the header', async () => {
    await openProject();
    expect(await screen.findByTestId('bucket-main')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Home' }));
    expect(await screen.findByRole('heading', { name: 'Your projects' })).toBeInTheDocument();
    expect(screen.queryByTestId('bucket-main')).toBeNull();
    expect(engine.listProjects).toHaveBeenCalledTimes(2); // refreshed on the way back
  });

  it('creates a project from Home and opens it', async () => {
    const created = { id: 'project_b', name: 'Launch reel', current_revision: 0, created_at: '' };
    engine.createProject.mockResolvedValue({ project_id: created.id });
    render(<SimpleWorkspace />);
    fireEvent.click(await screen.findByRole('button', { name: 'New project' }));
    fireEvent.change(screen.getByLabelText('New project name'), { target: { value: 'Launch reel' } });
    engine.listProjects.mockResolvedValue([created, project]);
    fireEvent.click(screen.getByRole('button', { name: 'Create' }));
    await waitFor(() => expect(engine.createProject).toHaveBeenCalledWith('Launch reel', 1800));
    await waitFor(() => expect(engine.summary).toHaveBeenCalledWith('project_b'));
    expect(await screen.findByTestId('bucket-main')).toBeInTheDocument();
  });

  it('shows an empty state when there are no projects', async () => {
    engine.listProjects.mockResolvedValue([]);
    render(<SimpleWorkspace />);
    expect(await screen.findByText('No projects yet')).toBeInTheDocument();
  });
});

describe('simple workspace', () => {
  it('sorts imported files into main footage, references and assets', async () => {
    await openProject();
    expect(await within(await screen.findByTestId('bucket-main')).findByText('interview.mp4')).toBeInTheDocument();
    expect(within(screen.getByTestId('bucket-reference')).getByText('style.mp4')).toBeInTheDocument();
    const assets = within(screen.getByTestId('bucket-asset'));
    expect(assets.getByText('city.mp4')).toBeInTheDocument();
    expect(assets.getByText(/AI-generated \(external\) · Google Flow/)).toBeInTheDocument();
  });

  it('offers the speech model install only while the model is missing', async () => {
    await openProject();
    await screen.findByTestId('bucket-main');
    await waitFor(() => expect(engine.speechModelStatus).toHaveBeenCalled());
    expect(screen.queryByRole('button', { name: /Install speech model/ })).toBeNull();
  });

  it('installs the missing speech model and then stops offering it', async () => {
    engine.speechModelStatus.mockResolvedValue({ tiny: false, base: false, small: false });
    engine.cacheModel.mockResolvedValue('tiny model cached');
    await openProject();
    fireEvent.click(await screen.findByRole('button', { name: /Install speech model/ }));
    expect(await screen.findByText('Speech model ready (tiny model cached).')).toBeInTheDocument();
    expect(engine.cacheModel).toHaveBeenCalledWith('tiny');
    expect(screen.queryByRole('button', { name: /Install speech model/ })).toBeNull();
  });

  it('imports a multi-file drop into the box it landed on and reports per-file errors', async () => {
    engine.importDroppedFiles.mockResolvedValue([
      { file: 'one.mp4', asset: asset('a4', 'reference', 'one.mp4') },
      { file: 'bad.txt', error: 'unsupported file' },
    ]);
    await openProject();
    const box = await screen.findByTestId('bucket-reference');
    document.elementFromPoint = vi.fn(() => box);
    emitDrop({ count: 2, x: 10, y: 10 });
    await waitFor(() => expect(engine.importDroppedFiles).toHaveBeenCalledWith(project.id, 'reference', undefined));
    expect(await screen.findByRole('status')).toHaveTextContent(/Added 1 file/);
    expect(screen.getByRole('status')).toHaveTextContent('bad.txt: unsupported file');
  });

  it('labels assets with the AI tool name only when one is given', async () => {
    engine.importFiles.mockResolvedValue(null);
    await openProject();
    const assets = within(await screen.findByTestId('bucket-asset'));
    fireEvent.change(assets.getByPlaceholderText('e.g. Google Flow'), { target: { value: ' Google Flow ' } });
    fireEvent.click(assets.getByRole('button', { name: /Browse/ }));
    await waitFor(() => expect(engine.importFiles).toHaveBeenCalledWith(project.id, 'asset', 'Google Flow'));
    fireEvent.click(within(screen.getByTestId('bucket-main')).getByRole('button', { name: /Browse/ }));
    await waitFor(() => expect(engine.importFiles).toHaveBeenCalledWith(project.id, 'main', undefined));
  });

  it('saves instructions, sends the prompt to Claude and streams its replies into Output', async () => {
    let finish: (value: unknown) => void = () => undefined;
    engine.runAgent.mockImplementation(() => new Promise((resolve) => { finish = resolve; }));
    await openProject();
    const instructions = await screen.findByLabelText(/Instructions/);
    await waitFor(() => expect(instructions).toHaveValue('Vertical'));
    fireEvent.change(instructions, { target: { value: 'Vertical, under 30s' } });
    fireEvent.change(screen.getByLabelText('Prompt'), { target: { value: 'Make a teaser' } });
    fireEvent.click(screen.getByRole('button', { name: /Send/ }));

    await waitFor(() => expect(engine.runAgent).toHaveBeenCalledWith(project.id, 'Make a teaser', 'plan'));
    expect(engine.saveBrief).toHaveBeenCalledWith(project.id, expect.objectContaining({ brief_text: 'Vertical, under 30s', script_text: 's' }), 1);
    emitAgent(project.id, { kind: 'tool', name: 'commit_timeline' });
    emitAgent(project.id, { kind: 'text', text: 'Cut to 28s around the hook.' });
    const log = within(screen.getByTestId('output-log'));
    expect(await log.findByText('Saving a new revision')).toBeInTheDocument();
    expect(log.getByText('Cut to 28s around the hook.')).toBeInTheDocument();
    expect(log.getByText('Make a teaser')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Stop/ })).toBeInTheDocument();

    finish({ ok: true, stopped: null, text: 'Cut to 28s around the hook.' });
    expect(await screen.findByRole('button', { name: /Send/ })).toBeInTheDocument();
  });

  it('starts a fresh project in Plan and moves to Execute, then Review, after each successful run', async () => {
    engine.runAgent.mockImplementation(async (_id, _text, stage) => ({ ok: true, stopped: null, text: `${stage} reply`, stage }));
    await openProject();
    const plan = await screen.findByRole('radio', { name: /Plan/ });
    await waitFor(() => expect(plan).toHaveAttribute('aria-checked', 'true'));
    expect(plan).toHaveTextContent('Opus 5.5 · high');
    expect(screen.getByRole('radio', { name: /Execute/ })).toHaveTextContent('Sonnet 5.5 · high');
    // Plan needs a prompt; Execute and Review fall back to a default one.
    expect(screen.getByRole('button', { name: 'Send to Plan' })).toBeDisabled();
    fireEvent.change(screen.getByLabelText('Prompt'), { target: { value: 'A 30s teaser' } });
    fireEvent.click(screen.getByRole('button', { name: 'Send to Plan' }));
    await waitFor(() => expect(engine.runAgent).toHaveBeenLastCalledWith(project.id, 'A 30s teaser', 'plan'));
    expect(await screen.findByText('Plan done. Next: Execute.')).toBeInTheDocument();
    expect(screen.getByRole('radio', { name: /Execute/ })).toHaveAttribute('aria-checked', 'true');

    fireEvent.click(screen.getByRole('button', { name: 'Send to Execute' }));
    await waitFor(() => expect(engine.runAgent).toHaveBeenLastCalledWith(project.id, 'Carry out the plan and any review notes.', 'execute'));
    await waitFor(() => expect(screen.getByRole('radio', { name: /Review/ })).toHaveAttribute('aria-checked', 'true'));
    expect(within(screen.getByTestId('output-log')).getByText('Execute · Sonnet 5.5 · high')).toBeInTheDocument();
  });

  it('lets the person pick a stage before Claude works', async () => {
    engine.runAgent.mockResolvedValue({ ok: true, stopped: null, text: 'Notes', stage: 'review' });
    await openProject();
    fireEvent.click(await screen.findByRole('radio', { name: /Review/ }));
    fireEvent.change(screen.getByLabelText('Prompt'), { target: { value: 'Is the hook strong enough?' } });
    fireEvent.click(screen.getByRole('button', { name: 'Send to Review' }));
    await waitFor(() => expect(engine.runAgent).toHaveBeenCalledWith(project.id, 'Is the hook strong enough?', 'review'));
  });

  it('keeps the stage when a run fails', async () => {
    engine.runAgent.mockResolvedValue({ ok: false, stopped: null, text: 'Overloaded' });
    await openProject();
    fireEvent.change(await screen.findByLabelText('Prompt'), { target: { value: 'Plan it' } });
    fireEvent.click(screen.getByRole('button', { name: 'Send to Plan' }));
    expect(await screen.findByText('Overloaded')).toBeInTheDocument();
    expect(screen.getByRole('radio', { name: /Plan/ })).toHaveAttribute('aria-checked', 'true');
  });

  it('removes a file only after a second click and reports the engine reason when refused', async () => {
    engine.removeAsset.mockRejectedValueOnce('{"code":"PERMISSION_DENIED","message":"this file is used in the current edit (revision 2); ask Claude to take it out first","details":{}}');
    await openProject();
    const assets = within(await screen.findByTestId('bucket-asset'));
    fireEvent.click(await assets.findByRole('button', { name: 'Remove city.mp4' }));
    expect(engine.removeAsset).not.toHaveBeenCalled();
    fireEvent.click(assets.getByRole('button', { name: 'Remove?' }));
    await waitFor(() => expect(engine.removeAsset).toHaveBeenCalledWith(project.id, 'a3'));
    expect(await screen.findByRole('status')).toHaveTextContent('city.mp4 was not removed: this file is used in the current edit (revision 2); ask Claude to take it out first');

    engine.removeAsset.mockResolvedValueOnce({ removed: 'a3' });
    engine.summary.mockResolvedValue({ ...project, assets: [asset('a1', 'primary', 'interview.mp4'), asset('a2', 'reference', 'style.mp4')] });
    fireEvent.click(assets.getByRole('button', { name: 'Remove city.mp4' }));
    fireEvent.click(assets.getByRole('button', { name: 'Remove?' }));
    await waitFor(() => expect(assets.queryByText('city.mp4')).toBeNull());
    expect(screen.getByRole('status')).toHaveTextContent('Removed city.mp4 from this project. Your own file is untouched');
  });

  it('polishes rough instructions with Opus and only saves when accepted', async () => {
    engine.polishInstructions.mockResolvedValue('- Make the video vertical.\n- Keep it under 45 seconds.');
    await openProject();
    const instructions = await screen.findByLabelText(/Instructions/);
    await waitFor(() => expect(instructions).toHaveValue('Vertical'));
    fireEvent.change(instructions, { target: { value: 'vertical pls, 45s max' } });
    fireEvent.click(screen.getByRole('button', { name: 'Polish with Opus' }));
    await waitFor(() => expect(engine.polishInstructions).toHaveBeenCalledWith('vertical pls, 45s max'));
    const suggestion = within(await screen.findByRole('region', { name: 'Polished instructions' }));
    expect(suggestion.getByText(/Keep it under 45 seconds/)).toBeInTheDocument();
    expect(engine.saveBrief).not.toHaveBeenCalled();

    fireEvent.click(suggestion.getByRole('button', { name: /Use this/ }));
    expect(instructions).toHaveValue('- Make the video vertical.\n- Keep it under 45 seconds.');
    await waitFor(() => expect(engine.saveBrief).toHaveBeenCalledWith(project.id, expect.objectContaining({ brief_text: '- Make the video vertical.\n- Keep it under 45 seconds.' }), 1));
    expect(screen.queryByRole('region', { name: 'Polished instructions' })).toBeNull();
  });

  it('keeps the person\'s notes when they decline the polish', async () => {
    engine.polishInstructions.mockResolvedValue('- Polished.');
    await openProject();
    const instructions = await screen.findByLabelText(/Instructions/);
    await waitFor(() => expect(instructions).toHaveValue('Vertical'));
    fireEvent.click(screen.getByRole('button', { name: 'Polish with Opus' }));
    fireEvent.click(await screen.findByRole('button', { name: 'Keep mine' }));
    expect(instructions).toHaveValue('Vertical');
    expect(engine.saveBrief).not.toHaveBeenCalled();
  });

  it('offers starter prompts that fill the prompt box', async () => {
    await openProject();
    fireEvent.click(await screen.findByRole('button', { name: 'Remove silences and filler words' }));
    expect(screen.getByLabelText('Prompt')).toHaveValue('Remove silences and filler words');
    expect(engine.runAgent).not.toHaveBeenCalled();
  });

  it('tracks the Get started steps from real project state', async () => {
    await openProject();
    const steps = within(await screen.findByRole('region', { name: 'Get started' }));
    // Main footage is imported; no styles on (mocks return none); nothing sent yet.
    await waitFor(() => expect(steps.getByText('1 of 3 done')).toBeInTheDocument());
    expect(steps.getByRole('progressbar')).toHaveAttribute('aria-valuenow', '1');
    expect(steps.getByText('(done)', { exact: false }).closest('p')).toHaveTextContent('Add main footage');
  });

  describe('B-roll from the plan', () => {
    const storyboardRequest = { id: 'broll_01_storyboard', kind: 'image', purpose: 'Storyboard', prompt: 'A grid', aspect: '9:16' };
    const videoRequest = { id: 'broll_01', kind: 'video', purpose: 'Street shot', prompt: 'Push in', aspect: '9:16' };
    const planReply = `1. Cut the intro.\n\n\`\`\`json\n${JSON.stringify({ asset_requests: [storyboardRequest, videoRequest] })}\n\`\`\`\nReview the plan.`;
    const configured = (extra = {}) => ({ ...emptyChecklist, version: 1, configured: true, review_required: true, requests: [storyboardRequest, videoRequest], ...extra });

    it('saves the plan\'s B-roll list and keeps Execute locked until every item is approved', async () => {
      engine.runAgent.mockResolvedValue({ ok: true, stopped: null, text: planReply, stage: 'plan' });
      let stored = emptyChecklist;
      engine.getChecklist.mockImplementation(async () => stored);
      engine.saveChecklist.mockImplementation(async (_id, list) => (stored = { ...emptyChecklist, version: 1, configured: true, ...list }));
      await openProject();
      fireEvent.change(await screen.findByLabelText('Prompt'), { target: { value: 'Teaser' } });
      fireEvent.click(screen.getByRole('button', { name: 'Send to Plan' }));
      await waitFor(() => expect(engine.saveChecklist).toHaveBeenCalledWith(project.id,
        expect.objectContaining({ review_required: true, requests: [storyboardRequest, videoRequest], links: {} }), 0));
      expect(await screen.findByText(/The plan lists 2 B-roll items \(0 ready\)/)).toBeInTheDocument();
      expect(await screen.findByRole('heading', { name: 'B-roll to make' })).toBeInTheDocument();
      expect(screen.getByRole('radio', { name: /Execute/ })).toHaveAttribute('aria-checked', 'true');
      expect(screen.getByRole('button', { name: 'Send to Execute' })).toBeDisabled();
      expect(screen.getByText(/Execute is locked: 0 of 2 B-roll items are ready/)).toBeInTheDocument();
      // Ctrl+Enter must not get around the lock.
      fireEvent.keyDown(screen.getByLabelText('Prompt'), { key: 'Enter', ctrlKey: true });
      expect(engine.runAgent).toHaveBeenCalledTimes(1);
    });

    it('tells the person when the plan\'s B-roll list cannot be read', async () => {
      engine.runAgent.mockResolvedValue({ ok: true, stopped: null, text: 'Plan\n{"asset_requests": [ {"id": "x"', stage: 'plan' });
      await openProject();
      fireEvent.change(await screen.findByLabelText('Prompt'), { target: { value: 'Teaser' } });
      fireEvent.click(screen.getByRole('button', { name: 'Send to Plan' }));
      expect(await screen.findByText(/B-roll list could not be read/)).toBeInTheDocument();
      expect(engine.saveChecklist).not.toHaveBeenCalled();
    });

    it('clears an older B-roll list when the new plan says no B-roll is needed', async () => {
      let stored = configured();
      engine.getChecklist.mockImplementation(async () => stored);
      engine.saveChecklist.mockImplementation(async (_id, list) => (stored = { ...emptyChecklist, version: stored.version + 1, configured: list !== null, ...(list ?? {}) }));
      engine.runAgent.mockResolvedValue({ ok: true, stopped: null, text: 'Revised plan.\n\nNo extra B-roll needed.\n', stage: 'plan' });
      engine.timeline.mockResolvedValue({ revision: 1 });
      await openProject();
      expect(await screen.findByRole('heading', { name: 'B-roll to make' })).toBeInTheDocument();
      fireEvent.click(screen.getByRole('radio', { name: /Plan/ }));
      fireEvent.change(screen.getByLabelText('Prompt'), { target: { value: 'Shorter please' } });
      fireEvent.click(screen.getByRole('button', { name: 'Send to Plan' }));
      await waitFor(() => expect(engine.saveChecklist).toHaveBeenCalledWith(project.id, null, 1));
      expect(await screen.findByText(/earlier B-roll list was cleared/)).toBeInTheDocument();
      await waitFor(() => expect(screen.queryByRole('heading', { name: 'B-roll to make' })).toBeNull());
      expect(screen.getByRole('button', { name: 'Send to Execute' })).toBeEnabled();
    });

    it('keeps the list when a plan reply says nothing about B-roll', async () => {
      engine.getChecklist.mockResolvedValue(configured());
      engine.runAgent.mockResolvedValue({ ok: true, stopped: null, text: 'Sure, shortening the intro.', stage: 'plan' });
      engine.timeline.mockResolvedValue({ revision: 1 });
      await openProject();
      await screen.findByRole('heading', { name: 'B-roll to make' });
      fireEvent.click(screen.getByRole('radio', { name: /Plan/ }));
      fireEvent.change(screen.getByLabelText('Prompt'), { target: { value: 'Shorter' } });
      fireEvent.click(screen.getByRole('button', { name: 'Send to Plan' }));
      await screen.findByText('Plan done. Next: Execute.');
      expect(engine.saveChecklist).not.toHaveBeenCalled();
      expect(screen.getByRole('heading', { name: 'B-roll to make' })).toBeInTheDocument();
    });

    it('replaces a file in one engine step, and a refused file leaves the approved one alone', async () => {
      let list = configured({ version: 3, links: { broll_01_storyboard: 'a_old' }, approved: ['broll_01_storyboard'] });
      engine.getChecklist.mockImplementation(async () => list);
      engine.summary.mockResolvedValue({ ...project, assets: [{ ...asset('a_old', 'reference', 'old.png'), kind: 'image' }] });
      engine.importMedia.mockResolvedValue({ ...asset('a_new', 'reference', 'new.png'), kind: 'image' });
      engine.removeAsset.mockResolvedValue({ removed: 'x' });
      engine.assetUrl.mockResolvedValue('asset://old');
      engine.replaceChecklistAsset.mockRejectedValueOnce('{"code":"INVALID_ARGUMENT","message":"reset the video first; it was made from this storyboard","details":{}}');
      await openProject();
      const card = within(await screen.findByTestId('broll-broll_01_storyboard'));
      fireEvent.click(card.getByRole('button', { name: /Replace file/ }));
      await waitFor(() => expect(engine.replaceChecklistAsset).toHaveBeenCalledWith(project.id, 'broll_01_storyboard', 'a_new', 3));
      await waitFor(() => expect(engine.removeAsset).toHaveBeenCalledWith(project.id, 'a_new')); // the rejected import is cleaned up
      expect(await screen.findByRole('status')).toHaveTextContent('reset the video first');
      expect(engine.resetChecklistRequest).not.toHaveBeenCalled(); // never a separate reset that could strand the request
      expect(engine.removeAsset).not.toHaveBeenCalledWith(project.id, 'a_old');

      engine.replaceChecklistAsset.mockImplementationOnce(async (_id, requestId, assetId) => {
        list = { ...list, version: 4, links: { [requestId]: assetId }, approved: [] };
        return list;
      });
      fireEvent.click(within(await screen.findByTestId('broll-broll_01_storyboard')).getByRole('button', { name: /Replace file/ }));
      await waitFor(() => expect(engine.removeAsset).toHaveBeenCalledWith(project.id, 'a_old')); // old file leaves only after success
    });

    it('does not take a dropped file for a video that is still waiting on its storyboard', async () => {
      engine.getChecklist.mockResolvedValue(configured());
      await openProject();
      const card = await screen.findByTestId('broll-broll_01');
      document.elementFromPoint = vi.fn(() => card);
      emitDrop({ count: 1, x: 5, y: 5 });
      expect(await screen.findByRole('status')).toHaveTextContent('Approve (or skip) the storyboard first');
      expect(engine.importDroppedMedia).not.toHaveBeenCalled();
    });

    it('unlocks Execute when every item is approved or skipped', async () => {
      engine.getChecklist.mockResolvedValue(configured({ links: { broll_01_storyboard: 'dropped', broll_01: 'dropped' } }));
      engine.timeline.mockResolvedValue({ revision: 1 });
      await openProject();
      expect(await screen.findByText('2 of 2 ready')).toBeInTheDocument();
      expect(screen.getByRole('button', { name: 'Send to Execute' })).toBeEnabled();
      expect(screen.queryByText(/Execute is locked/)).toBeNull();
    });

    it('attaches a dropped file to the card it landed on, labelled with the tool', async () => {
      let list = configured();
      engine.getChecklist.mockImplementation(async () => list);
      engine.importDroppedMedia.mockResolvedValue({ ...asset('a9', 'reference', 'grid.png'), kind: 'image' });
      engine.linkChecklistAsset.mockImplementation(async (_id, requestId, assetId) => {
        list = { ...list, version: 2, links: { [requestId]: assetId } };
        return list;
      });
      await openProject();
      const card = await screen.findByTestId('broll-broll_01_storyboard');
      document.elementFromPoint = vi.fn(() => card);
      emitDrop({ count: 1, x: 5, y: 5 });
      await waitFor(() => expect(engine.importDroppedMedia).toHaveBeenCalledWith(project.id, 'reference', 'ChatGPT'));
      await waitFor(() => expect(engine.linkChecklistAsset).toHaveBeenCalledWith(project.id, 'broll_01_storyboard', 'a9', 1));
      expect(await screen.findByRole('status')).toHaveTextContent('Added grid.png. Look it over on its card, then approve it.');
    });

    it('removes a file of the wrong kind instead of linking it', async () => {
      engine.getChecklist.mockResolvedValue(configured());
      engine.importMedia.mockResolvedValue({ ...asset('a10', 'broll', 'clip.mp4'), kind: 'video' });
      engine.removeAsset.mockResolvedValue({ removed: 'a10' });
      await openProject();
      const card = within(await screen.findByTestId('broll-broll_01_storyboard'));
      fireEvent.click(card.getByRole('button', { name: /Browse/ }));
      await waitFor(() => expect(engine.importMedia).toHaveBeenCalledWith(project.id, 'reference', 'ChatGPT'));
      await waitFor(() => expect(engine.removeAsset).toHaveBeenCalledWith(project.id, 'a10'));
      expect(await screen.findByRole('status')).toHaveTextContent('clip.mp4 is a video, but this item needs an image.');
      expect(engine.linkChecklistAsset).not.toHaveBeenCalled();
    });
  });

  it('shows a stopped run honestly instead of as done', async () => {
    engine.runAgent.mockResolvedValue({ ok: false, stopped: 'stopped by you', text: null });
    await openProject();
    fireEvent.change(await screen.findByLabelText('Prompt'), { target: { value: 'Go' } });
    fireEvent.click(screen.getByRole('button', { name: /Send/ }));
    expect(await screen.findByText('Claude stopped by you.')).toBeInTheDocument();
    expect(screen.queryByText(/done\. Next/)).not.toBeInTheDocument();
  });

  it('disables Send when Claude Code is not installed', async () => {
    engine.connection.mockResolvedValue({ connected: true, command: '', workspace: '', claude_available: false });
    await openProject();
    fireEvent.change(await screen.findByLabelText('Prompt'), { target: { value: 'Go' } });
    expect(await screen.findByText(/Claude Code isn't installed/)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Send/ })).toBeDisabled();
  });

  it('refuses to save a final render whose technical check failed', async () => {
    engine.listJobs.mockResolvedValue([{ id: 'job_final', kind: 'final_render', status: 'succeeded', progress: 1, timeline_revision: 1 }]);
    engine.runQA.mockResolvedValue({ overall: 'fail', checks: [{ check_id: 'duration', status: 'fail', message: 'too long' }], generated_assets: [] });
    await openProject();
    const save = await screen.findByRole('button', { name: /Check & save MP4/ });
    await waitFor(() => expect(save).toBeEnabled());
    fireEvent.click(save);
    expect(await screen.findByText('Technical check: fail')).toBeInTheDocument();
    expect(engine.saveExport).not.toHaveBeenCalled();
  });

  it('imports an .otio file only after a second click, and reports a refusal without changing the edit', async () => {
    engine.timeline.mockResolvedValue({ revision: 2 });
    engine.importOtio.mockResolvedValueOnce({ committed_revision: null, clip_count: 0, issues: [{ code: 'INVALID_TIMELINE', message: 'clip "x" uses media that is not registered in this project', severity: 'error' }] })
      .mockResolvedValueOnce({ committed_revision: 3, clip_count: 4, issues: [{ code: 'INVALID_TIMELINE', message: 'captions were not imported', severity: 'warning' }] });
    await openProject();
    const button = await screen.findByRole('button', { name: 'Import .otio…' });
    fireEvent.click(button);
    expect(engine.importOtio).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole('button', { name: 'Replace the cut? Click again' }));
    expect(await screen.findByText(/The timeline was not imported: clip "x" uses media that is not registered/)).toBeInTheDocument();
    expect(engine.importOtio).toHaveBeenCalledWith('project_a', 2);
    fireEvent.click(screen.getByRole('button', { name: 'Import .otio…' }));
    fireEvent.click(screen.getByRole('button', { name: 'Replace the cut? Click again' }));
    expect(await screen.findByText(/Imported 4 clips as revision 3. captions were not imported/)).toBeInTheDocument();
  });

  it('saves a delivery: every final that passes its check, under the client name, with a known-issues note', async () => {
    engine.listJobs.mockResolvedValue([
      { id: 'job_main', kind: 'final_render', status: 'succeeded', progress: 1, timeline_revision: 2 },
      { id: 'job_hook', kind: 'final_render', status: 'succeeded', progress: 1, timeline_revision: 2, variant_id: 'variant_a', variant_revision: 3 },
      { id: 'job_es', kind: 'final_render', status: 'succeeded', progress: 1, timeline_revision: 2, variant_id: 'variant_b', variant_revision: 1 },
    ]);
    const variant = (id: string, name: string, revision: number) => ({
      variant: { id, name, kind: 'hook', language: null, note: '', revision }, main_revision: 2, duration_frames: 90,
      renderable: true, errors: [], warnings: [], pending_requests: [],
    });
    engine.listVariants.mockResolvedValue([variant('variant_a', 'Hook B', 3), variant('variant_b', 'Spanish', 1)]);
    engine.projectSettings.mockResolvedValue({ archived: false, editor_preset_id: null, client_name: 'Acme' });
    engine.runQA.mockImplementation(async (_p: string, jobId: string) => ({ overall: jobId === 'job_es' ? 'fail' : 'pass', checks: [], generated_assets: [] }));
    engine.saveDelivery.mockResolvedValue({
      folder: 'C:/out/Acme_Teaser_delivery_20261122', note: 'C:/out/Acme_Teaser_delivery_20261122/known_issues.md', excluded: [],
      files: [
        { job_id: 'job_main', name: 'Acme_Teaser_Main_9x16_3s_r2', saved: ['C:/out/Acme_Teaser_delivery_20261122/Acme_Teaser_Main_9x16_3s_r2.mp4'] },
        { job_id: 'job_hook', name: 'Acme_Teaser_HookB_9x16_3s_r2v3', error: 'render is not complete' },
      ],
    });
    await openProject();
    fireEvent.click(await screen.findByRole('button', { name: /Save delivery/ }));
    const form = within(await screen.findByRole('form', { name: 'Save delivery' }));
    const client = form.getByLabelText(/Client name/);
    expect(client).toHaveValue('Acme');
    expect(engine.runQA).not.toHaveBeenCalled();
    fireEvent.change(client, { target: { value: ' Acme Co ' } });
    fireEvent.click(form.getByRole('button', { name: 'Choose folder…' }));
    await waitFor(() => expect(engine.saveDelivery).toHaveBeenCalledWith('project_a', ['job_main', 'job_hook'], 'Acme Co'));
    expect(engine.runQA).toHaveBeenCalledTimes(3);
    expect(await screen.findByText(/Saved 1 of 3 finals with a known-issues note in C:\/out\/Acme_Teaser_delivery_20261122. Not saved \(technical check failed\): Spanish. Errors: Acme_Teaser_HookB_9x16_3s_r2v3: render is not complete/)).toBeInTheDocument();
    expect(screen.queryByRole('form', { name: 'Save delivery' })).toBeNull();
  });

  it('offers a delivery with only the main final, and cancelling saves nothing', async () => {
    engine.listJobs.mockResolvedValue([{ id: 'job_main', kind: 'final_render', status: 'succeeded', progress: 1, timeline_revision: 2 }]);
    await openProject();
    fireEvent.click(await screen.findByRole('button', { name: /Save delivery/ }));
    const form = within(await screen.findByRole('form', { name: 'Save delivery' }));
    expect(form.getByLabelText(/Client name/)).toHaveValue('');
    expect(form.getByText(/Main edit · each is checked first/)).toBeInTheDocument();
    fireEvent.click(form.getByRole('button', { name: 'Cancel' }));
    expect(screen.queryByRole('form', { name: 'Save delivery' })).toBeNull();
    expect(engine.saveDelivery).not.toHaveBeenCalled();
  });
});
