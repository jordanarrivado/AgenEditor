import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Link, useNavigate, useParams, useSearchParams } from 'react-router-dom';
import {
  Bot,
  ChevronRight,
  ClipboardCheck,
  Download,
  Eye,
  FileText,
  Film,
  Layers,
  PanelLeft,
  PanelRight,
  Redo2,
  Undo2,
  Wand2,
  Activity as ActivityIcon,
  CircleDot,
} from 'lucide-react';
import { useProjectWorkspace, pickRevision } from '../../hooks/useProjectWorkspace';
import { useRevisionHistory } from '../../hooks/useRevisionHistory';
import { editorLayoutFor, isTypingTarget, useViewportWidth } from '../../hooks/useViewport';
import { editorStore, useEditorStore, captionStyleFromPreset, type LeftTab, type NarrowPane } from '../../stores/editorStore';
import { useAppData } from '../../app/AppDataContext';
import { useSettings } from '../../app/SettingsContext';
import type { DemoTimelineClip, DemoTimelineTrack } from '../../types';
import {
  describeClip,
  duplicateClip,
  findClip,
  patchClip,
  patchTrack,
  removeClip,
  setClipDuration,
  setClipStart,
} from '../../utils/timelineGeometry';
import { framesToTimecode } from '../../utils/timecode';
import { copyText } from '../../utils/agentBrief';
import { PreviewStage } from '../../components/editor/PreviewStage';
import { Timeline } from '../../components/editor/Timeline';
import { MediaBin } from '../../components/editor/MediaBin';
import { TranscriptPanel } from '../../components/editor/TranscriptPanel';
import { BriefPanel } from '../../components/editor/BriefPanel';
import { AgentHandoffContent } from '../../components/editor/AgentHandoffPanel';
import { InspectorPanel } from '../../components/editor/InspectorPanel';
import { ExportModal } from '../../components/export/ExportModal';
import { DemoModeBadge } from '../../components/common/DemoModeBadge';
import { showToast } from '../../components/common/Toast';
import { Button, IconButton, Tooltip, cx } from '../../components/ui/primitives';
import { EmptyState, ErrorState, LoadingState } from '../../components/ui/StateViews';
import { NotFoundPage } from '../system/NotFoundPage';

const LEFT_TABS: { id: LeftTab; label: string; icon: React.ReactNode }[] = [
  { id: 'media', label: 'Media', icon: <Film className="w-3.5 h-3.5" /> },
  { id: 'transcript', label: 'Transcript', icon: <FileText className="w-3.5 h-3.5" /> },
  { id: 'brief', label: 'Brief', icon: <Wand2 className="w-3.5 h-3.5" /> },
  { id: 'agent', label: 'Agent', icon: <Bot className="w-3.5 h-3.5" /> },
];

