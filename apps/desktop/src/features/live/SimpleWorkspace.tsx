import React, { useCallback, useEffect, useRef, useState } from 'react';
import {
  ArrowRight, Check, ChevronDown, Download, Eye, Factory, Film, Folder, House, Layers, Loader2, Lock, Mic, Music2, PenLine, Play, Plus,
  ShieldCheck, Sparkles, Square, Upload, Wand2, Wrench, X,
} from 'lucide-react';
import {
  liveEngineRepository as engine,
  type AgentEvent,
  type AgentStage,
  type EngineAsset,
  type EngineAssetChecklist,
  type EngineBrief,
  type EngineConnection,
  type EngineJob,
  type EngineProject,
  type EngineQA,
  type ImportBucket,
  type ImportOutcome,
} from '../../services/liveEngineRepository';
import { ClaudeMarkdown } from '../../components/common/ClaudeMarkdown';
import { BrandMark } from '../../components/common/Brand';
import { Badge, Button, IconButton, Kbd } from '../../components/ui/primitives';
import { engineErrorText } from '../../utils/engineError';
import { isStoryboard, mergePlanRequests, reviewProgress, reviewState } from '../../utils/assetChecklist';
import { parseAssetRequests, type AssetRequest } from '../../utils/assetRequests';
import { BrollPanel, toolFor } from './BrollPanel';
import { HomeView } from './HomeView';
import { SetupPanel } from './SetupPanel';
import { UsageBadge } from './UsageBadge';
import { SectionLabel } from './SectionLabel';
import { SoundLibraryPanel } from './SoundLibraryPanel';
import { TransitionsPanel } from './TransitionsPanel';
import { ProductionWorkspace } from '../production/ProductionWorkspace';
import { liveProductionRepository } from '../../services/productionRepository';

const field = 'w-full rounded-lg border border-line bg-app px-3 py-2 text-sm text-fg outline-none placeholder:text-fg-3 focus:border-accent';
const card = 'rounded-xl border border-line bg-surface';

const errorText = engineErrorText;

/** One line in the Claude conversation. */
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
  { id: 'main', title: 'Main footage', hint: 'The video you want edited. Add several at once.', icon: <Film className="h-5 w-5" />, roles: ['primary'] },
  { id: 'reference', title: 'References', hint: 'Style or pacing examples. Never cut into your video.', icon: <Eye className="h-5 w-5" />, roles: ['reference'] },
  { id: 'asset', title: 'Assets', hint: 'B-roll, images, logos and music Claude may use.', icon: <Layers className="h-5 w-5" />, roles: ['broll', 'logo', 'music', 'sfx'] },
];
const ROLE_LABELS: Record<EngineAsset['role'], string> = {
  primary: 'Main footage', broll: 'B-roll', music: 'Music', logo: 'Logo', subtitles: 'Subtitles', reference: 'Reference', sfx: 'Sound effect',
};

const SUGGESTIONS = [
  'Cut this into a 30-second 9:16 highlight',
  'Remove silences and filler words',
  'Match the pacing of my reference clip',
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
  run_qa: 'Running the technical check',
  list_transitions: 'Checking transition styles',
  list_sound_effects: 'Searching sound effects',
  add_sound_effect: 'Adding a sound effect',
};

