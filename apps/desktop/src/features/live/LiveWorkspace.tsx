import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Activity, CheckCircle2, CircleAlert, Clapperboard, Download, FileVideo2, FolderPlus, Play, Plus, RefreshCw, Scissors, Sparkles, WandSparkles } from 'lucide-react';
import {
  liveEngineRepository as engine,
  type EngineAsset,
  type EngineAssetChecklist,
  type EngineBrief,
  type EngineClip,
  type EngineConnection,
  type EngineJob,
  type EngineProject,
  type EngineQA,
  type EngineTimeline,
} from '../../services/liveEngineRepository';
import { checklistReady, loadChecklist, saveChecklist, type AssetChecklist } from '../../utils/assetChecklist';
import { NativeAssetChecklist } from './NativeAssetChecklist';
import { NativeBriefPanel, type BriefDraft } from './NativeBriefPanel';

const button = 'rounded-lg border border-line px-3 py-2 text-sm font-medium text-fg hover:bg-elevated disabled:opacity-40 disabled:cursor-not-allowed';
const primary = `${button} border-accent bg-accent text-on-accent hover:bg-accent-hover`;
const panel = 'rounded-xl border border-line bg-surface p-4';
const input = 'w-full rounded-lg border border-line bg-app px-3 py-2 text-sm text-fg outline-none focus:border-accent';

const errorText = (error: unknown) => error instanceof Error ? error.message : String(error);
const framesLabel = (frames: number, fps: number) => `${frames} frames · ${(frames / fps).toFixed(2)}s`;

