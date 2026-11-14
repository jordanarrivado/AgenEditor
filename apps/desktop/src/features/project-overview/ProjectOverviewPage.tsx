import React, { useEffect, useState } from 'react';
import { Link, useNavigate, useParams } from 'react-router-dom';
import { Bot, ClipboardCopy, Eye, Layers, Pencil, Play } from 'lucide-react';
import { useProjectWorkspace, pickRevision } from '../../hooks/useProjectWorkspace';
import type { DemoRevision, PlatformTarget } from '../../types';
import {
  PageLayout,
  PLATFORM_LABEL,
  ProjectStatusBadge,
  aspectLabel,
  formatDate,
} from '../../components/projects/ProjectBits';
import { Badge, Button, Card, Field, Input, SectionHeading, Select, Textarea, buttonClass } from '../../components/ui/primitives';
import { EmptyState, ErrorState, LoadingState } from '../../components/ui/StateViews';
import { AgentHandoffDialog } from '../../components/editor/AgentHandoffPanel';
import { KIND_ICON } from '../../components/editor/MediaBin';
import { RenameDialog } from '../project-library/ProjectsPage';
import { RecentActivityFeed } from '../activity/ActivityPage';
import { NotFoundPage } from '../system/NotFoundPage';
import { showToast } from '../../components/common/Toast';
import { copyText } from '../../utils/agentBrief';
import { formatBytes, framesToTimecode } from '../../utils/timecode';
import { DURATION_MAX, DURATION_MIN } from '../../utils/validation';

