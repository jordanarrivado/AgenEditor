import React from 'react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { SimpleWorkspace } from '../../apps/desktop/src/features/live/SimpleWorkspace';

const engine = vi.hoisted(() => Object.fromEntries([
  'listProjects', 'summary', 'timeline', 'getBrief', 'saveBrief', 'listJobs', 'connection', 'createProject',
  'importFiles', 'importDroppedFiles', 'onMediaDropped', 'onAgentEvent', 'runAgent', 'stopAgent',
  'newAgentConversation', 'listSoundPacks', 'listSoundCatalog', 'listTransitionStyles', 'mediaUrl', 'startRender', 'runQA', 'saveExport', 'cacheModel',
].map((name) => [name, vi.fn()])));

vi.mock('../../apps/desktop/src/services/liveEngineRepository', () => ({ liveEngineRepository: engine }));

const project = { id: 'project_a', name: 'Teaser', current_revision: 0, created_at: '' };
const video = { width: 1080, height: 1920, duration_frames: 300, frame_rate: { num: 30, den: 1 } };
const asset = (id: string, role: string, name: string, generated = false) => ({
  id, role, kind: 'video', original_filename: name, sha256: '', size_bytes: 1, video, audio: null,
  provenance: generated ? { origin: 'generated-external', tool_name: 'Google Flow' } : { origin: 'original', tool_name: null },
});
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
  engine.listSoundPacks.mockResolvedValue([]);
  engine.listSoundCatalog.mockResolvedValue([]);
  engine.listTransitionStyles.mockResolvedValue([]);
  engine.connection.mockResolvedValue({ connected: true, command: '', workspace: '', claude_available: true });
  engine.onMediaDropped.mockImplementation(async (handler) => { emitDrop = handler; return () => undefined; });
  engine.onAgentEvent.mockImplementation(async (handler) => { emitAgent = handler; return () => undefined; });
});

describe('simple workspace', () => {
  it('sorts imported files into main footage, references and assets', async () => {
    render(<SimpleWorkspace />);
    expect(await within(await screen.findByTestId('bucket-main')).findByText('interview.mp4')).toBeInTheDocument();
    expect(within(screen.getByTestId('bucket-reference')).getByText('style.mp4')).toBeInTheDocument();
    const assets = within(screen.getByTestId('bucket-asset'));
    expect(assets.getByText('city.mp4')).toBeInTheDocument();
    expect(assets.getByText(/AI-generated \(external\) · Google Flow/)).toBeInTheDocument();
  });

  it('imports a multi-file drop into the box it landed on and reports per-file errors', async () => {
    engine.importDroppedFiles.mockResolvedValue([
      { file: 'one.mp4', asset: asset('a4', 'reference', 'one.mp4') },
      { file: 'bad.txt', error: 'unsupported file' },
    ]);
    render(<SimpleWorkspace />);
    const box = await screen.findByTestId('bucket-reference');
    document.elementFromPoint = vi.fn(() => box);
    emitDrop({ count: 2, x: 10, y: 10 });
    await waitFor(() => expect(engine.importDroppedFiles).toHaveBeenCalledWith(project.id, 'reference', undefined));
    expect(await screen.findByRole('status')).toHaveTextContent(/Added 1 file/);
    expect(screen.getByRole('status')).toHaveTextContent('bad.txt: unsupported file');
  });

  it('labels assets with the AI tool name only when one is given', async () => {
    engine.importFiles.mockResolvedValue(null);
    render(<SimpleWorkspace />);
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
    render(<SimpleWorkspace />);
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
    render(<SimpleWorkspace />);
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
    render(<SimpleWorkspace />);
    fireEvent.click(await screen.findByRole('radio', { name: /Review/ }));
    fireEvent.change(screen.getByLabelText('Prompt'), { target: { value: 'Is the hook strong enough?' } });
    fireEvent.click(screen.getByRole('button', { name: 'Send to Review' }));
    await waitFor(() => expect(engine.runAgent).toHaveBeenCalledWith(project.id, 'Is the hook strong enough?', 'review'));
  });

  it('keeps the stage when a run fails', async () => {
    engine.runAgent.mockResolvedValue({ ok: false, stopped: null, text: 'Overloaded' });
    render(<SimpleWorkspace />);
    fireEvent.change(await screen.findByLabelText('Prompt'), { target: { value: 'Plan it' } });
    fireEvent.click(screen.getByRole('button', { name: 'Send to Plan' }));
    expect(await screen.findByText('Overloaded')).toBeInTheDocument();
    expect(screen.getByRole('radio', { name: /Plan/ })).toHaveAttribute('aria-checked', 'true');
  });

  it('offers starter prompts that fill the prompt box', async () => {
    render(<SimpleWorkspace />);
    fireEvent.click(await screen.findByRole('button', { name: 'Remove silences and filler words' }));
    expect(screen.getByLabelText('Prompt')).toHaveValue('Remove silences and filler words');
    expect(engine.runAgent).not.toHaveBeenCalled();
  });

  it('tracks the Get started steps from real project state', async () => {
    render(<SimpleWorkspace />);
    const steps = within(await screen.findByRole('region', { name: 'Get started' }));
    // Main footage is imported; no styles on (mocks return none); nothing sent yet.
    await waitFor(() => expect(steps.getByText('1 of 3 done')).toBeInTheDocument());
    expect(steps.getByRole('progressbar')).toHaveAttribute('aria-valuenow', '1');
    expect(steps.getByText('(done)', { exact: false }).closest('p')).toHaveTextContent('Add main footage');
  });

  it('shows a stopped run honestly instead of as done', async () => {
    engine.runAgent.mockResolvedValue({ ok: false, stopped: 'stopped by you', text: null });
    render(<SimpleWorkspace />);
    fireEvent.change(await screen.findByLabelText('Prompt'), { target: { value: 'Go' } });
    fireEvent.click(screen.getByRole('button', { name: /Send/ }));
    expect(await screen.findByText('Claude stopped by you.')).toBeInTheDocument();
    expect(screen.queryByText(/done\. Next/)).not.toBeInTheDocument();
  });

  it('disables Send when Claude Code is not installed', async () => {
    engine.connection.mockResolvedValue({ connected: true, command: '', workspace: '', claude_available: false });
    render(<SimpleWorkspace />);
    fireEvent.change(await screen.findByLabelText('Prompt'), { target: { value: 'Go' } });
    expect(await screen.findByText(/Claude Code isn't installed/)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Send/ })).toBeDisabled();
  });

  it('refuses to save a final render whose technical check failed', async () => {
    engine.listJobs.mockResolvedValue([{ id: 'job_final', kind: 'final_render', status: 'succeeded', progress: 1, timeline_revision: 1 }]);
    engine.runQA.mockResolvedValue({ overall: 'fail', checks: [{ check_id: 'duration', status: 'fail', message: 'too long' }], generated_assets: [] });
    render(<SimpleWorkspace />);
    const save = await screen.findByRole('button', { name: /Check & save MP4/ });
    await waitFor(() => expect(save).toBeEnabled());
    fireEvent.click(save);
    expect(await screen.findByText('Technical check: fail')).toBeInTheDocument();
    expect(engine.saveExport).not.toHaveBeenCalled();
  });
});