export const EditorPage: React.FC = () => {
  const { projectId } = useParams();
  const [params, setParams] = useSearchParams();
  const navigate = useNavigate();
  const ws = useProjectWorkspace(projectId);
  const app = useAppData();
  const { settings } = useSettings();
  const [ui] = useEditorStore();
  const width = useViewportWidth();
  const layout = editorLayoutFor(width);
  const [exportOpen, setExportOpen] = useState(false);
  const [leftW, setLeftW] = useState(280);
  const [rightW, setRightW] = useState(300);

  const source = useMemo(() => pickRevision(ws.revisions, ws.project, params.get('rev')), [ws.revisions, ws.project, params]);
  const history = useRevisionHistory(source, ws.saveRevision);
  const revision = history.revision;
  const fps = ws.project ? ws.project.fpsNum / ws.project.fpsDen : 30;

  // Reset transient UI state whenever a different revision is opened.
  const openedRef = useRef<string | null>(null);
  useEffect(() => {
    if (!revision || !ws.project) return;
    const key = `${ws.project.id}:${revision.id}`;
    if (openedRef.current === key) {
      editorStore.setTimelineBounds(revision.durationFrames, fps);
      return;
    }
    openedRef.current = key;
    editorStore.reset({
      maxFrames: revision.durationFrames,
      fps,
      playheadFrames: Math.round(revision.durationFrames * 0.3),
      snappingEnabled: settings.defaultSnapping,
      captionStyle: captionStyleFromPreset(settings.defaultCaptionPresetId),
      activeLeftTab: ws.transcript.length ? 'transcript' : 'media',
    });
  }, [revision, ws.project, ws.transcript.length, fps, settings.defaultSnapping, settings.defaultCaptionPresetId]);

  useEffect(() => () => editorStore.pause(), []);

  // ---- Edit commands (all go through undo history)
  const commit = history.commit;
  const removeWithUndo = useCallback(
    (clipId: string) => {
      if (!revision) return;
      const ref = findClip(revision, clipId);
      if (!ref) return;
      commit(removeClip(revision, clipId), `Remove “${ref.clip.label ?? 'clip'}”`);
      editorStore.selectClip(null);
      showToast({
        type: 'info',
        title: 'Clip removed from demo timeline',
        message: ref.clip.label,
        action: { label: 'Undo', onClick: () => history.undo() },
      });
    },
    [revision, commit, history]
  );
  const duplicate = useCallback(
    (clipId: string) => {
      if (!revision) return;
      const r = duplicateClip(revision, clipId);
      if (!r) return;
      commit(r.revision, 'Duplicate clip');
      const ref = findClip(r.revision, r.newClipId);
      editorStore.selectClip(r.newClipId, ref?.track.id ?? null);
    },
    [revision, commit]
  );
  const copyInfo = useCallback(
    async (clipId: string) => {
      if (!revision) return;
      const ref = findClip(revision, clipId);
      if (!ref) return;
      const ok = await copyText(describeClip(ref, fps, framesToTimecode));
      showToast(ok ? { type: 'success', title: 'Clip info copied' } : { type: 'warning', title: 'Clipboard unavailable' });
    },
    [revision, fps]
  );
  const toggleTrack = (trackId: string, key: 'muted' | 'hidden') => {
    if (!revision) return;
    const t = revision.tracks.find((x) => x.id === trackId);
    if (!t) return;
    const on = !t[key];
    commit(patchTrack(revision, trackId, { [key]: on }), `${on ? (key === 'muted' ? 'Mute' : 'Hide') : key === 'muted' ? 'Unmute' : 'Show'} ${t.label}`);
  };
  const onPatchClip = (clipId: string, patch: Partial<DemoTimelineClip>, label: string) =>
    revision && commit(patchClip(revision, clipId, patch), label);
  const onPatchTrack = (trackId: string, patch: Partial<DemoTimelineTrack>, label: string) =>
    revision && commit(patchTrack(revision, trackId, patch), label);

  // ---- Keyboard shortcuts (spec §11)
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (isTypingTarget(e.target) || !revision) return;
      if (document.querySelector('[role="dialog"]')) return; // dialogs own the keyboard
      const mod = e.ctrlKey || e.metaKey;
      if (mod && e.key.toLowerCase() === 'z') {
        e.preventDefault();
        if (e.shiftKey) history.redo();
        else history.undo();
        return;
      }
      if (mod && e.key.toLowerCase() === 'y') {
        e.preventDefault();
        history.redo();
        return;
      }
      if (mod || e.altKey) return;
      const s = editorStore.getState();
      // Let focused buttons handle Space/Enter themselves, except the clip/ruler surface.
      const focusedButton = e.target instanceof HTMLButtonElement && !e.target.dataset.clipId;
      switch (e.key) {
        case ' ':
          if (focusedButton) return;
          e.preventDefault();
          editorStore.togglePlay();
          break;
        case 'ArrowLeft':
          e.preventDefault();
          editorStore.stepFrames(e.shiftKey ? -fps : -1);
          break;
        case 'ArrowRight':
          e.preventDefault();
          editorStore.stepFrames(e.shiftKey ? fps : 1);
          break;
        case 'Home':
          e.preventDefault();
          editorStore.setPlayhead(0);
          break;
        case 'End':
          e.preventDefault();
          editorStore.setPlayhead(revision.durationFrames);
          break;
        case '+':
        case '=':
          e.preventDefault();
          editorStore.zoomIn();
          break;
        case '-':
        case '_':
          e.preventDefault();
          editorStore.zoomOut();
          break;
        case 'Delete':
        case 'Backspace':
          if (s.selectedClipId) {
            e.preventDefault();
            removeWithUndo(s.selectedClipId);
          }
          break;
        case 'Escape':
          if (s.selectedClipId) editorStore.selectClip(null);
          break;
        case 's':
        case 'S':
          editorStore.toggleSnapping();
          break;
        case 'z':
        case 'Z':
          editorStore.toggleSafeZones();
          break;
        case 'g':
        case 'G':
          editorStore.toggleGridGuides();
          break;
      }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [revision, history, fps, removeWithUndo]);

  // ---- Load / empty states
  if (ws.status === 'loading') {
    return (
      <div className="p-6">
        <LoadingState label="Loading editor…" rows={6} />
      </div>
    );
  }
  if (ws.status === 'missing') return <NotFoundPage what="project" />;
  if (ws.status === 'error' || !ws.project) return <ErrorState className="m-6" message={ws.error} onRetry={ws.reload} />;

  const project = ws.project;
  const libraryAssets = app.allAssets;

  if (ws.assets.length === 0 || !revision) {
    return (
      <div className="h-full flex flex-col">
        <EditorTopBar projectName={project.name} projectId={project.id} />
        <div className="flex-1 flex items-center justify-center p-6">
          {ws.assets.length === 0 ? (
            <EmptyState
              icon={<Film className="w-5 h-5" />}
              title="This project has no media yet"
              message="Add demo assets to plan an edit. Importing your own files requires the media engine."
              action={
                <Button variant="primary" onClick={() => ws.addAssets(libraryAssets.filter((a) => a.kind === 'video').slice(0, 3).map((a) => a.id))}>
                  Choose sample assets
                </Button>
              }
            />
          ) : (
            <EmptyState
              icon={<Layers className="w-5 h-5" />}
              title="No timeline revision yet"
              message="Lay out the project's demo assets on a five-track timeline to start editing. This is a sample plan, not an AI edit."
              action={
                <Button
                  variant="primary"
                  onClick={async () => {
                    const rev = await ws.createSampleRevision();
                    if (rev) setParams({ rev: rev.id }, { replace: true });
                    showToast({ type: 'success', title: 'Sample edit plan created', message: 'Demo timeline v1 is ready.' });
                  }}
                >
                  Create sample edit plan
                </Button>
              }
            />
          )}
        </div>
      </div>
    );
  }

  const selectRevision = (id: string) => setParams({ rev: id }, { replace: true });
  const reviewHref = `/projects/${project.id}/review?rev=${revision.id}`;
  const runningJobs = app.jobs.filter((j) => j.projectId === project.id && (j.status === 'running' || j.status === 'queued')).length;

  // ---- Panels
  const dock = (
    <div className="h-full flex flex-col bg-sidebar min-w-0">
      <div role="tablist" aria-label="Editor panels" className="h-10 shrink-0 px-1.5 flex items-center gap-0.5 border-b border-line bg-surface">
        {LEFT_TABS.map((t) => (
          <button
            key={t.id}
            role="tab"
            id={`dock-tab-${t.id}`}
            aria-selected={ui.activeLeftTab === t.id}
            aria-controls="dock-panel"
            onClick={() => editorStore.setActiveLeftTab(t.id)}
            className={cx(
              'flex-1 h-7 rounded-md text-xs font-medium inline-flex items-center justify-center gap-1',
              ui.activeLeftTab === t.id ? 'bg-elevated text-accent' : 'text-fg-3 hover:text-fg'
            )}
          >
            {t.icon}
            {t.label}
          </button>
        ))}
      </div>
      <div id="dock-panel" role="tabpanel" aria-labelledby={`dock-tab-${ui.activeLeftTab}`} className="flex-1 min-h-0 overflow-y-auto">
        {ui.activeLeftTab === 'media' && <MediaBin assets={ws.assets} libraryAssets={libraryAssets} onAddAssets={ws.addAssets} />}
        {ui.activeLeftTab === 'transcript' && <TranscriptPanel utterances={ws.transcript} revision={revision} />}
        {ui.activeLeftTab === 'brief' && <BriefPanel project={project} onSave={ws.updateProject} />}
        {ui.activeLeftTab === 'agent' && <AgentHandoffContent project={project} assets={ws.assets} revision={revision} />}
      </div>
    </div>
  );
  const inspector = (
    <InspectorPanel
      project={project}
      revision={revision}
      assets={ws.assets}
      onPatchClip={onPatchClip}
      onSetClipDuration={(id, f) => commit(setClipDuration(revision, id, f), 'Change clip duration')}
      onSetClipStart={(id, f) => commit(setClipStart(revision, id, f), 'Move clip')}
      onPatchTrack={onPatchTrack}
    />
  );
  const preview = <PreviewStage project={project} revision={revision} />;
  const timeline = (
    <Timeline
      revision={revision}
      onToggleTrack={toggleTrack}
      onDuplicateClip={duplicate}
      onRemoveClip={removeWithUndo}
      onCopyClipInfo={copyInfo}
    />
  );

  const topBar = (
    <EditorTopBar projectName={project.name} projectId={project.id}>
      <label className="sr-only" htmlFor="rev-select">
        Revision
      </label>
      <select
        id="rev-select"
        value={revision.id}
        onChange={(e) => selectRevision(e.target.value)}
        className="h-7 px-2 rounded-md bg-surface border border-line text-xs font-semibold text-fg focus:outline-none focus:border-accent"
      >
        {ws.revisions.map((r) => (
          <option key={r.id} value={r.id}>
            v{r.revisionNumber}
            {r.id === project.activeRevisionId ? ' (active)' : ''}
          </option>
        ))}
      </select>
      <DemoModeBadge compact={layout === 'narrow'} />
      <div className="flex-1" />
      <SaveIndicator persists={app.persistsLocally} lastLabel={history.lastLabel} />
      <div className="flex items-center">
        <IconButton label={history.undoLabel ? `Undo: ${history.undoLabel} (Ctrl+Z)` : 'Nothing to undo'} disabled={!history.canUndo} onClick={history.undo}>
          <Undo2 className="w-4 h-4" />
        </IconButton>
        <IconButton label={history.redoLabel ? `Redo: ${history.redoLabel} (Ctrl+Shift+Z)` : 'Nothing to redo'} disabled={!history.canRedo} onClick={history.redo}>
          <Redo2 className="w-4 h-4" />
        </IconButton>
      </div>
      {layout !== 'narrow' && (
        <div className="flex items-center">
          <IconButton label={ui.leftDockOpen ? 'Hide left panel' : 'Show left panel'} active={ui.leftDockOpen} onClick={() => editorStore.toggleLeftDock()}>
            <PanelLeft className="w-4 h-4" />
          </IconButton>
          <IconButton label={ui.inspectorOpen ? 'Hide inspector' : 'Show inspector'} active={ui.inspectorOpen} onClick={() => editorStore.toggleInspector()}>
            <PanelRight className="w-4 h-4" />
          </IconButton>
        </div>
      )}
      <Button size="sm" icon={<Eye className="w-3.5 h-3.5" />} onClick={() => navigate(reviewHref)}>
        Review
      </Button>
      <Button size="sm" variant="primary" icon={<Download className="w-3.5 h-3.5" />} onClick={() => setExportOpen(true)}>
        Export
      </Button>
    </EditorTopBar>
  );

  const statusBar = (
    <footer className="h-7 shrink-0 px-3 border-t border-line bg-sidebar flex items-center justify-between gap-3 text-[11px] text-fg-3">
      <span className="flex items-center gap-1.5 truncate">
        <CircleDot aria-hidden className="w-3 h-3 text-warning" />
        Demo timeline · No actual rendering connected
      </span>
      <span className="flex items-center gap-3 shrink-0">
        <span className="font-mono hidden md:inline">
          {revision.tracks.reduce((n, t) => n + t.clips.length, 0)} clips · {framesToTimecode(revision.durationFrames, fps)}
        </span>
        <Link to="/activity" className="flex items-center gap-1 hover:text-fg">
          <ActivityIcon aria-hidden className="w-3 h-3" /> Jobs{runningJobs ? ` (${runningJobs} running)` : ''}
        </Link>
        <Link to={reviewHref} className="flex items-center gap-1 hover:text-fg">
          <ClipboardCheck aria-hidden className="w-3 h-3" /> QA
        </Link>
      </span>
    </footer>
  );

  const exportModal = (
    <ExportModal open={exportOpen} onOpenChange={setExportOpen} project={project} revision={revision} onViewActivity={() => navigate('/activity')} />
  );

  // ---- Narrow layout (<960px): one pane at a time
  if (layout === 'narrow') {
    const panes: { id: NarrowPane; label: string }[] = [
      { id: 'preview', label: 'Preview' },
      { id: 'timeline', label: 'Timeline' },
      { id: 'inspector', label: 'Inspector' },
      { id: 'dock', label: 'Panels' },
    ];
    return (
      <div className="h-full flex flex-col min-w-0">
        {topBar}
        <p className="shrink-0 px-3 py-1.5 text-[11px] text-fg-3 bg-surface border-b border-line">Best experienced at 1280px or wider.</p>
        <div role="tablist" aria-label="Editor view" className="shrink-0 flex border-b border-line bg-sidebar">
          {panes.map((p) => (
            <button
              key={p.id}
              role="tab"
              aria-selected={ui.narrowPane === p.id}
              onClick={() => editorStore.setState({ narrowPane: p.id })}
              className={cx('flex-1 h-9 text-xs font-medium border-b-2', ui.narrowPane === p.id ? 'border-accent text-fg' : 'border-transparent text-fg-3')}
            >
              {p.label}
            </button>
          ))}
        </div>
        <div className="flex-1 min-h-0 flex flex-col">
          {ui.narrowPane === 'preview' && preview}
          {ui.narrowPane === 'timeline' && <div className="flex-1 flex flex-col justify-end">{timeline}</div>}
          {ui.narrowPane === 'inspector' && inspector}
          {ui.narrowPane === 'dock' && dock}
        </div>
        {statusBar}
        {exportModal}
      </div>
    );
  }

  const inspectorAsDrawer = layout === 'compact';

  return (
    <div className="h-full flex flex-col min-w-0" data-testid="editor-workstation">
      {topBar}
      <div className="flex-1 min-h-0 flex relative">
        {ui.leftDockOpen && (
          <>
            <div className="shrink-0 border-r border-line" style={{ width: leftW }}>
              {dock}
            </div>
            <ResizeHandle label="Resize left panel" value={leftW} min={220} max={380} onChange={setLeftW} />
          </>
        )}
        <div className="flex-1 min-w-0">{preview}</div>
        {ui.inspectorOpen &&
          (inspectorAsDrawer ? (
            <div className="absolute right-0 top-0 bottom-0 z-30 border-l border-line shadow-2xl" style={{ width: 300 }}>
              {inspector}
            </div>
          ) : (
            <>
              <ResizeHandle label="Resize inspector" value={rightW} min={250} max={380} onChange={setRightW} invert />
              <div className="shrink-0 border-l border-line" style={{ width: rightW }}>
                {inspector}
              </div>
            </>
          ))}
      </div>
      {timeline}
      {statusBar}
      {exportModal}
    </div>
  );
};

