import React, { useCallback, useEffect, useRef, useState } from 'react';
import { Clapperboard, Download, FileVideo2, Film, Images, Loader2, Plus, Send, Sparkles, Square, Upload, Wrench } from 'lucide-react';
import {
  liveEngineRepository as engine,
  type AgentEvent,
  type AgentStage,
  type EngineAsset,
  type EngineBrief,
  type EngineConnection,
  type EngineJob,
  type EngineProject,
  type EngineQA,
  type ImportBucket,
  type ImportOutcome,
} from '../../services/liveEngineRepository';
import { ClaudeMarkdown } from '../../components/common/ClaudeMarkdown';
import { SoundLibraryPanel } from './SoundLibraryPanel';
import { TransitionsPanel } from './TransitionsPanel';

const button = 'inline-flex items-center justify-center gap-1.5 rounded-lg border border-line px-3 py-2 text-sm font-medium text-fg hover:bg-elevated disabled:opacity-40 disabled:cursor-not-allowed';
const primary = `${button} border-accent bg-accent text-on-accent hover:bg-accent-hover`;
const panel = 'rounded-xl border border-line bg-surface p-4';
const field = 'w-full rounded-lg border border-line bg-app px-3 py-2 text-sm text-fg outline-none focus:border-accent';

const errorText = (error: unknown) => error instanceof Error ? error.message : String(error);

/** One line in the Output conversation. */
export type OutputEntry =
  | { kind: 'prompt'; text: string; stage?: AgentStage }
  | { kind: 'text'; text: string }
  | { kind: 'tool'; name: string }
  | { kind: 'status'; text: string; tone: 'ok' | 'warn' | 'error' };

/**
 * The three Claude stages (spec §3.1). The shell maps each to its model and effort; the labels
 * here only describe that mapping. After a successful run the picker moves to the next stage.
 */
export const STAGES: { id: AgentStage; label: string; model: string; hint: string; fallbackPrompt: string | null }[] = [
  { id: 'plan', label: 'Plan', model: 'Opus 5.5 · high', hint: "Writes a plan from your files and instructions. Doesn't change the video.", fallbackPrompt: null },
  { id: 'execute', label: 'Execute', model: 'Sonnet 5.5 · high', hint: 'Makes the edit (following the plan and review notes) and renders a preview.', fallbackPrompt: 'Carry out the plan and any review notes.' },
  { id: 'review', label: 'Review', model: 'Opus 5.5 · high', hint: "Checks the edit against your instructions and lists fixes. Doesn't change the video.", fallbackPrompt: 'Review the current edit.' },
];
const NEXT_STAGE: Record<AgentStage, AgentStage> = { plan: 'execute', execute: 'review', review: 'execute' };
const stageInfo = (id: AgentStage) => STAGES.find((stage) => stage.id === id) ?? STAGES[0];

const BUCKETS: { id: ImportBucket; title: string; hint: string; icon: React.ReactNode; roles: EngineAsset['role'][] }[] = [
  { id: 'main', title: 'Main footage', hint: 'The video you want edited.', icon: <FileVideo2 className="w-4 h-4" />, roles: ['primary'] },
  { id: 'reference', title: 'References', hint: 'Examples of the style or pacing you want. Never cut into the video.', icon: <Images className="w-4 h-4" />, roles: ['reference'] },
  { id: 'asset', title: 'Assets', hint: 'B-roll, images, logos and music the editor may use.', icon: <Film className="w-4 h-4" />, roles: ['broll', 'logo', 'music', 'sfx'] },
];

const TOOL_LABELS: Record<string, string> = {
  get_brief: 'Reading your instructions',
  list_assets: 'Looking at your files',
  inspect_asset: 'Inspecting a file',
  transcribe_asset: 'Transcribing speech',
  get_transcript: 'Reading the transcript',
  get_timeline: 'Reading the current edit',
  validate_timeline: 'Checking the edit',
  commit_timeline: 'Saving a new revision',
  apply_revision: 'Saving a new revision',
  submit_edit_plan: 'Checking an edit plan',
  start_render: 'Rendering a preview',
  get_job_status: 'Waiting for the render',
};