export const SimpleWorkspace: React.FC = () => {
  const [projects, setProjects] = useState<EngineProject[] | null>(null);
  const [projectId, setProjectId] = useState<string | null>(null);
  // The multi-clip Production workspace (spec §16) replaces Home/project while open.
  const [production, setProduction] = useState(false);
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
  const promptRef = useRef<HTMLTextAreaElement>(null);
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
  // Offered until the cache check says the model is there; a failed check keeps the offer visible.
  const [speechInstalled, setSpeechInstalled] = useState(false);
  // Remove asks for a second click; `removing` is the asset being removed right now.
  const [confirmRemove, setConfirmRemove] = useState<string | null>(null);
  const [removing, setRemoving] = useState<string | null>(null);
  // B-roll requests from the plan (made in ChatGPT / Google Flow) and the one being attached now.
  const [checklist, setChecklist] = useState<EngineAssetChecklist | null>(null);
  const [attaching, setAttaching] = useState<string | null>(null);
  const [polishing, setPolishing] = useState(false);
  const [polished, setPolished] = useState<{ from: string; text: string } | null>(null);
  const [stylesOn, setStylesOn] = useState({ transitions: 0, sounds: 0 });
  const onTransitionsOn = useCallback((count: number) => setStylesOn((on) => ({ ...on, transitions: count })), []);
  const onSoundsOn = useCallback((count: number) => setStylesOn((on) => ({ ...on, sounds: count })), []);

  const output = projectId ? outputs[projectId] ?? [] : [];
  const stage: AgentStage = (projectId && stages[projectId]) || (revision === 0 ? 'plan' : 'execute');
  const currentStage = stageInfo(stage);
  const chooseStage = (id: string, next: AgentStage) => setStages((all) => ({ ...all, [id]: next }));
  const append = useCallback((id: string, ...entries: OutputEntry[]) => {
    setOutputs((all) => ({ ...all, [id]: [...(all[id] ?? []), ...entries] }));
  }, []);

  const refreshLibrary = useCallback(async () => {
    const list = await engine.listProjects();
    // Only the list changes here; which project is open is decided by the person (open, create, Home).
    setProjects(list);
  }, []);

  const refreshProject = useCallback(async (id: string) => {
    const [summary, timeline, storedBrief, projectJobs, storedChecklist] = await Promise.all([
      engine.summary(id), engine.timeline(id), engine.getBrief(id), engine.listJobs(id), engine.getChecklist(id),
    ]);
    if (id !== currentProjectId.current) return;
    setAssets(summary.assets);
    setRevision(timeline.revision);
    setBrief(storedBrief);
    setInstructions(storedBrief.brief_text);
    setJobs(projectJobs);
    setChecklist(storedChecklist);
  }, []);

  useEffect(() => {
    void refreshLibrary().catch((error) => setNotice(errorText(error)));
    void engine.connection().then(setConnection).catch((error) => setNotice(errorText(error)));
    void engine.speechModelStatus().then((status) => setSpeechInstalled(status.tiny)).catch(() => undefined);
  }, [refreshLibrary]);

  useEffect(() => {
    setAssets([]);
    setBrief(null);
    setChecklist(null);
    setJobs([]);
    setPreviewUrl(null);
    shownPreview.current = null;
    setQA(null);
    setPolished(null);
    setConfirmRemove(null);
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

  // Keep the newest conversation line in view.
  const logEnd = useRef<HTMLLIElement>(null);
  useEffect(() => { logEnd.current?.scrollIntoView?.({ block: 'end' }); }, [output.length, running]);

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
    const target = document.elementFromPoint(drop.x, drop.y);
    const requestId = target?.closest<HTMLElement>('[data-request]')?.dataset.request;
    const request = requestId ? checklist?.requests.find((item) => item.id === requestId) : undefined;
    if (request) { void attachToRequest(request, true); return; }
    const box = target?.closest<HTMLElement>('[data-bucket]');
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

  const refreshConnection = useCallback(() => {
    void engine.connection().then(setConnection).catch(() => undefined);
  }, []);

  const openProject = (id: string) => {
    setCreating(false);
    setNotice(null);
    setProduction(false);
    setProjectId(id);
  };

  const goHome = () => {
    if (running) return;
    setCreating(false);
    setNotice(null);
    setProduction(false);
    setProjectId(null);
    // Revisions may have changed while the project was open.
    void refreshLibrary().catch((error) => setNotice(errorText(error)));
  };

  const createProject = () => {
    const name = newName.trim();
    if (!name) return;
    setBusy(true);
    void engine.createProject(name, 60 * 30)
      .then(async (created) => { await refreshLibrary(); setProjectId(created.project_id); setNewName(''); setCreating(false); })
      .catch((error) => setNotice(errorText(error)))
      .finally(() => setBusy(false));
  };

  const saveInstructions = async (text = instructions) => {
    if (!projectId || !brief || text === brief.brief_text) return;
    try {
      setBrief(await engine.saveBrief(projectId, {
        brief_text: text, script_text: brief.script_text, reference_note: brief.reference_note, checklist: brief.checklist,
      }, brief.version));
    } catch (error) {
      setNotice(`Instructions not saved: ${errorText(error)}`);
      throw error;
    }
  };

  /**
   * Imports a file picked or dropped for a B-roll request, labels it AI-generated (external) with the
   * tool the request calls for, and links it to that request. The person then reviews and approves it.
   */
  const attachToRequest = async (request: AssetRequest, dropped: boolean) => {
    if (!projectId || !checklist || attaching || running) return;
    if (reviewState(checklist, request.id) === 'locked') {
      setNotice('Approve (or skip) the storyboard first, then add this video.');
      return;
    }
    const id = projectId;
    setAttaching(request.id);
    setNotice(null);
    let imported: EngineAsset | null = null;
    try {
      const role = isStoryboard(request.id) ? 'reference' : 'broll';
      imported = await (dropped ? engine.importDroppedMedia(id, role, toolFor(request)) : engine.importMedia(id, role, toolFor(request)));
      if (!imported) return; // picker cancelled
      if (imported.kind !== request.kind) {
        throw new Error(`${imported.original_filename} is a ${imported.kind === 'audio' ? 'sound file' : imported.kind}, but this item needs ${request.kind === 'image' ? 'an image' : 'a video'}.`);
      }
      let list = await engine.getChecklist(id);
      const previous = list.links[request.id];
      // Replacing is one engine step, so a refused file never costs the person the approved one.
      list = previous
        ? await engine.replaceChecklistAsset(id, request.id, imported.id, list.version)
        : await engine.linkChecklistAsset(id, request.id, imported.id, list.version);
      if (previous && previous !== 'dropped') void engine.removeAsset(id, previous).catch(() => undefined);
      const added = imported;
      imported = null;
      if (currentProjectId.current === id) setChecklist(list);
      setNotice(`Added ${added.original_filename}. Look it over on its card, then approve it.`);
    } catch (error) {
      // Do not leave a rejected file behind in the project.
      if (imported) void engine.removeAsset(id, imported.id).catch(() => undefined);
      setNotice(errorText(error));
    } finally {
      setAttaching(null);
      if (currentProjectId.current === id) void refreshProject(id).catch(() => undefined);
    }
  };

  /** When a Plan reply lists B-roll, save it as the project's to-do list; the Execute lock follows from it. */
  const adoptPlanRequests = async (id: string, text: string | null) => {
    if (!text) return;
    const hasBlock = text.includes('"asset_requests"');
    const saysNone = /no extra b-?roll needed/i.test(text);
    if (!hasBlock && !saysNone) return;
    let requests: AssetRequest[] = [];
    if (hasBlock) {
      const parsed = parseAssetRequests(text);
      if (!parsed.ok) {
        append(id, { kind: 'status', tone: 'warn', text: `The plan's B-roll list could not be read (${parsed.error}) Ask Claude to fix it.` });
        return;
      }
      requests = parsed.requests;
    }
    const current = await engine.getChecklist(id);
    if (requests.length === 0) {
      // The new plan needs no B-roll: drop an older list so it cannot keep Execute locked.
      if (current.configured && current.requests.length > 0) {
        const cleared = await engine.saveChecklist(id, null, current.version);
        if (currentProjectId.current === id) setChecklist(cleared);
        append(id, { kind: 'status', tone: 'ok', text: 'The new plan needs no B-roll, so the earlier B-roll list was cleared. Files you already added stay in your Assets.' });
      }
      return;
    }
    const saved = await engine.saveChecklist(id, mergePlanRequests(current.configured ? current : null, requests), current.version);
    if (currentProjectId.current === id) setChecklist(saved);
    const { done, total } = reviewProgress(saved);
    append(id, { kind: 'status', tone: 'ok', text: `The plan lists ${total} B-roll item${total === 1 ? '' : 's'} (${done} ready). Make them in ChatGPT and Google Flow from "B-roll to make", then approve each one before Execute.` });
  };

  const send = async () => {
    const runStage = stage;
    const text = prompt.trim() || currentStage.fallbackPrompt || '';
    if (!projectId || !text || running || executeLocked) return;
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
        if (runStage === 'plan') await adoptPlanRequests(id, result.text).catch((error) => append(id, { kind: 'status', tone: 'error', text: `B-roll list not saved: ${errorText(error)}` }));
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

  const removeAsset = (asset: EngineAsset) => {
    if (!projectId) return;
    if (confirmRemove !== asset.id) { setConfirmRemove(asset.id); return; }
    const id = projectId;
    setConfirmRemove(null);
    setRemoving(asset.id);
    setNotice(null);
    void engine.removeAsset(id, asset.id)
      .then(async () => {
        await refreshProject(id);
        setNotice(`Removed ${asset.original_filename} from this project. Your own file is untouched, and earlier revisions that used it still play.`);
      })
      .catch((error) => setNotice(`${asset.original_filename} was not removed: ${errorText(error)}`))
      .finally(() => setRemoving(null));
  };

  const polishInstructions = () => {
    const from = instructions.trim();
    if (!from || polishing) return;
    setPolishing(true);
    setPolished(null);
    setNotice(null);
    void engine.polishInstructions(from)
      .then((text) => setPolished({ from, text }))
      .catch((error) => setNotice(errorText(error)))
      .finally(() => setPolishing(false));
  };

  const acceptPolished = () => {
    if (!polished) return;
    setInstructions(polished.text);
    setPolished(null);
    void saveInstructions(polished.text).catch(() => undefined);
  };

  const applySuggestion = (text: string) => {
    setPrompt(text);
    promptRef.current?.focus();
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
    void engine.cacheModel('tiny').then((message) => { setSpeechInstalled(true); setNotice(`Speech model ready (${message}).`); })
      .catch((error) => setNotice(errorText(error))).finally(() => setModelState('idle'));
  };

  const hasMain = assets.some((asset) => asset.role === 'primary');
  const claudeMissing = connection?.claude_available === false;
  const brollProgress = reviewProgress(checklist?.configured ? checklist : null);
  const executeLocked = stage === 'execute' && !brollProgress.complete;
  const steps = [
    { title: 'Add main footage', text: 'The video Claude will cut.', done: hasMain },
    { title: 'Choose styles', text: stylesOn.transitions + stylesOn.sounds > 0 ? 'Transitions and sounds are set.' : 'Tick transition styles or sound packs.', done: stylesOn.transitions + stylesOn.sounds > 0 },
    { title: 'Describe the edit', text: 'Tell Claude what to make.', done: output.length > 0 || revision > 0 },
  ];
  const stepsDone = steps.filter((step) => step.done).length;
  const jobSummary = activeJobs.map((job) => `${job.kind === 'final_render' ? 'final' : job.kind === 'preview_render' ? 'preview' : job.kind} ${Math.round(job.progress * 100)}%`).join(', ');

  return (
    <div className="flex h-screen flex-col overflow-hidden bg-app text-fg">
      <header className="flex h-16 shrink-0 items-center gap-3 border-b border-line bg-sidebar px-5">
        <button type="button" className="flex items-center gap-2.5 rounded-lg disabled:cursor-default" onClick={goHome} disabled={running || !projectId} title={projectId ? 'Home' : undefined} aria-label="AgenEditor home">
          <BrandMark size={30} />
          <span className="text-lg font-semibold tracking-tight">AgenEditor</span>
          <Badge tone="accent">Alpha</Badge>
        </button>
        {projectId && <>
        <span aria-hidden className="mx-1 h-7 w-px bg-line" />
        <Button variant="ghost" size="lg" icon={<House className="h-4 w-4" />} onClick={goHome} disabled={running} title={running ? 'Claude is working; wait or press Stop first' : undefined}>Home</Button>
        <div className="relative">
          <Folder aria-hidden className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-fg-3" />
          <select aria-label="Project" className="h-9 max-w-[16rem] appearance-none rounded-lg border border-line bg-surface pl-9 pr-8 text-sm font-medium text-fg outline-none focus:border-accent disabled:opacity-60" value={projectId ?? ''} onChange={(e) => setProjectId(e.target.value || null)} disabled={running}>
            {(projects ?? []).map((project) => <option key={project.id} value={project.id}>{project.name}</option>)}
          </select>
          <ChevronDown aria-hidden className="pointer-events-none absolute right-2.5 top-1/2 h-4 w-4 -translate-y-1/2 text-fg-3" />
        </div>
        {creating ? (
          <form className="flex items-center gap-2" onSubmit={(e) => { e.preventDefault(); createProject(); }}>
            <input autoFocus aria-label="New project name" className={`${field} h-9 w-48 py-0`} placeholder="Project name" value={newName} maxLength={100} onChange={(e) => setNewName(e.target.value)} />
            <Button variant="primary" size="lg" type="submit" disabled={busy || !newName.trim()}>Create</Button>
            <Button size="lg" onClick={() => setCreating(false)}>Cancel</Button>
          </form>
        ) : <Button variant="ghost" size="lg" icon={<Plus className="h-4 w-4" />} onClick={() => setCreating(true)} disabled={running}>New project</Button>}
        </>}
        <Button
          variant={production ? 'secondary' : 'ghost'}
          size="lg"
          icon={<Factory className="h-4 w-4" />}
          aria-pressed={production}
          onClick={() => (production ? goHome() : setProduction(true))}
          disabled={running}
          title={running ? 'Claude is working; wait or press Stop first' : 'Batches of clips with AI production teams'}
        >
          Production
        </Button>
        <span className="ml-auto" />
        <UsageBadge />
        <span className="inline-flex shrink-0 items-center gap-2 rounded-full border border-line bg-surface px-3.5 py-1.5 text-xs">
          <span aria-hidden className={`h-2 w-2 rounded-full ${connection?.connected ? 'bg-success' : 'bg-warning animate-pulse'}`} />
          <span className={connection?.connected ? 'text-success' : 'text-warning'}>{connection?.connected ? 'Engine connected' : 'Engine starting…'}</span>
          <span aria-hidden className="text-fg-3">·</span>
          <Lock aria-hidden className="h-3.5 w-3.5 text-fg-3" />
          <span className="text-fg-2">Files stay on this computer</span>
        </span>
      </header>

      {notice && (
        <div role="status" className="mx-5 mt-3 flex items-start gap-3 whitespace-pre-line break-words rounded-lg border border-accent/40 bg-accent/10 px-4 py-2 text-sm">
          <span className="min-w-0 flex-1">{notice}</span>
          <button type="button" className="text-xs text-fg-3 hover:text-fg" onClick={() => setNotice(null)}>Dismiss</button>
        </div>
      )}

      {production ? (
        <ProductionWorkspace repository={liveProductionRepository} projects={(projects ?? []).map(({ id, name }) => ({ id, name }))} />
      ) : !projectId ? (
        <HomeView
          projects={projects}
          creating={creating}
          newName={newName}
          busy={busy}
          onNewName={setNewName}
          onStartCreate={() => setCreating(true)}
          onCancelCreate={() => { setCreating(false); setNewName(''); }}
          onCreate={createProject}
          onOpen={openProject}
          setup={<SetupPanel engineConnected={Boolean(connection?.connected)} onChanged={refreshConnection} />}
        />
      ) : (
        <div className="grid min-h-0 flex-1 grid-cols-[18.5rem_minmax(0,1fr)_21rem] xl:grid-cols-[21rem_minmax(0,1fr)_24rem]">
          {/* ---- Left: media and styles ---------------------------------------------------- */}
          <aside aria-label="Media and styles" className="min-h-0 space-y-7 overflow-y-auto overflow-x-hidden border-r border-line bg-sidebar px-4 py-5">
            <section aria-labelledby="media-title" className="space-y-2.5">
              <SectionLabel id="media-title" title="Media" meta="Drop files onto a box" />
              {BUCKETS.map((bucket) => {
                const files = assets.filter((asset) => bucket.roles.includes(asset.role));
                return (
                  <div key={bucket.id} data-bucket={bucket.id} data-testid={`bucket-${bucket.id}`} className={`${card} space-y-3 border-dashed p-3.5`}>
                    <div className="flex items-start gap-3">
                      <span aria-hidden className="flex h-10 w-10 shrink-0 items-center justify-center rounded-lg bg-accent/12 text-accent">{bucket.icon}</span>
                      <div className="min-w-0 flex-1">
                        <h3 className="flex flex-wrap items-center gap-x-2 gap-y-1 font-semibold">
                          {bucket.title}
                          {bucket.id === 'main' && files.length === 0 && <Badge tone="warning">Required</Badge>}
                          {files.length > 0 && <Badge>{files.length}</Badge>}
                        </h3>
                        <p className="mt-0.5 text-xs leading-snug text-fg-3">{bucket.hint}</p>
                      </div>
                      <Button className="shrink-0" disabled={importing !== null || running} onClick={() => importInto(bucket.id, false)}
                        icon={importing === bucket.id ? <Loader2 className="h-4 w-4 animate-spin" /> : <Upload className="h-4 w-4" />}>Browse</Button>
                    </div>
                    {files.length > 0 && (
                      <ul className="space-y-1.5" aria-label={`${bucket.title} files`}>
                        {files.map((asset) => (
                          <li key={asset.id} className={`flex items-center gap-2.5 rounded-lg border bg-app px-2.5 py-2 ${confirmRemove === asset.id ? 'border-danger/50' : 'border-line'}`}>
                            <span aria-hidden className="flex h-8 w-8 shrink-0 items-center justify-center rounded-md bg-elevated text-fg-3">
                              {asset.video ? <Play className="h-3.5 w-3.5" /> : <Music2 className="h-3.5 w-3.5" />}
                            </span>
                            <div className="min-w-0 flex-1">
                              <p className="truncate text-sm font-medium" title={asset.original_filename}>{asset.original_filename}</p>
                              <p className="text-xs text-fg-3">{asset.video ? `${asset.video.width} × ${asset.video.height}` : 'Audio'} · {ROLE_LABELS[asset.role]}</p>
                              {asset.provenance?.origin === 'generated-external' && <p className="text-xs text-warning">AI-generated (external) · {asset.provenance.tool_name}</p>}
                              {asset.provenance?.library && <p className="text-xs text-fg-2">from {asset.provenance.library.pack_name}{asset.provenance.library.credit ? ` · credit: ${asset.provenance.library.credit}` : ''}</p>}
                            </div>
                            {removing === asset.id
                              ? <Loader2 aria-label={`Removing ${asset.original_filename}`} className="h-4 w-4 shrink-0 animate-spin text-fg-3" />
                              : confirmRemove === asset.id
                                ? <Button variant="danger" size="sm" className="shrink-0" onClick={() => removeAsset(asset)} onBlur={() => setConfirmRemove(null)} autoFocus>Remove?</Button>
                                : (
                                  <IconButton label={`Remove ${asset.original_filename}`} size="sm" className="shrink-0 hover:text-danger" disabled={running || removing !== null} onClick={() => removeAsset(asset)}>
                                    <X className="h-4 w-4" />
                                  </IconButton>
                                )}
                          </li>
                        ))}
                      </ul>
                    )}
                    {bucket.id === 'main' && !speechInstalled && (
                      <button type="button" className="flex items-start gap-1.5 text-left text-xs font-medium leading-snug text-accent hover:underline disabled:opacity-40 [&>svg]:mt-px [&>svg]:shrink-0" disabled={modelState === 'installing'} onClick={installModel}>
                        {modelState === 'installing' ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Mic className="h-3.5 w-3.5" />}
                        {modelState === 'installing' ? 'Installing speech model…' : 'Install speech model · one-time, for footage with talking'}
                      </button>
                    )}
                    {bucket.id === 'asset' && (
                      <label className="block text-xs font-medium text-fg-2">Made with an AI tool? <span className="font-normal text-fg-3">Optional</span>
                        <input className={`${field} mt-1.5`} placeholder="e.g. Google Flow" maxLength={100} value={toolName} onChange={(e) => setToolName(e.target.value)} />
                        <span className="mt-1 block font-normal text-fg-3">Labels these files. Leave empty for your own footage.</span>
                      </label>
                    )}
                  </div>
                );
              })}
            </section>

            <TransitionsPanel disabled={running} onNotice={setNotice} onActiveCount={onTransitionsOn} />

            <SoundLibraryPanel disabled={running} onNotice={setNotice} onActiveCount={onSoundsOn} />

            <section className="space-y-2.5">
              <div className="flex items-baseline justify-between gap-3">
                <label htmlFor="instructions" className="text-xs font-semibold uppercase tracking-[0.08em] text-fg-2">Instructions</label>
                <span className="text-xs text-fg-3">optional · used for every prompt</span>
              </div>
              <textarea id="instructions" className={field} rows={4} maxLength={20000} placeholder="Jot down rules in any order, e.g. vertical, 45s max, no music, big yellow captions, logo at the end. Polish tidies them up." value={instructions} onChange={(e) => setInstructions(e.target.value)} onBlur={() => void saveInstructions().catch(() => undefined)} />
              <div className="flex items-center gap-2">
                <Button size="md" icon={polishing ? <Loader2 className="h-4 w-4 animate-spin" /> : <Wand2 className="h-4 w-4" />} disabled={!instructions.trim() || polishing || claudeMissing} onClick={polishInstructions}>
                  {polishing ? 'Polishing…' : 'Polish with Opus'}
                </Button>
                <span className="min-w-0 text-xs text-fg-3">Opus 5.5 · high turns rough notes into clear rules</span>
              </div>
              {polished && (
                <div role="region" aria-label="Polished instructions" className="space-y-2.5 rounded-xl border border-accent/50 bg-accent/8 p-3">
                  <p className="flex items-center gap-1.5 text-xs font-semibold text-accent"><Sparkles className="h-3.5 w-3.5" /> Recommended by Opus 5.5</p>
                  <p className="whitespace-pre-wrap text-sm text-fg">{polished.text}</p>
                  {polished.from !== instructions.trim() && <p className="text-xs text-warning">You changed your notes since polishing; using this replaces them.</p>}
                  <div className="flex gap-2">
                    <Button variant="primary" size="md" icon={<Check className="h-4 w-4" />} onClick={acceptPolished}>Use this</Button>
                    <Button size="md" onClick={() => setPolished(null)}>Keep mine</Button>
                  </div>
                  <p className="text-xs text-fg-3">Your notes are sent to Anthropic to polish. Nothing changes until you choose Use this.</p>
                </div>
              )}
            </section>
          </aside>

          {/* ---- Centre: output ------------------------------------------------------------- */}
          <main aria-labelledby="output-title" className="min-h-0 space-y-5 overflow-y-auto overflow-x-hidden px-6 py-5">
            <div className="flex flex-wrap items-center gap-x-3 gap-y-2">
              <h2 id="output-title" className="text-lg font-semibold">Output</h2>
              <Badge className="px-2 py-1 text-xs">Revision {revision}</Badge>
              {jobSummary && <span className="inline-flex items-center gap-1.5 text-xs text-fg-3"><Loader2 className="h-3.5 w-3.5 animate-spin" /> {jobSummary}</span>}
              <div className="ml-auto flex flex-wrap items-center justify-end gap-2">
                {revision === 0 && <span className="text-xs text-fg-3">Exports unlock after the first edit</span>}
                <Button size="lg" icon={<ShieldCheck className="h-4 w-4" />} disabled={busy || !latestFinal} onClick={checkAndSave}>Check &amp; save MP4…</Button>
                <Button variant="primary" size="lg" icon={<Download className="h-4 w-4" />} disabled={busy || running || revision === 0} onClick={renderFinal}>Render final MP4</Button>
              </div>
            </div>

            <div className="flex aspect-video w-full items-center justify-center overflow-hidden rounded-2xl border border-line bg-black">
              {previewUrl ? <video key={previewUrl} className="h-full w-full" controls src={previewUrl} /> : (
                <div className="space-y-3 px-6 text-center">
                  <span aria-hidden className="mx-auto flex h-14 w-14 items-center justify-center rounded-full border border-line bg-surface text-fg-2"><Play className="ml-0.5 h-5 w-5" /></span>
                  <p className="font-semibold text-fg">Your edit will play here</p>
                  <p className="mx-auto max-w-sm text-sm text-fg-3">Claude renders a preview after each edit, so you can review before exporting.</p>
                </div>
              )}
            </div>
            {latestPreview && <p className="-mt-2 text-xs text-fg-3">Preview of revision {latestPreview.timeline_revision}{latestPreview.timeline_revision !== revision ? ` · newer revision ${revision} not rendered yet` : ''}</p>}

            {qa && latestFinal && qa.jobId === latestFinal.id && (
              <div className={`${card} p-4 text-sm`}>
                <p className={`font-semibold ${qa.report.overall === 'pass' ? 'text-success' : qa.report.overall === 'fail' ? 'text-danger' : 'text-warning'}`}>Technical check: {qa.report.overall}</p>
                <ul className="mt-1 text-xs text-fg-2">{qa.report.checks.filter((check) => check.status !== 'pass').map((check) => <li key={check.check_id}>{check.check_id}: {check.measured ?? check.message}</li>)}</ul>
              </div>
            )}

            {projectId && checklist?.configured && checklist.requests.length > 0 && (
              <BrollPanel projectId={projectId} checklist={checklist} assets={assets} disabled={running} attaching={attaching}
                onBrowse={(request) => void attachToRequest(request, false)} onChange={setChecklist} onNotice={setNotice} />
            )}

            {revision === 0 && (
              <section aria-labelledby="get-started-title" className={`${card} space-y-4 p-5`}>
                <div className="flex items-center justify-between gap-3">
                  <h3 id="get-started-title" className="font-semibold">Get started</h3>
                  <span className="text-xs text-fg-3">{stepsDone} of {steps.length} done</span>
                </div>
                <div className="h-1.5 overflow-hidden rounded-full bg-elevated" role="progressbar" aria-label="Setup progress" aria-valuemin={0} aria-valuemax={steps.length} aria-valuenow={stepsDone}>
                  <div className="h-full rounded-full bg-accent transition-all" style={{ width: `${(stepsDone / steps.length) * 100}%` }} />
                </div>
                <ol className="grid grid-cols-1 gap-3 lg:grid-cols-3">
                  {steps.map((step, index) => (
                    <li key={step.title} className="flex items-start gap-3 rounded-xl border border-line bg-app/50 p-3.5">
                      <span aria-hidden className={`flex h-7 w-7 shrink-0 items-center justify-center rounded-full text-xs font-semibold ${step.done ? 'bg-success text-on-accent' : 'border border-accent/60 text-accent'}`}>
                        {step.done ? <Check className="h-4 w-4" strokeWidth={3} /> : index + 1}
                      </span>
                      <div className="min-w-0">
                        <p className="font-semibold">{step.title}<span className="sr-only">{step.done ? ' (done)' : ''}</span></p>
                        <p className="text-xs text-fg-3">{step.text}</p>
                      </div>
                    </li>
                  ))}
                </ol>
              </section>
            )}
          </main>

          {/* ---- Right: Claude -------------------------------------------------------------- */}
          <aside aria-label="Claude" className="flex min-h-0 flex-col border-l border-line bg-sidebar">
            <div className="flex shrink-0 items-center justify-between gap-2 border-b border-line px-4 py-3">
              <h2 className="flex items-center gap-2 font-semibold"><Sparkles className="h-4 w-4 text-accent" /> Claude</h2>
              <Button variant="ghost" icon={<PenLine className="h-4 w-4" />} disabled={running || output.length === 0} onClick={newConversation}>New conversation</Button>
            </div>

            <ol data-testid="output-log" aria-label="Conversation" className="flex min-h-0 flex-1 flex-col gap-2.5 overflow-y-auto overflow-x-hidden px-4 py-4 text-sm">
              {output.length === 0 && (
                <li className="mt-auto space-y-2.5">
                  <p className="text-fg-2">Claude's replies show up here. Not sure where to start? Try one:</p>
                  {SUGGESTIONS.map((text) => (
                    <button key={text} type="button" className="block w-full rounded-xl border border-line bg-surface px-3.5 py-2.5 text-left text-sm text-fg hover:border-accent/60 hover:bg-elevated disabled:opacity-50" disabled={running} onClick={() => applySuggestion(text)}>{text}</button>
                  ))}
                </li>
              )}
              {output.map((entry, index) => <OutputLine key={index} entry={entry} />)}
              {running && <li className="flex items-center gap-2 text-fg-3"><Loader2 className="h-4 w-4 animate-spin" /> Claude is working ({currentStage.label} · {currentStage.model})…</li>}
              <li ref={logEnd} aria-hidden className="h-0" />
            </ol>

            <div className="shrink-0 space-y-3 border-t border-line p-4">
              <div role="radiogroup" aria-label="Claude stage" className="grid grid-cols-3 gap-1 rounded-xl border border-line bg-app p-1">
                {STAGES.map((item) => (
                  <button key={item.id} type="button" role="radio" aria-checked={stage === item.id} disabled={running}
                    onClick={() => projectId && chooseStage(projectId, item.id)}
                    className={`rounded-lg px-2.5 py-2 text-left transition-colors disabled:opacity-60 ${stage === item.id ? 'bg-surface shadow-sm ring-1 ring-accent/60' : 'hover:bg-surface/60'}`}>
                    <span className={`block text-sm font-semibold ${stage === item.id ? 'text-fg' : 'text-fg-2'}`}>{item.label}</span>
                    <span className="block text-[11px] leading-tight text-fg-3">{item.model}</span>
                  </button>
                ))}
              </div>
              <p className="text-xs text-fg-3">{currentStage.hint}</p>
              {executeLocked && <p role="status" className="rounded-lg border border-warning/40 bg-warning/10 px-3 py-2 text-xs text-warning">Execute is locked: {brollProgress.done} of {brollProgress.total} B-roll items are ready. Approve or skip each one under "B-roll to make".</p>}

              <div className="rounded-xl border border-line bg-app focus-within:border-accent">
                <label htmlFor="prompt" className="sr-only">Prompt</label>
                <textarea id="prompt" ref={promptRef} className="block w-full resize-none bg-transparent px-3.5 pt-3 text-sm text-fg outline-none placeholder:text-fg-3" rows={4} maxLength={20000}
                  placeholder={!hasMain ? 'Add your main footage first, then describe the edit you want…' : currentStage.fallbackPrompt ? `Optional · empty sends "${currentStage.fallbackPrompt}"` : 'Describe the edit you want…'}
                  value={prompt} onChange={(e) => setPrompt(e.target.value)}
                  onKeyDown={(e) => { if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) { e.preventDefault(); void send(); } }} />
                <div className="flex items-center gap-2 px-3 pb-3 pt-1">
                  <span className="flex items-center gap-1 text-xs text-fg-3"><Kbd>Ctrl</Kbd>+<Kbd>Enter</Kbd> to send</span>
                  {running
                    ? <Button size="lg" className="ml-auto" icon={<Square className="h-4 w-4" />} onClick={() => void engine.stopAgent()}>Stop</Button>
                    : (
                      <Button variant="primary" size="lg" className="ml-auto" disabled={(!prompt.trim() && !currentStage.fallbackPrompt) || claudeMissing || executeLocked} onClick={() => void send()}>
                        Send to {currentStage.label} <ArrowRight className="h-4 w-4" />
                      </Button>
                    )}
                </div>
              </div>
              {claudeMissing && <p className="text-xs text-warning">Claude Code isn't installed on this PC. Go to Home and use Set up this PC to install it and sign in.</p>}
              <p className="flex items-start gap-1.5 text-xs text-fg-3"><Lock aria-hidden className="mt-px h-3.5 w-3.5 shrink-0" /> Claude edits only through AgenEditor's tools. Your prompt, filenames, file details and transcripts go to Anthropic; video files are never uploaded.</p>
            </div>
          </aside>
        </div>
      )}
    </div>
  );
};

const OutputLine: React.FC<{ entry: OutputEntry }> = ({ entry }) => {
  switch (entry.kind) {
    case 'prompt':
      return (
        <li className="ml-8 rounded-xl bg-accent/15 px-3.5 py-2.5">
          {entry.stage && <span className="mb-1 block text-xs font-semibold text-fg-2">{stageInfo(entry.stage).label} · {stageInfo(entry.stage).model}</span>}
          <span className="whitespace-pre-wrap">{entry.text}</span>
        </li>
      );
    case 'text':
      return <li className="mr-4 rounded-xl border border-line bg-surface px-3.5 py-2.5"><ClaudeMarkdown text={entry.text} /></li>;
    case 'tool':
      return <li className="flex items-center gap-1.5 text-xs text-fg-3"><Wrench className="h-3 w-3" /> {TOOL_LABELS[entry.name] ?? entry.name}</li>;
    case 'status':
      return <li className={`text-xs ${entry.tone === 'ok' ? 'text-success' : entry.tone === 'warn' ? 'text-warning' : 'text-danger'}`}>{entry.text}</li>;
  }
};