export const ProjectOverviewPage: React.FC = () => {
  const { projectId } = useParams();
  const ws = useProjectWorkspace(projectId);
  const navigate = useNavigate();
  const [renameOpen, setRenameOpen] = useState(false);
  const [handoffOpen, setHandoffOpen] = useState(false);
  const [brief, setBrief] = useState('');
  const [platform, setPlatform] = useState<PlatformTarget>('instagram');
  const [duration, setDuration] = useState('40');

  useEffect(() => {
    if (!ws.project) return;
    setBrief(ws.project.brief);
    setPlatform(ws.project.platform);
    setDuration(String(Math.round(ws.project.targetDurationFrames / (ws.project.fpsNum / ws.project.fpsDen))));
  }, [ws.project]);

  if (ws.status === 'loading')
    return (
      <div className="p-6">
        <LoadingState rows={5} />
      </div>
    );
  if (ws.status === 'missing') return <NotFoundPage what="project" />;
  if (ws.status === 'error' || !ws.project) return <ErrorState className="m-6" message={ws.error} onRetry={ws.reload} />;

  const p = ws.project;
  const fps = p.fpsNum / p.fpsDen;
  const active = pickRevision(ws.revisions, p, null);
  const durNum = Number(duration);
  const durErr = Number.isInteger(durNum) && durNum >= DURATION_MIN && durNum <= DURATION_MAX ? undefined : `${DURATION_MIN}–${DURATION_MAX} s`;
  const settingsDirty = platform !== p.platform || durNum !== Math.round(p.targetDurationFrames / fps);

  return (
    <PageLayout
      title={p.name}
      eyebrow={
        <div className="flex items-center gap-2 flex-wrap">
          <Link to="/projects" className="text-xs text-fg-3 hover:text-fg">
            Projects
          </Link>
          <span className="text-fg-3 text-xs">/</span>
          <ProjectStatusBadge status={p.status} />
          <Badge>Demo project</Badge>
        </div>
      }
      description={p.description}
      actions={
        <>
          <Button icon={<Pencil className="w-3.5 h-3.5" />} onClick={() => setRenameOpen(true)}>
            Rename
          </Button>
          <Button icon={<Bot className="w-3.5 h-3.5" />} onClick={() => setHandoffOpen(true)}>
            Prepare AI Brief
          </Button>
          <Button
            icon={<Eye className="w-3.5 h-3.5" />}
            disabled={!active}
            title={active ? undefined : 'Create a timeline revision first'}
            onClick={() => navigate(`/projects/${p.id}/review`)}
          >
            Review Current Draft
          </Button>
          <Button variant="primary" icon={<Play className="w-3.5 h-3.5" />} onClick={() => navigate(`/projects/${p.id}/editor`)}>
            Open Editor
          </Button>
        </>
      }
    >
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        <div className="lg:col-span-2 flex flex-col gap-6">
          <section aria-labelledby="brief-h">
            <SectionHeading
              id="brief-h"
              title="Editing brief"
              action={
                <Button
                  size="sm"
                  variant="ghost"
                  icon={<ClipboardCopy className="w-3.5 h-3.5" />}
                  onClick={async () => {
                    const ok = await copyText(`[AgenEditor demo] ${p.name}\n\n${p.brief}`);
                    showToast(ok ? { type: 'success', title: 'Brief copied' } : { type: 'warning', title: 'Clipboard unavailable' });
                  }}
                >
                  Copy brief
                </Button>
              }
            />
            <Card className="p-4">
              <label htmlFor="ov-brief" className="sr-only">
                Editing brief
              </label>
              <Textarea id="ov-brief" rows={5} value={brief} onChange={(e) => setBrief(e.target.value)} />
              <div className="mt-2 flex justify-end">
                <Button
                  size="sm"
                  variant="primary"
                  disabled={brief === p.brief}
                  onClick={async () => {
                    await ws.updateProject({ brief });
                    showToast({ type: 'success', title: 'Brief updated' });
                  }}
                >
                  Save brief
                </Button>
              </div>
            </Card>
          </section>

          <section aria-labelledby="rev-h">
            <SectionHeading id="rev-h" title={`Timeline revisions (${ws.revisions.length})`} />
            {ws.revisions.length === 0 ? (
              <EmptyState
                compact
                icon={<Layers className="w-5 h-5" />}
                title="No revisions yet"
                message="Open the editor to create a sample edit plan."
                action={
                  <Link to={`/projects/${p.id}/editor`} className={buttonClass('primary', 'sm')}>
                    Open Editor
                  </Link>
                }
              />
            ) : (
              <ul className="flex flex-col gap-2">
                {[...ws.revisions].reverse().map((r) => (
                  <li key={r.id}>
                    <Card className="p-3.5 flex flex-wrap items-center gap-3">
                      <span className="w-10 h-10 rounded-lg bg-elevated flex items-center justify-center font-mono text-[13px] font-semibold text-fg">
                        v{r.revisionNumber}
                      </span>
                      <div className="flex-1 min-w-0">
                        <p className="text-[13px] text-fg font-medium flex items-center gap-2">
                          {r.changeSummary ?? r.notes}
                          {r.id === p.activeRevisionId && <Badge tone="accent">Active</Badge>}
                        </p>
                        <p className="text-xs text-fg-3 mt-0.5">
                          {formatDate(r.createdAtIso)} · {framesToTimecode(r.durationFrames, fps)} · {r.tracks.reduce((n, t) => n + t.clips.length, 0)} clips
                        </p>
                        <TrackStrip revision={r} />
                      </div>
                      <div className="flex gap-2">
                        <Link to={`/projects/${p.id}/review?rev=${r.id}`} className={buttonClass('secondary', 'sm')}>
                          Review
                        </Link>
                        <Link to={`/projects/${p.id}/editor?rev=${r.id}`} className={buttonClass('secondary', 'sm')}>
                          Open
                        </Link>
                      </div>
                    </Card>
                  </li>
                ))}
              </ul>
            )}
          </section>

          <section aria-labelledby="assets-h">
            <SectionHeading id="assets-h" title={`Assets (${ws.assets.length})`} />
            {ws.assets.length === 0 ? (
              <EmptyState compact title="No assets" message="Add demo assets from the editor's Media panel." />
            ) : (
              <Card className="divide-y divide-line-subtle">
                {ws.assets.map((a) => (
                  <div key={a.id} className="flex items-center gap-3 px-4 py-2.5">
                    <span aria-hidden className="w-8 h-8 rounded-md flex items-center justify-center text-[#08121A] shrink-0" style={{ background: a.thumbnailColor }}>
                      {KIND_ICON[a.kind]}
                    </span>
                    <div className="min-w-0 flex-1">
                      <p className="text-[13px] text-fg truncate">{a.name}</p>
                      <p className="text-[11px] font-mono text-fg-3 truncate">{a.filename}</p>
                    </div>
                    <span className="text-xs font-mono text-fg-2 hidden sm:inline">
                      {a.durationFrames ? framesToTimecode(a.durationFrames, a.frameRateNum ?? 30) : '—'}
                    </span>
                    <span className="text-xs font-mono text-fg-3 hidden sm:inline w-16 text-right">{formatBytes(a.fileSizeBytes)}</span>
                    <Badge>{a.kind}</Badge>
                  </div>
                ))}
              </Card>
            )}
          </section>
        </div>

        <div className="flex flex-col gap-6">
          <section aria-labelledby="settings-h">
            <SectionHeading id="settings-h" title="Settings" />
            <Card className="p-4 flex flex-col gap-3">
              <Field label="Platform" htmlFor="ov-platform">
                <Select id="ov-platform" value={platform} onChange={(e) => setPlatform(e.target.value as PlatformTarget)}>
                  {(Object.keys(PLATFORM_LABEL) as PlatformTarget[]).map((k) => (
                    <option key={k} value={k}>
                      {PLATFORM_LABEL[k]}
                    </option>
                  ))}
                </Select>
              </Field>
              <Field label="Target duration (s)" htmlFor="ov-dur" error={durErr}>
                <Input id="ov-dur" type="number" value={duration} aria-invalid={!!durErr} onChange={(e) => setDuration(e.target.value)} className="font-mono" />
              </Field>
              <dl className="grid grid-cols-2 gap-y-1 text-xs">
                <dt className="text-fg-3">Canvas</dt>
                <dd className="font-mono text-fg-2 text-right">
                  {aspectLabel(p)} · {p.width}×{p.height}
                </dd>
                <dt className="text-fg-3">Frame rate</dt>
                <dd className="font-mono text-fg-2 text-right">{fps} fps</dd>
                <dt className="text-fg-3">Updated</dt>
                <dd className="text-fg-2 text-right">{formatDate(p.updatedAtIso)}</dd>
              </dl>
              <Button
                size="sm"
                variant="primary"
                disabled={!settingsDirty || !!durErr}
                onClick={async () => {
                  await ws.updateProject({ platform, targetDurationFrames: Math.round(durNum * fps) });
                  showToast({ type: 'success', title: 'Project settings updated' });
                }}
              >
                Save settings
              </Button>
            </Card>
          </section>
          <section aria-labelledby="pact-h">
            <SectionHeading id="pact-h" title="Activity" />
            <RecentActivityFeed projectId={p.id} limit={4} />
          </section>
        </div>
      </div>

      <RenameDialog
        project={renameOpen ? p : null}
        onClose={() => setRenameOpen(false)}
        onSave={async (name) => {
          await ws.updateProject({ name });
          setRenameOpen(false);
          showToast({ type: 'success', title: 'Project renamed' });
        }}
      />
      <AgentHandoffDialog open={handoffOpen} onOpenChange={setHandoffOpen} project={p} assets={ws.assets} revision={active} />
    </PageLayout>
  );
};

/** Miniature, non-interactive rendering of a revision's tracks. */
export const TrackStrip: React.FC<{ revision: DemoRevision }> = ({ revision }) => {
  const color: Record<string, string> = {
    broll: 'bg-track-broll',
    video: 'bg-track-video',
    dialogue: 'bg-track-audio',
    music: 'bg-track-audio/60',
    captions: 'bg-track-caption',
  };
  return (
    <div aria-hidden className="mt-2 flex flex-col gap-[2px] max-w-md">
      {revision.tracks.map((t) => (
        <div key={t.id} className="relative h-1.5 bg-elevated rounded-sm overflow-hidden">
          {t.clips.map((c) => (
            <span
              key={c.id}
              className={`absolute top-0 bottom-0 ${color[t.type]}`}
              style={{ left: `${(c.timelineInFrames / revision.durationFrames) * 100}%`, width: `${(c.durationFrames / revision.durationFrames) * 100}%` }}
            />
          ))}
        </div>
      ))}
    </div>
  );
};