export const LiveWorkspace: React.FC = () => {
  const [projects, setProjects] = useState<EngineProject[]>([]);
  const [projectId, setProjectId] = useState<string | null>(null);
  const currentProjectId = useRef(projectId);
  currentProjectId.current = projectId;
  const [projectName, setProjectName] = useState('');
  const [durationSeconds, setDurationSeconds] = useState(40);
  const [summary, setSummary] = useState<(EngineProject & { assets: EngineAsset[] }) | null>(null);
  const [timeline, setTimeline] = useState<EngineTimeline | null>(null);
  const [selectedAssetId, setSelectedAssetId] = useState<string | null>(null);
  const [jobs, setJobs] = useState<EngineJob[]>([]);
  const [qa, setQA] = useState<EngineQA | null>(null);
  const [qaJobId, setQAJobId] = useState<string | null>(null);
  const [previewUrl, setPreviewUrl] = useState<string | null>(null);
  const [previewKind, setPreviewKind] = useState<'video' | 'image'>('video');
  const [connection, setConnection] = useState<EngineConnection | null>(null);
  const [generatedTool, setGeneratedTool] = useState('Google Flow');
  const [checklist, setChecklist] = useState<EngineAssetChecklist | null>(null);
  const [clientBrief, setClientBrief] = useState<EngineBrief | null>(null);
  const [feedback, setFeedback] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [modelBusy, setModelBusy] = useState(false);

  const run = useCallback(async (task: () => Promise<void>) => {
    setBusy(true);
    setFeedback(null);
    try { await task(); } catch (error) { setFeedback(errorText(error)); } finally { setBusy(false); }
  }, []);

  const refreshLibrary = useCallback(async () => {
    const list = await engine.listProjects();
    setProjects(list);
    setProjectId((current) => current ?? list[0]?.id ?? null);
  }, []);

  const refreshProject = useCallback(async (id: string) => {
    const [details, latest, activeJobs, storedChecklist, storedBrief] = await Promise.all([
      engine.summary(id), engine.timeline(id), engine.listJobs(id), engine.getChecklist(id), engine.getBrief(id),
    ]);
    if (id !== currentProjectId.current) return;
    let resolvedChecklist = storedChecklist;
    // One-time migration from the earlier WebView-only checklist.
    const legacy = storedChecklist.version === 0 && !storedChecklist.configured ? loadChecklist(id) : null;
    if (legacy) {
      const links: Record<string, string> = {};
      for (const request of legacy.requests) {
        const linked = legacy.links[request.id];
        if (linked === 'dropped') links[request.id] = linked;
        else if (details.assets.some((asset) => asset.id === linked && asset.kind === request.kind && asset.provenance?.origin === 'generated-external')) links[request.id] = linked;
      }
      try {
        resolvedChecklist = await engine.saveChecklist(id, { requests: legacy.requests, links }, 0);
        saveChecklist(id, null);
      } catch (error) {
        resolvedChecklist = await engine.getChecklist(id);
        setFeedback(`Earlier local checklist could not be moved into the project: ${errorText(error)}`);
      }
    }
    if (id !== currentProjectId.current) return;
    setSummary(details);
    setTimeline(latest);
    setQA(null);
    setQAJobId(null);
    setPreviewUrl(null);
    setJobs(activeJobs);
    setChecklist(resolvedChecklist);
    setClientBrief(storedBrief);
    setSelectedAssetId((current) => details.assets.some((asset) => asset.id === current)
      ? current : details.assets.find((asset) => asset.role === 'primary')?.id ?? null);
  }, []);

  useEffect(() => {
    void refreshLibrary().catch((error) => setFeedback(errorText(error)));
    void engine.connection().then(setConnection).catch((error) => setFeedback(errorText(error)));
  }, [refreshLibrary]);

  useEffect(() => {
    setChecklist(null);
    setSummary(null);
    setTimeline(null);
    if (!projectId) return;
    void refreshProject(projectId).catch((error) => setFeedback(errorText(error)));
  }, [projectId, refreshProject]);

  useEffect(() => {
    if (!projectId) return;
    const timer = window.setInterval(() => {
      void engine.listJobs(projectId).then(setJobs).catch(() => setFeedback('Engine status unavailable. Progress shown is the last received status.'));
    }, 800);
    return () => window.clearInterval(timer);
  }, [projectId]);

  // The agent commits revisions through MCP; show them without a manual refresh.
  const shownRevision = useRef<number | null>(null);
  shownRevision.current = timeline?.revision ?? null;
  useEffect(() => {
    if (!projectId) return;
    const timer = window.setInterval(() => {
      void engine.timeline(projectId).then((latest) => {
        if (currentProjectId.current === projectId && shownRevision.current !== null && latest.revision !== shownRevision.current) {
          void refreshProject(projectId).then(() => setFeedback(`Your agent saved revision ${latest.revision}.`)).catch(() => undefined);
        }
      }).catch(() => undefined);
    }, 3000);
    return () => window.clearInterval(timer);
  }, [projectId, refreshProject]);

  const fps = timeline?.render_profile.frame_rate.num && timeline.render_profile.frame_rate.den
    ? timeline.render_profile.frame_rate.num / timeline.render_profile.frame_rate.den : 30;
  const cuts = timeline?.clips.filter((clip) => clip.track === 'V1') ?? [];
  const overlays = timeline?.clips.filter((clip) => clip.track === 'V2') ?? [];
  const primaryAssets = summary?.assets.filter((asset) => asset.role === 'primary' && asset.video) ?? [];
  const selectedAsset = summary?.assets.find((asset) => asset.id === selectedAssetId);
  const latestPreview = jobs.find((job) => job.kind === 'preview_render' && job.status === 'succeeded');
  const latestFinal = jobs.find((job) => job.kind === 'final_render' && job.status === 'succeeded');
  const finalHasQA = Boolean(latestFinal && qa && qaJobId === latestFinal.id && qa.overall !== 'fail');
  const activeJobs = jobs.filter((job) => job.status === 'queued' || job.status === 'running');
  const assetRequestsPending = Boolean(projectId && (
    !checklist || checklist.project_id !== projectId ||
    (checklist.configured && !checklistReady(checklist, new Set(summary?.assets.map((asset) => asset.id) ?? [])))
  ));

  const createProject = () => run(async () => {
    const name = projectName.trim();
    if (!name) throw new Error('Enter a project name.');
    if (!Number.isInteger(durationSeconds) || durationSeconds < 5 || durationSeconds > 180) throw new Error('Duration must be a whole number from 5 to 180 seconds.');
    const created = await engine.createProject(name, durationSeconds * 30);
    await refreshLibrary();
    setProjectId(created.project_id);
    setProjectName('');
    setFeedback('Project created. Import your own approved media to begin.');
  });

  const updateChecklist = useCallback((next: AssetChecklist | null) => {
    if (!projectId || !checklist || checklist.project_id !== projectId) return;
    void run(async () => {
      try {
        setChecklist(await engine.saveChecklist(projectId, next, checklist.version));
      } catch (error) {
        setChecklist(await engine.getChecklist(projectId));
        throw error;
      }
    });
  }, [projectId, checklist, run]);

  const importAsset = (role: EngineAsset['role'], toolName?: string, requestId?: string, dropped = false) => run(async () => {
    if (!projectId) return;
    if (requestId && checklist?.project_id !== projectId) throw new Error('Refresh this project checklist before importing for a request.');
    if (toolName !== undefined && !toolName.trim()) throw new Error('Enter the external tool used to create this asset.');
    const asset = dropped
      ? await engine.importDroppedMedia(projectId, role, toolName?.trim())
      : await engine.importMedia(projectId, role, toolName?.trim());
    if (!asset) return;
    if (requestId && checklist?.project_id === projectId) {
      try {
        await engine.linkChecklistAsset(projectId, requestId, asset.id, checklist.version);
      } catch (error) {
        await refreshProject(projectId);
        throw new Error(`Imported ${asset.original_filename}, but could not link it to ${requestId}: ${errorText(error)}`);
      }
    }
    await refreshProject(projectId);
    if (asset.kind !== 'image') setSelectedAssetId(asset.id);
    setFeedback(`Imported ${asset.original_filename} as ${role}${toolName ? ' · AI-generated (external)' : ''}. The source copy is hashed and protected.`);
  });

  const linkChecklistAsset = (requestId: string, assetId: string) => run(async () => {
    if (!projectId || !checklist || checklist.project_id !== projectId) return;
    try {
      setChecklist(await engine.linkChecklistAsset(projectId, requestId, assetId, checklist.version));
    } catch (error) {
      setChecklist(await engine.getChecklist(projectId));
      throw error;
    }
    setFeedback(`Linked registered asset ${assetId} to request ${requestId}.`);
  });

  const saveBrief = (draft: BriefDraft) => run(async () => {
    if (!projectId || !clientBrief || clientBrief.project_id !== projectId) return;
    try {
      setClientBrief(await engine.saveBrief(projectId, draft, clientBrief.version));
    } catch (error) {
      setClientBrief(await engine.getBrief(projectId));
      throw new Error(`${errorText(error)} Your edits were not saved; the latest saved brief is shown.`);
    }
    setFeedback('Brief saved. Agents read this version through MCP.');
  });

  const acceptChecklistIssue = (requestId: string, note: string | null) => run(async () => {
    if (!projectId || !checklist || checklist.project_id !== projectId) return;
    try {
      setChecklist(await engine.acceptChecklistIssue(projectId, requestId, note, checklist.version));
    } catch (error) {
      setChecklist(await engine.getChecklist(projectId));
      throw error;
    }
    setFeedback(note === null ? `Cleared the accepted issue on ${requestId}.` : `Logged a known issue for ${requestId}; the file stays in use.`);
  });

  // A file dropped on a pending request card imports for that request. The shell
  // holds the dropped path; the page only learns where the drop landed.
  const dropHandler = useRef<(drop: { count: number; x: number; y: number }) => void>(() => undefined);
  dropHandler.current = (drop) => {
    if (busy || !checklist?.configured) return;
    const card = document.elementFromPoint(drop.x, drop.y)?.closest<HTMLElement>('[data-request-id]');
    if (!card) return;
    if (drop.count !== 1) { setFeedback('Drop exactly one file on a request.'); return; }
    const request = checklist.requests.find((item) => item.id === card.dataset.requestId);
    if (!request || card.dataset.requestStatus !== 'pending') { setFeedback('Drop files only on a pending request.'); return; }
    void importAsset('broll', generatedTool, request.id, true);
  };
  useEffect(() => {
    let unlisten: (() => void) | undefined;
    let cancelled = false;
    engine.onMediaDropped((drop) => dropHandler.current(drop))
      .then((stop) => { if (cancelled) stop(); else unlisten = stop; })
      .catch(() => undefined); // not running inside the desktop shell
    return () => { cancelled = true; unlisten?.(); };
  }, []);

  const startRender = (final: boolean) => run(async () => {
    if (!projectId || !timeline) return;
    if (assetRequestsPending) throw new Error('Import or drop every asset request before rendering.');
    if (timeline.clips.length === 0) throw new Error('Your agent has not committed a cut yet.');
    const job = await engine.startRender(projectId, timeline.revision, final);
    setJobs(await engine.listJobs(projectId));
    setFeedback(`${final ? 'Final render' : 'Preview'} queued for revision ${job.timeline_revision}. Progress below is measured by the engine.`);
  });

  const openPreview = () => run(async () => {
    if (!projectId) return;
    if (latestPreview) {
      setPreviewKind('video');
      setPreviewUrl(await engine.mediaUrl(projectId, latestPreview.id));
    } else if (selectedAsset?.video) {
      setPreviewKind(selectedAsset.kind === 'image' ? 'image' : 'video');
      setPreviewUrl(await engine.assetUrl(projectId, selectedAsset.id));
    }
  });

  const runQA = () => run(async () => {
    if (!projectId || !latestFinal) return;
    const report = await engine.runQA(projectId, latestFinal.id);
    setQA(report);
    setQAJobId(latestFinal.id);
    setFeedback(`Technical QA ${report.overall}. Review every check before saving.`);
  });

  const exportFile = () => run(async () => {
    if (!projectId || !latestFinal || !finalHasQA) return;
    const path = await engine.saveExport(projectId, latestFinal.id);
    if (path) setFeedback(`Saved verified export to ${path}`);
  });

  const copyHandoff = () => run(async () => {
    if (!projectId || !connection) return;
    const message = `Connect AgenEditor with: ${connection.command}\nProject ID: ${projectId}\nYou are the editor. Call get_brief first: it holds my instructions, the script and my requirement checklist. Inspect registered assets and transcript source words, then commit a source-cited edit yourself with commit_timeline (pass expected_revision) and revise it when I ask. Include an asset_requests JSON block with id, kind, purpose, prompt and aspect for visuals the footage lacks; I will prepare those assets externally and import them. I will not edit cuts by hand. Use get_asset_checklist to check request status before committing or rendering. A person confirms each requirement, so do not mark any as met.`;
    await navigator.clipboard.writeText(message);
    setFeedback('Agent handoff copied. The external host must use the shared-service MCP command.');
  });

  const transcriptAsset = primaryAssets.find((asset) => asset.audio);
  const transcribe = () => run(async () => {
    if (!projectId || !transcriptAsset) return;
    const job = await engine.transcribe(projectId, transcriptAsset.id);
    setJobs(await engine.listJobs(projectId));
    setFeedback(`Local transcription ${job.status === 'succeeded' ? 'loaded from cache' : 'queued'}. No source is uploaded by the engine.`);
  });

  return (
    <div className="flex h-screen bg-app text-fg overflow-hidden">
      <aside className="w-72 shrink-0 border-r border-line bg-sidebar p-5 flex flex-col gap-5 overflow-y-auto">
        <div>
          <div className="flex items-center gap-2 text-lg font-bold"><Clapperboard className="w-5 h-5 text-accent" /> AgenEditor</div>
          <p className="text-xs text-fg-3 mt-1">Desktop Alpha · Local engine</p>
        </div>
        <section>
          <div className="flex justify-between items-center mb-2"><h2 className="font-semibold text-sm">Project Library</h2><button className="text-fg-3 hover:text-fg" onClick={() => void run(refreshLibrary)} aria-label="Refresh projects"><RefreshCw className="w-4 h-4" /></button></div>
          <div className="space-y-1">{projects.map((project) => <button key={project.id} disabled={busy} onClick={() => setProjectId(project.id)} className={`w-full text-left rounded-lg p-2.5 text-sm ${projectId === project.id ? 'bg-accent/15 text-accent' : 'hover:bg-elevated'}`}><span className="block truncate font-medium">{project.name}</span><span className="text-xs text-fg-3">Revision {project.current_revision}</span></button>)}</div>
          {projects.length === 0 && <p className="text-xs text-fg-3">No projects yet.</p>}
        </section>
        <section className="space-y-2 border-t border-line pt-4">
          <h2 className="font-semibold text-sm flex items-center gap-2"><FolderPlus className="w-4 h-4" /> New project</h2>
          <input className={input} aria-label="Project name" placeholder="Project name" value={projectName} onChange={(event) => setProjectName(event.target.value)} />
          <label className="block text-xs text-fg-2">Target seconds<input className={`${input} mt-1`} type="number" min={5} max={180} step={1} value={durationSeconds} onChange={(event) => setDurationSeconds(Number(event.target.value))} /></label>
          <button className={`${primary} w-full`} disabled={busy} onClick={() => void createProject()}><Plus className="w-4 h-4 inline mr-1" /> Create project</button>
        </section>
        <div className="mt-auto text-xs text-fg-3 border-t border-line pt-4">{connection?.connected ? '● Shared engine connected' : 'Engine connection pending'}<p className="mt-1">Sources remain on this computer.</p></div>
      </aside>

      <main className="flex-1 min-w-0 overflow-y-auto p-6 space-y-5">
        <header className="flex flex-wrap items-start justify-between gap-3">
          <div><p className="text-xs uppercase tracking-wider text-accent">Live workspace</p><h1 className="text-2xl font-bold mt-1">{summary?.name ?? 'Choose a project'}</h1><p className="text-sm text-fg-2 mt-1">{timeline ? `Revision ${timeline.revision} · ${framesLabel(timeline.target_duration_frames, fps)} target` : 'Create or open a project to edit approved media.'}</p></div>
          {projectId && <button className={button} disabled={busy} onClick={() => void run(() => refreshProject(projectId))}><RefreshCw className="w-4 h-4 inline mr-1" /> Refresh shared state</button>}
        </header>
        {feedback && <div role="status" className="rounded-lg border border-accent/40 bg-accent/10 px-4 py-3 text-sm break-words">{feedback}</div>}
        {!projectId || !summary || !timeline ? <div className={panel}>Create a project to begin a real editing session.</div> : <>

          <Step n={1} title="Import assets and instructions" status={`${summary.assets.length} asset${summary.assets.length === 1 ? '' : 's'} · instructions ${clientBrief && clientBrief.version > 0 ? 'saved' : 'not saved yet'}`}>
            <p className="text-xs text-fg-2">Add everything the editor needs: your main footage (with or without speech), any B-roll or music, and your instructions. Your AI editor reads all of it.</p>
            <section className={`${panel} space-y-4`}>
              <div className="flex justify-between items-center"><h3 className="font-semibold flex items-center gap-2"><FileVideo2 className="w-4 h-4" /> Asset bin</h3><span className="text-xs text-fg-3">{summary.assets.length} registered</span></div>
              <div className="flex flex-wrap gap-2"><button className={button} disabled={busy} onClick={() => void importAsset('primary')}>Import main footage</button><button className={button} disabled={busy} onClick={() => void importAsset('broll')}>Import B-roll</button><button className={button} disabled={busy} onClick={() => void importAsset('music')}>Import music</button></div>
              <div className="flex flex-wrap items-end gap-2"><label className="text-xs text-fg-2">External tool<input className={`${input} mt-1`} value={generatedTool} onChange={(event) => setGeneratedTool(event.target.value)} maxLength={100} /></label><button className={button} disabled={busy} onClick={() => void importAsset('broll', generatedTool)}>Import generated B-roll…</button></div>
              <div className="max-h-44 overflow-y-auto space-y-1">{summary.assets.map((asset) => <button key={asset.id} onClick={() => setSelectedAssetId(asset.id)} className={`w-full text-left rounded-lg p-2 text-sm ${selectedAssetId === asset.id ? 'bg-accent/15' : 'hover:bg-elevated'}`}><span className="font-medium block truncate">{asset.original_filename}</span><span className="text-xs text-fg-3">{asset.role} · {asset.video ? `${asset.video.width}×${asset.video.height} · ${asset.video.duration_frames} source frames` : 'audio'} · {asset.id}</span>{asset.provenance?.origin === 'generated-external' && <span className="block text-xs text-warning">AI-generated (external) · {asset.provenance.tool_name}</span>}</button>)}</div>
              {summary.assets.length === 0 && <p className="text-sm text-fg-3">Import a file through the native picker. The engine copies and hashes it before editing.</p>}
            </section>
            <NativeBriefPanel brief={clientBrief} busy={busy} onSave={(draft) => void saveBrief(draft)} />
          </Step>

          <Step n={2} title="AI edit" status={`revision ${timeline.revision} · ${cuts.length === 0 ? 'no cuts yet' : `${cuts.length} cut${cuts.length === 1 ? '' : 's'}`}${assetRequestsPending ? ' · asset requests pending' : ''}`}>
            <section className={`${panel} space-y-3`}><h3 className="font-semibold flex items-center gap-2"><Sparkles className="w-4 h-4" /> Agent handoff</h3><p className="text-xs text-fg-2">Codex or Claude Code is the editor: it connects to this local service through MCP and commits every revision itself. Your saved instructions are read by the agent with get_brief. You review and approve. The host can read transcripts, filenames and asset metadata through MCP and may send them to its model provider. Raw media is not uploaded by AgenEditor.</p><button className={button} disabled={!connection} onClick={() => void copyHandoff()}>Copy MCP setup for your agent</button><p className="text-xs text-fg-3 break-all">{connection?.command ?? 'Engine setup pending'}</p></section>
            <section className={`${panel} space-y-3`}><h3 className="font-semibold flex items-center gap-2"><WandSparkles className="w-4 h-4" /> Local transcript</h3><p className="text-xs text-fg-2">Optional, and only for footage that has speech: it lets the AI editor cite exact words. Footage without speech needs no transcript. Analysis runs locally; downloading a model is a separate, explicit action.</p><div className="flex gap-2 flex-wrap"><button className={button} disabled={modelBusy} onClick={() => { setModelBusy(true); void engine.cacheModel('tiny').then(setFeedback).catch((error) => setFeedback(errorText(error))).finally(() => setModelBusy(false)); }}> {modelBusy ? 'Installing model…' : 'Install tiny model'}</button><button className={button} disabled={!transcriptAsset || busy} onClick={() => void transcribe()}>Transcribe speech</button></div><TranscriptList projectId={projectId} jobs={jobs} /></section>
          <section data-testid="agent-edit" className={`${panel} space-y-3`}>
            <div className="flex flex-wrap items-center justify-between gap-2"><h3 className="font-semibold flex items-center gap-2"><Scissors className="w-4 h-4" /> Edit made by your AI agent</h3><div className="text-xs text-fg-2">Revision {timeline.revision} · {framesLabel(timeline.clips.filter((clip) => clip.track === 'V1').reduce((sum, clip) => sum + (clip.timeline_duration_frames ?? clip.source_out_frames - clip.source_in_frames), 0), fps)}</div></div>
            <p className="text-xs text-fg-2">Your agent makes every cut and revision through MCP. You review, approve and export. To change something, tell the agent; each change becomes a new immutable revision and appears here.</p>
            {cuts.length === 0 ? <p className="text-sm text-fg-3">No cuts yet. Copy the agent handoff below and ask your agent to plan the edit.</p> : (
              <ol className="space-y-2" aria-label="Cuts">{cuts.map((clip, index) => <li key={clip.clip_id} className="rounded-lg border border-line p-2 text-xs space-y-0.5"><div className="flex flex-wrap gap-x-3"><strong>Cut {index + 1}</strong><span className="truncate">{summary.assets.find((asset) => asset.id === clip.asset_id)?.original_filename ?? clip.asset_id}</span><span>source frames {clip.source_in_frames}–{clip.source_out_frames}</span><span>{clip.timeline_duration_frames ?? clip.source_out_frames - clip.source_in_frames} out frames</span></div>{clip.rationale && <div className="text-fg-3">Why: {clip.rationale}</div>}</li>)}</ol>
            )}
            {overlays.length > 0 && (
              <div className="border-t border-line pt-2 space-y-1.5"><h3 className="text-sm font-semibold">Overlays</h3>{overlays.map((clip) => { const asset = summary.assets.find((item) => item.id === clip.asset_id); return <div key={clip.clip_id} className="rounded border border-line p-2 text-xs"><span>{asset?.original_filename ?? clip.asset_id} · {asset?.kind === 'image' ? 'still image' : 'B-roll'} · frames {clip.timeline_in_frames}–{clip.timeline_in_frames + (clip.timeline_duration_frames ?? 1)}</span>{asset?.provenance?.origin === 'generated-external' && <span className="text-warning"> · AI-generated (external)</span>}{clip.rationale && <div className="text-fg-3">Why: {clip.rationale}</div>}</div>; })}</div>
            )}
            <p className="text-xs text-fg-3">Last change by {timeline.provenance?.author ?? 'unknown'}{timeline.provenance?.summary ? `: ${timeline.provenance.summary}` : ''}</p>
          </section>
            <section className={`${panel} space-y-3`}>
              <NativeAssetChecklist list={checklist?.configured ? checklist : null} assets={summary.assets} busy={busy} onChange={updateChecklist} onImport={(requestId, toolName) => void importAsset('broll', toolName, requestId)} onLink={(requestId, assetId) => void linkChecklistAsset(requestId, assetId)} onAccept={(requestId, note) => void acceptChecklistIssue(requestId, note)} />
              <p className="text-xs text-fg-3">Checklist and asset links are saved in this project and shared with the engine and MCP.</p>
              {assetRequestsPending && <p className="text-xs text-warning">Import and link every requested file, or drop its request, before saving or rendering.</p>}
            </section>
          </Step>

          <Step n={3} title="Preview" status={latestFinal ? 'final rendered' : latestPreview ? `preview of revision ${latestPreview.timeline_revision} ready` : 'not rendered yet'}>
            <section className={`${panel} space-y-3`}>
              <div className="flex justify-between items-center"><h3 className="font-semibold flex items-center gap-2"><Play className="w-4 h-4" /> Preview player</h3><span className="text-xs text-fg-3">{latestPreview ? `Rendered revision ${latestPreview.timeline_revision}` : 'Source / render pending'}</span></div>
              <div className="aspect-video max-h-80 bg-black rounded-lg overflow-hidden flex items-center justify-center">{previewUrl ? (previewKind === 'image' ? <img className="w-full h-full object-contain" src={previewUrl} alt="Selected source image" /> : <video className="w-full h-full" controls src={previewUrl} />) : <p className="text-sm text-fg-3">No preview opened yet.</p>}</div>
              <div className="flex gap-2 flex-wrap"><button className={button} onClick={() => void openPreview()} disabled={!latestPreview && !selectedAsset?.video}>Open {latestPreview ? 'rendered preview' : 'selected source'}</button><button className={primary} onClick={() => void startRender(false)} disabled={busy || assetRequestsPending || !timeline.clips.length}>Render fast proxy</button></div>
              {activeJobs.length > 0 && <p className="text-xs text-warning">Preview/render pending: {activeJobs.map((job) => `${job.kind} ${Math.round(job.progress * 100)}%`).join(', ')}</p>}
            </section>
            <section className={`${panel} space-y-3`}><h3 className="font-semibold flex items-center gap-2"><Download className="w-4 h-4" /> Final export</h3><p className="text-xs text-fg-2">Final render stays pinned to the saved revision. Run technical QA before choosing an output path.</p><div className="flex flex-wrap gap-2"><button className={primary} onClick={() => void startRender(true)} disabled={busy || assetRequestsPending || !timeline.clips.length}>Render final MP4</button><button className={button} onClick={() => void runQA()} disabled={!latestFinal || busy}>Run technical QA</button><button className={button} onClick={() => void exportFile()} disabled={!finalHasQA || busy}>Save verified MP4…</button></div>{qa ? <div><p className={`text-sm font-semibold ${qa.overall === 'pass' ? 'text-success' : qa.overall === 'fail' ? 'text-danger' : 'text-warning'}`}>Technical QA: {qa.overall}</p><ul className="text-xs text-fg-2 mt-1 space-y-1">{qa.checks.map((check) => <li key={check.check_id} className="flex gap-2"><span>{check.status === 'pass' ? <CheckCircle2 className="w-3.5 h-3.5 text-success" /> : <CircleAlert className="w-3.5 h-3.5 text-warning" />}</span>{check.check_id}: {check.measured ?? check.message}</li>)}</ul>{qa.generated_assets?.length > 0 && <div className="mt-3 text-xs text-warning"><strong>AI-generated (external) assets used</strong><ul>{qa.generated_assets.map((asset) => <li key={asset.asset_id}>{asset.original_filename} · {asset.tool_name} · {asset.asset_id}</li>)}</ul></div>}</div> : <p className="text-xs text-fg-3">No QA measurement yet.</p>}</section>
            <section className={`${panel} space-y-3`}><h3 className="font-semibold flex items-center gap-2"><Activity className="w-4 h-4" /> Real jobs</h3>{jobs.length === 0 ? <p className="text-sm text-fg-3">No processing jobs yet.</p> : jobs.slice(0, 8).map((job) => <div key={job.id} className="rounded-lg border border-line p-2.5 text-sm"><div className="flex justify-between gap-2"><span>{job.kind} · r{job.timeline_revision ?? '—'}</span><strong className={job.status === 'failed' ? 'text-danger' : job.status === 'succeeded' ? 'text-success' : 'text-warning'}>{job.status}</strong></div><div className="h-1.5 bg-app rounded mt-2"><div className="h-full bg-accent rounded" style={{ width: `${Math.round(job.progress * 100)}%` }} /></div><div className="flex justify-between items-center mt-1"><span className="text-xs text-fg-3">{Math.round(job.progress * 100)}% {job.error && `· ${job.error.code}: ${job.error.message}`}</span>{['queued', 'running'].includes(job.status) && <button className="text-xs text-danger" onClick={() => void run(async () => { await engine.cancelJob(projectId, job.id); setJobs(await engine.listJobs(projectId)); })}>Cancel</button>}</div></div>)}</section>
          </Step>
        </>}
      </main>
    </div>
  );
};