export const SimpleWorkspace: React.FC = () => {
  const [projects, setProjects] = useState<EngineProject[]>([]);
  const [projectId, setProjectId] = useState<string | null>(null);
  const currentProjectId = useRef(projectId);
  currentProjectId.current = projectId;
  const [assets, setAssets] = useState<EngineAsset[]>([]);
  const [revision, setRevision] = useState(0);
  const [brief, setBrief] = useState<EngineBrief | null>(null);
  const [instructions, setInstructions] = useState('');
  const [jobs, setJobs] = useState<EngineJob[]>([]);
  const [connection, setConnection] = useState<EngineConnection | null>(null);
  const [newName, setNewName] = useState('');
  const [creating, setCreating] = useState(false);
  const [importing, setImporting] = useState<ImportBucket | null>(null);
  const [toolName, setToolName] = useState('');
  const [prompt, setPrompt] = useState('');
  const [outputs, setOutputs] = useState<Record<string, OutputEntry[]>>({});
  const [running, setRunning] = useState(false);
  // Chosen stage per project; until the person picks one, a fresh project plans first.
  const [stages, setStages] = useState<Record<string, AgentStage>>({});
  const [previewUrl, setPreviewUrl] = useState<string | null>(null);
  const shownPreview = useRef<string | null>(null);
  const [qa, setQA] = useState<{ jobId: string; report: EngineQA } | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [modelState, setModelState] = useState<'idle' | 'installing'>('idle');

  const output = projectId ? outputs[projectId] ?? [] : [];
  const stage: AgentStage = (projectId && stages[projectId]) || (revision === 0 ? 'plan' : 'execute');
  const currentStage = stageInfo(stage);
  const chooseStage = (id: string, next: AgentStage) => setStages((all) => ({ ...all, [id]: next }));
  const append = useCallback((id: string, ...entries: OutputEntry[]) => {
    setOutputs((all) => ({ ...all, [id]: [...(all[id] ?? []), ...entries] }));
  }, []);

  const refreshLibrary = useCallback(async () => {
    const list = await engine.listProjects();
    setProjects(list);
    setProjectId((current) => current ?? list[0]?.id ?? null);
  }, []);

  const refreshProject = useCallback(async (id: string) => {
    const [summary, timeline, storedBrief, projectJobs] = await Promise.all([
      engine.summary(id), engine.timeline(id), engine.getBrief(id), engine.listJobs(id),
    ]);
    if (id !== currentProjectId.current) return;
    setAssets(summary.assets);
    setRevision(timeline.revision);
    setBrief(storedBrief);
    setInstructions(storedBrief.brief_text);
    setJobs(projectJobs);
  }, []);

  useEffect(() => {
    void refreshLibrary().catch((error) => setNotice(errorText(error)));
    void engine.connection().then(setConnection).catch((error) => setNotice(errorText(error)));
  }, [refreshLibrary]);

  useEffect(() => {
    setAssets([]);
    setBrief(null);
    setJobs([]);
    setPreviewUrl(null);
    shownPreview.current = null;
    setQA(null);
    if (projectId) void refreshProject(projectId).catch((error) => setNotice(errorText(error)));
  }, [projectId, refreshProject]);

  // Jobs and revisions change while Claude works; poll the engine for the real state.
  useEffect(() => {
    if (!projectId) return;
    const timer = window.setInterval(() => {
      void engine.listJobs(projectId).then((list) => { if (currentProjectId.current === projectId) setJobs(list); }).catch(() => undefined);
      void engine.timeline(projectId).then((tl) => { if (currentProjectId.current === projectId) setRevision(tl.revision); }).catch(() => undefined);
    }, 1500);
    return () => window.clearInterval(timer);
  }, [projectId]);

  const latestPreview = jobs.find((job) => job.kind === 'preview_render' && job.status === 'succeeded');
  const latestFinal = jobs.find((job) => job.kind === 'final_render' && job.status === 'succeeded');
  const activeJobs = jobs.filter((job) => job.status === 'queued' || job.status === 'running');

  // Show the newest finished preview as soon as the engine reports it.
  useEffect(() => {
    if (!projectId || !latestPreview || shownPreview.current === latestPreview.id) return;
    shownPreview.current = latestPreview.id;
    void engine.mediaUrl(projectId, latestPreview.id).then(setPreviewUrl).catch(() => undefined);
  }, [projectId, latestPreview]);

  useEffect(() => {
    let unlisten: (() => void) | undefined;
    let cancelled = false;
    engine.onAgentEvent((id, event: AgentEvent) => append(id, event))
      .then((stop) => { if (cancelled) stop(); else unlisten = stop; })
      .catch(() => undefined);
    return () => { cancelled = true; unlisten?.(); };
  }, [append]);

  const reportImports = (outcomes: ImportOutcome[] | null) => {
    if (!outcomes) return;
    const failed = outcomes.filter((item) => item.error);
    const added = outcomes.length - failed.length;
    setNotice([
      added > 0 ? `Added ${added} file${added === 1 ? '' : 's'}. Originals are copied, hashed and never changed.` : '',
      ...failed.map((item) => `${item.file}: ${item.error}`),
    ].filter(Boolean).join('\n'));
  };

  const importInto = (bucket: ImportBucket, dropped: boolean) => {
    if (!projectId || importing) return;
    const tool = bucket === 'asset' && toolName.trim() ? toolName.trim() : undefined;
    setImporting(bucket);
    setNotice(null);
    const id = projectId;
    void (dropped ? engine.importDroppedFiles(id, bucket, tool) : engine.importFiles(id, bucket, tool))
      .then(async (outcomes) => { reportImports(outcomes); await refreshProject(id); })
      .catch((error) => setNotice(errorText(error)))
      .finally(() => setImporting(null));
  };

  // The shell keeps the dropped paths; the page only learns which box the drop landed on.
  const dropHandler = useRef<(drop: { count: number; x: number; y: number }) => void>(() => undefined);
  dropHandler.current = (drop) => {
    const box = document.elementFromPoint(drop.x, drop.y)?.closest<HTMLElement>('[data-bucket]');
    if (!box) { setNotice('Drop files onto Main footage, References or Assets.'); return; }
    importInto(box.dataset.bucket as ImportBucket, true);
  };
  useEffect(() => {
    let unlisten: (() => void) | undefined;
    let cancelled = false;
    engine.onMediaDropped((drop) => dropHandler.current(drop))
      .then((stop) => { if (cancelled) stop(); else unlisten = stop; })
      .catch(() => undefined);
    return () => { cancelled = true; unlisten?.(); };
  }, []);

  const createProject = () => {
    const name = newName.trim();
    if (!name) return;
    setBusy(true);
    void engine.createProject(name, 60 * 30)
      .then(async (created) => { await refreshLibrary(); setProjectId(created.project_id); setNewName(''); setCreating(false); })
      .catch((error) => setNotice(errorText(error)))
      .finally(() => setBusy(false));
  };

  const saveInstructions = async () => {
    if (!projectId || !brief || instructions === brief.brief_text) return;
    try {
      setBrief(await engine.saveBrief(projectId, {
        brief_text: instructions, script_text: brief.script_text, reference_note: brief.reference_note, checklist: brief.checklist,
      }, brief.version));
    } catch (error) {
      setNotice(`Instructions not saved: ${errorText(error)}`);
      throw error;
    }
  };

  const send = async () => {
    const runStage = stage;
    const text = prompt.trim() || currentStage.fallbackPrompt || '';
    if (!projectId || !text || running) return;
    const id = projectId;
    try { await saveInstructions(); } catch { return; }
    setRunning(true);
    setNotice(null);
    setPrompt('');
    append(id, { kind: 'prompt', text, stage: runStage });
    try {
      const result = await engine.runAgent(id, text, runStage);
      if (result.stopped) append(id, { kind: 'status', tone: 'warn', text: `Claude ${result.stopped}.` });
      else if (!result.ok) append(id, { kind: 'status', tone: 'error', text: result.text ?? 'Claude reported an error.' });
      else {
        const next = NEXT_STAGE[runStage];
        if (result.text) append(id, { kind: 'status', tone: 'ok', text: `${stageInfo(runStage).label} done. Next: ${stageInfo(next).label}.` });
        chooseStage(id, next);
      }
    } catch (error) {
      append(id, { kind: 'status', tone: 'error', text: errorText(error) });
    } finally {
      setRunning(false);
      if (currentProjectId.current === id) void refreshProject(id).catch(() => undefined);
    }
  };

  const newConversation = () => {
    if (!projectId) return;
    const id = projectId;
    void engine.newAgentConversation(id).then(() => {
      setOutputs((all) => ({ ...all, [id]: [] }));
      setStages(({ [id]: _dropped, ...rest }) => rest);
    });
  };

  const run = (task: () => Promise<void>) => {
    setBusy(true);
    setNotice(null);
    void task().catch((error) => setNotice(errorText(error))).finally(() => setBusy(false));
  };

  const renderFinal = () => run(async () => {
    if (!projectId) return;
    const job = await engine.startRender(projectId, revision, true);
    setJobs(await engine.listJobs(projectId));
    setNotice(`Final render of revision ${job.timeline_revision} started.`);
  });

  const checkAndSave = () => run(async () => {
    if (!projectId || !latestFinal) return;
    const report = qa?.jobId === latestFinal.id ? qa.report : await engine.runQA(projectId, latestFinal.id);
    setQA({ jobId: latestFinal.id, report });
    if (report.overall === 'fail') throw new Error('Technical check failed, so the file was not saved. See the failed checks below.');
    const path = await engine.saveExport(projectId, latestFinal.id);
    if (path) setNotice(`Saved to ${path}`);
  });

  const installModel = () => {
    setModelState('installing');
    void engine.cacheModel('tiny').then((message) => setNotice(`Speech model ready (${message}).`))
      .catch((error) => setNotice(errorText(error))).finally(() => setModelState('idle'));
  };

  const hasMain = assets.some((asset) => asset.role === 'primary');
  const claudeMissing = connection?.claude_available === false;

  return (
    <div className="flex h-screen flex-col bg-app text-fg overflow-hidden">
      <header className="flex flex-wrap items-center gap-3 border-b border-line bg-sidebar px-4 py-3">
        <div className="flex items-center gap-2 font-bold"><Clapperboard className="w-5 h-5 text-accent" /> AgenEditor</div>
        <select aria-label="Project" className="rounded-lg border border-line bg-app px-3 py-1.5 text-sm max-w-[16rem]" value={projectId ?? ''} onChange={(e) => setProjectId(e.target.value || null)} disabled={running}>
          {projects.length === 0 && <option value="">No projects yet</option>}
          {projects.map((project) => <option key={project.id} value={project.id}>{project.name}</option>)}
        </select>
        {creating ? (
          <form className="flex gap-2" onSubmit={(e) => { e.preventDefault(); createProject(); }}>
            <input autoFocus aria-label="New project name" className={`${field} py-1.5 w-48`} placeholder="Project name" value={newName} maxLength={100} onChange={(e) => setNewName(e.target.value)} />
            <button className={primary} disabled={busy || !newName.trim()} type="submit">Create</button>
            <button className={button} type="button" onClick={() => setCreating(false)}>Cancel</button>
          </form>
        ) : <button className={button} onClick={() => setCreating(true)} disabled={running}><Plus className="w-4 h-4" /> New project</button>}
        <span className="ml-auto text-xs text-fg-3">{connection?.connected ? '● Engine connected' : 'Engine starting…'} · files stay on this computer</span>
      </header>

      {notice && <div role="status" className="mx-4 mt-3 whitespace-pre-line rounded-lg border border-accent/40 bg-accent/10 px-4 py-2 text-sm break-words">{notice}</div>}

      {!projectId ? (
        <div className="m-auto text-center space-y-3"><p className="text-fg-2">Create a project to start.</p><button className={primary} onClick={() => setCreating(true)}><Plus className="w-4 h-4" /> New project</button></div>
      ) : (
        <main className="flex-1 min-h-0 grid grid-cols-1 lg:grid-cols-2 gap-4 overflow-y-auto lg:overflow-hidden p-4">
          <div className="space-y-4 lg:overflow-y-auto lg:pr-1">
            <section aria-labelledby="imports-title" className="space-y-3">
              <h2 id="imports-title" className="text-sm font-semibold uppercase tracking-wider text-fg-2">Imports</h2>
              {BUCKETS.map((bucket) => {
                const files = assets.filter((asset) => bucket.roles.includes(asset.role));
                return (
                  <div key={bucket.id} data-bucket={bucket.id} data-testid={`bucket-${bucket.id}`} className={`${panel} border-dashed space-y-2`}>
                    <div className="flex items-center justify-between gap-2">
                      <h3 className="flex items-center gap-2 font-semibold">{bucket.icon} {bucket.title} <span className="text-xs font-normal text-fg-3">{files.length || ''}</span></h3>
                      <button className={button} disabled={importing !== null || running} onClick={() => importInto(bucket.id, false)}>
                        {importing === bucket.id ? <Loader2 className="w-4 h-4 animate-spin" /> : <Upload className="w-4 h-4" />} Browse
                      </button>
                    </div>
                    <p className="text-xs text-fg-3">{bucket.hint} Drop files here; you can add several at once.</p>
                    {files.length > 0 && (
                      <ul className="space-y-1 text-xs">
                        {files.map((asset) => (
                          <li key={asset.id} className="flex flex-wrap gap-x-2 rounded-md bg-app px-2 py-1">
                            <span className="font-medium truncate max-w-full">{asset.original_filename}</span>
                            <span className="text-fg-3">{asset.video ? `${asset.video.width}×${asset.video.height}` : 'audio'}{bucket.id === 'asset' ? ` · ${asset.role === 'sfx' ? 'sound effect' : asset.role}` : ''}</span>
                            {asset.provenance?.origin === 'generated-external' && <span className="text-warning">AI-generated (external) · {asset.provenance.tool_name}</span>}
                            {asset.provenance?.library && <span className="text-fg-2">from {asset.provenance.library.pack_name}{asset.provenance.library.credit ? ` · credit: ${asset.provenance.library.credit}` : ''}</span>}
                          </li>
                        ))}
                      </ul>
                    )}
                    {bucket.id === 'asset' && (
                      <label className="block text-xs text-fg-2">Made with an AI tool? Name it so the files are labelled (leave empty for your own files)
                        <input className={`${field} mt-1 py-1.5`} placeholder="e.g. Google Flow" maxLength={100} value={toolName} onChange={(e) => setToolName(e.target.value)} />
                      </label>
                    )}
                    {bucket.id === 'main' && (
                      <button className="text-xs text-accent hover:underline disabled:opacity-40" disabled={modelState === 'installing'} onClick={installModel}>
                        {modelState === 'installing' ? 'Installing speech model…' : 'Install speech model (once, for footage with talking)'}
                      </button>
                    )}
                  </div>
                );
              })}
            </section>

            <SoundLibraryPanel disabled={running} onNotice={setNotice} />

            <TransitionsPanel disabled={running} onNotice={setNotice} />

            <section className={`${panel} space-y-2`}>
              <label htmlFor="instructions" className="font-semibold block">Instructions <span className="text-xs font-normal text-fg-3">applied to every prompt · optional</span></label>
              <textarea id="instructions" className={field} rows={3} maxLength={20000} placeholder="Standing rules, e.g. 9:16 vertical, under 45 seconds, keep my logo at the end, no music." value={instructions} onChange={(e) => setInstructions(e.target.value)} onBlur={() => void saveInstructions().catch(() => undefined)} />
            </section>

            <section className={`${panel} space-y-2`}>
              <div role="radiogroup" aria-label="Claude stage" className="grid grid-cols-3 gap-1 rounded-lg border border-line bg-app p-1">
                {STAGES.map((item) => (
                  <button key={item.id} type="button" role="radio" aria-checked={stage === item.id} disabled={running}
                    onClick={() => projectId && chooseStage(projectId, item.id)}
                    className={`rounded-md px-2 py-1.5 text-left disabled:opacity-60 ${stage === item.id ? 'bg-accent text-on-accent' : 'text-fg-2 hover:bg-elevated'}`}>
                    <span className="block text-sm font-semibold">{item.label}</span>
                    <span className={`block text-xs ${stage === item.id ? 'opacity-90' : 'text-fg-3'}`}>{item.model}</span>
                  </button>
                ))}
              </div>
              <p className="text-xs text-fg-3">{currentStage.hint}</p>
              <label htmlFor="prompt" className="font-semibold block">Prompt</label>
              <textarea id="prompt" className={field} rows={4} maxLength={20000} placeholder={hasMain ? 'Tell Claude what to make, e.g. "Cut this into a punchy 30 second teaser, start with the strongest line."' : 'Add your main footage first, then tell Claude what to make.'} value={prompt} onChange={(e) => setPrompt(e.target.value)}
                onKeyDown={(e) => { if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) { e.preventDefault(); void send(); } }} />
              <div className="flex flex-wrap items-center gap-2">
                {running
                  ? <button className={button} onClick={() => void engine.stopAgent()}><Square className="w-4 h-4" /> Stop</button>
                  : <button className={primary} disabled={(!prompt.trim() && !currentStage.fallbackPrompt) || claudeMissing} onClick={() => void send()}><Send className="w-4 h-4" /> Send to {currentStage.label}</button>}
                <span className="text-xs text-fg-3">Ctrl+Enter to send</span>
                <button className="ml-auto text-xs text-fg-3 hover:text-fg disabled:opacity-40" disabled={running || output.length === 0} onClick={newConversation}>New conversation</button>
              </div>
              {claudeMissing && <p className="text-xs text-warning">Claude Code isn't installed. Install it from claude.com/claude-code and sign in once in a terminal, then restart AgenEditor.</p>}
              <p className="text-xs text-fg-3">Claude Code edits through AgenEditor's tools only. It sends your prompt, filenames, file details and transcripts to Anthropic; your video files are not uploaded.</p>
            </section>
          </div>

          <section aria-labelledby="output-title" className={`${panel} flex min-h-[28rem] flex-col gap-3 lg:overflow-hidden`}>
            <div className="flex items-center justify-between gap-2">
              <h2 id="output-title" className="font-semibold flex items-center gap-2"><Sparkles className="w-4 h-4 text-accent" /> Output</h2>
              <span className="text-xs text-fg-3">Revision {revision}{activeJobs.length > 0 ? ` · ${activeJobs.map((job) => `${job.kind === 'final_render' ? 'final' : job.kind === 'preview_render' ? 'preview' : job.kind} ${Math.round(job.progress * 100)}%`).join(', ')}` : ''}</span>
            </div>

            <div className="aspect-video w-full shrink-0 overflow-hidden rounded-lg bg-black flex items-center justify-center">
              {previewUrl ? <video key={previewUrl} className="h-full w-full" controls src={previewUrl} /> : <p className="text-sm text-fg-3 px-4 text-center">The preview appears here after Claude renders an edit.</p>}
            </div>
            {latestPreview && <p className="text-xs text-fg-3 -mt-1">Preview of revision {latestPreview.timeline_revision}{latestPreview.timeline_revision !== revision ? ` · newer revision ${revision} not rendered yet` : ''}</p>}

            <ol data-testid="output-log" aria-label="Conversation" className="flex-1 min-h-[8rem] space-y-2 overflow-y-auto text-sm">
              {output.length === 0 && <li className="text-fg-3">Claude's replies show up here.</li>}
              {output.map((entry, index) => <OutputLine key={index} entry={entry} />)}
              {running && <li className="flex items-center gap-2 text-fg-3"><Loader2 className="w-4 h-4 animate-spin" /> Claude is working…</li>}
            </ol>

            <div className="flex flex-wrap gap-2 border-t border-line pt-3">
              <button className={button} disabled={busy || running || revision === 0} onClick={renderFinal}>Render final MP4</button>
              <button className={primary} disabled={busy || !latestFinal} onClick={checkAndSave}><Download className="w-4 h-4" /> Check &amp; save MP4…</button>
            </div>
            {qa && latestFinal && qa.jobId === latestFinal.id && (
              <div className="text-xs">
                <p className={qa.report.overall === 'pass' ? 'text-success' : qa.report.overall === 'fail' ? 'text-danger' : 'text-warning'}>Technical check: {qa.report.overall}</p>
                <ul className="text-fg-2">{qa.report.checks.filter((check) => check.status !== 'pass').map((check) => <li key={check.check_id}>{check.check_id}: {check.measured ?? check.message}</li>)}</ul>
              </div>
            )}
          </section>
        </main>
      )}
    </div>
  );
};

const OutputLine: React.FC<{ entry: OutputEntry }> = ({ entry }) => {
  switch (entry.kind) {
    case 'prompt':
      return (
        <li className="ml-8 rounded-lg bg-accent/15 px-3 py-2">
          {entry.stage && <span className="mb-1 block text-xs font-semibold text-fg-2">{stageInfo(entry.stage).label} · {stageInfo(entry.stage).model}</span>}
          <span className="whitespace-pre-wrap">{entry.text}</span>
        </li>
      );
    case 'text':
      return <li className="mr-8 rounded-lg bg-app px-3 py-2"><ClaudeMarkdown text={entry.text} /></li>;
    case 'tool':
      return <li className="flex items-center gap-1.5 text-xs text-fg-3"><Wrench className="w-3 h-3" /> {TOOL_LABELS[entry.name] ?? entry.name}</li>;
    case 'status':
      return <li className={`text-xs ${entry.tone === 'ok' ? 'text-success' : entry.tone === 'warn' ? 'text-warning' : 'text-danger'}`}>{entry.text}</li>;
  }
};