// ------------------------------------------------------------------ pieces

const EditorTopBar: React.FC<{ projectName: string; projectId: string; children?: React.ReactNode }> = ({ projectName, projectId, children }) => (
  <header className="h-[52px] shrink-0 px-3 border-b border-line bg-sidebar flex items-center gap-2 min-w-0">
    <nav aria-label="Breadcrumb" className="flex items-center gap-1 text-[13px] min-w-0">
      <Link to="/projects" className="text-fg-3 hover:text-fg shrink-0">
        Projects
      </Link>
      <ChevronRight aria-hidden className="w-3.5 h-3.5 text-fg-3 shrink-0" />
      <Link to={`/projects/${projectId}`} className="font-semibold text-fg truncate max-w-[240px] hover:underline" title={projectName}>
        {projectName}
      </Link>
    </nav>
    {children}
  </header>
);

const SaveIndicator: React.FC<{ persists: boolean; lastLabel: string | null }> = ({ persists, lastLabel }) => (
  <Tooltip content={lastLabel ? `Last change: ${lastLabel}` : 'No changes this session'}>
    <span className="hidden lg:inline text-[11px] text-fg-3 mr-1" tabIndex={0} data-testid="save-indicator">
      {persists ? 'Demo changes saved locally' : 'Unsaved demo session'}
    </span>
  </Tooltip>
);