const TranscriptList: React.FC<{ projectId: string; jobs: EngineJob[] }> = ({ projectId, jobs }) => {
  const [lines, setLines] = useState<string[]>([]);
  const [status, setStatus] = useState<string>('');
  const completed = jobs.filter((job) => job.kind === 'transcribe' && job.status === 'succeeded').length;
  useEffect(() => {
    let active = true;
    setLines([]);
    setStatus('');
    void engine.transcripts(projectId).then(async (items) => {
      if (!active || items.length === 0) return;
      const transcript = await engine.transcript(projectId, items[0].id);
      if (active) { setLines(transcript.words.map((word) => `${(word.start_us / 1_000_000).toFixed(2)}s  ${word.text}`)); setStatus(`${transcript.total_words} source words · ${items[0].language}`); }
    }).catch((error) => { if (active) setStatus(errorText(error)); });
    return () => { active = false; };
  }, [projectId, completed]);
  return <div className="max-h-40 overflow-y-auto rounded-lg bg-app p-2 text-xs text-fg-2"><p className="text-fg-3 mb-1">{status || 'No transcript yet.'}</p>{lines.slice(0, 100).map((line, index) => <p key={index}>{line}</p>)}{lines.length > 100 && <p>Showing first 100 words. The MCP tool pages through all source words.</p>}</div>;
};

const Step: React.FC<{ n: number; title: string; status: string; children: React.ReactNode }> = ({ n, title, status, children }) => (
  <section data-testid={`step-${n}`} aria-labelledby={`step-${n}-title`} className="space-y-4">
    <div className="flex flex-wrap items-baseline justify-between gap-2 border-b border-line pb-2">
      <h2 id={`step-${n}-title`} className="text-lg font-bold"><span className="text-accent">Step {n} of 3</span> · {title}</h2>
      <span data-testid={`step-${n}-status`} className="text-xs text-fg-2">{status}</span>
    </div>
    {children}
  </section>
);