const ResizeHandle: React.FC<{ label: string; value: number; min: number; max: number; onChange: (v: number) => void; invert?: boolean }> = ({
  label,
  value,
  min,
  max,
  onChange,
  invert,
}) => {
  const clamp = (v: number) => Math.max(min, Math.min(max, v));
  return (
    <div
      role="separator"
      aria-orientation="vertical"
      aria-label={label}
      aria-valuemin={min}
      aria-valuemax={max}
      aria-valuenow={value}
      tabIndex={0}
      onKeyDown={(e) => {
        if (e.key === 'ArrowLeft') onChange(clamp(value + (invert ? 20 : -20)));
        if (e.key === 'ArrowRight') onChange(clamp(value + (invert ? -20 : 20)));
      }}
      onPointerDown={(e) => {
        e.preventDefault();
        const x0 = e.clientX;
        const move = (ev: PointerEvent) => onChange(clamp(value + (invert ? x0 - ev.clientX : ev.clientX - x0)));
        const up = () => {
          window.removeEventListener('pointermove', move);
          window.removeEventListener('pointerup', up);
        };
        window.addEventListener('pointermove', move);
        window.addEventListener('pointerup', up);
      }}
      className="w-1 -mx-0.5 z-20 shrink-0 cursor-col-resize hover:bg-accent/40 focus-visible:bg-accent/50"
    />
  );
};
