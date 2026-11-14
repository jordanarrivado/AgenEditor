import React, { useMemo, useState } from 'react';
import { Link, useNavigate, useParams, useSearchParams } from 'react-router-dom';
import { AlertTriangle, CheckCircle2, ChevronDown, CircleDashed, Download, MessageSquarePlus, Pencil, RotateCcw, Trash2 } from 'lucide-react';
import { useProjectWorkspace, pickRevision } from '../../hooks/useProjectWorkspace';
import type { DemoQAItem, DemoRevision } from '../../types';
import { PageLayout, ProjectStatusBadge, formatDate } from '../../components/projects/ProjectBits';
import { Badge, Button, Card, SectionHeading, Select, Segmented, Textarea, buttonClass, cx } from '../../components/ui/primitives';
import { EmptyState, ErrorState, LoadingState } from '../../components/ui/StateViews';
import { ConfirmDialog } from '../../components/ui/Modal';
import { ExportModal } from '../../components/export/ExportModal';
import { TrackStrip } from '../project-overview/ProjectOverviewPage';
import { NotFoundPage } from '../system/NotFoundPage';
import { showToast } from '../../components/common/Toast';
import { framesToTimecode } from '../../utils/timecode';
import { diffRevisions } from '../../utils/revisionDiff';
import { clipAt } from '../../utils/timelineGeometry';

const QA_META: Record<DemoQAItem['state'], { label: string; tone: 'success' | 'warning' | 'neutral'; icon: React.ReactNode }> = {
  sample_pass: { label: 'Sample result · pass', tone: 'success', icon: <CheckCircle2 aria-hidden className="w-3 h-3" /> },
  sample_warning: { label: 'Sample result · warning', tone: 'warning', icon: <AlertTriangle aria-hidden className="w-3 h-3" /> },
  untested: { label: 'Not tested', tone: 'neutral', icon: <CircleDashed aria-hidden className="w-3 h-3" /> },
};

export const ReviewPage: React.FC = () => {
  const { projectId } = useParams();
  const [params, setParams] = useSearchParams();
  const navigate = useNavigate();
  const ws = useProjectWorkspace(projectId);
  const [compareMode, setCompareMode] = useState<'side' | 'tabs'>('side');
  const [tab, setTab] = useState<'a' | 'b'>('b');
  const [openQa, setOpenQa] = useState<Set<string>>(new Set());
  const [noteText, setNoteText] = useState('');
  const [confirmApprove, setConfirmApprove] = useState(false);
  const [exportOpen, setExportOpen] = useState(false);

  const selected = useMemo(() => pickRevision(ws.revisions, ws.project, params.get('rev')), [ws.revisions, ws.project, params]);
  const baseline = useMemo(() => {
    if (!selected) return null;
    const requested = ws.revisions.find((r) => r.id === params.get('base'));
    return requested ?? ws.revisions.filter((r) => r.revisionNumber < selected.revisionNumber).pop() ?? null;
  }, [ws.revisions, selected, params]);

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

  if (!selected) {
    return (
      <PageLayout title={`Review · ${p.name}`}>
        <EmptyState
          title="Nothing to review yet"
          message="This project has no timeline revision. Open the editor and create a sample edit plan first."
          action={
            <Link to={`/projects/${p.id}/editor`} className={buttonClass('primary')}>
              Open Editor
            </Link>
          }
        />
      </PageLayout>
    );
  }

  const diff = baseline ? diffRevisions(baseline, selected) : null;
  const setRev = (key: 'rev' | 'base', id: string) => {
    const next = new URLSearchParams(params);
    next.set(key, id);
    setParams(next, { replace: true });
  };
  const counts = {
    pass: ws.qaItems.filter((q) => q.state === 'sample_pass').length,
    warn: ws.qaItems.filter((q) => q.state === 'sample_warning').length,
    untested: ws.qaItems.filter((q) => q.state === 'untested').length,
  };
  const toggleQa = (id: string) =>
    setOpenQa((s) => {
      const n = new Set(s);
      if (n.has(id)) n.delete(id);
      else n.add(id);
      return n;
    });

  return (
    <PageLayout
      title={`Review · v${selected.revisionNumber}`}
      width="wide"
      eyebrow={
        <div className="flex items-center gap-2 flex-wrap text-xs">
          <Link to={`/projects/${p.id}`} className="text-fg-3 hover:text-fg">
            {p.name}
          </Link>
          <ProjectStatusBadge status={p.status} />
          <Badge tone="warning">Sample QA · demo revisions</Badge>
        </div>
      }
      description={selected.changeSummary ?? selected.notes}
      actions={
        <>
          <Button onClick={() => navigate(`/projects/${p.id}/editor?rev=${selected.id}`)}>Open v{selected.revisionNumber} in editor</Button>
          <Button icon={<Download className="w-3.5 h-3.5" />} onClick={() => setExportOpen(true)}>
            Export settings
          </Button>
          {p.status === 'approved' ? (
            <Button
              icon={<RotateCcw className="w-3.5 h-3.5" />}
              onClick={async () => {
                await ws.updateProject({ status: 'review' });
                showToast({ type: 'info', title: 'Approval withdrawn (demo)' });
              }}
            >
              Withdraw approval
            </Button>
          ) : (
            <>
              <Button
                icon={<Pencil className="w-3.5 h-3.5" />}
                onClick={async () => {
                  await ws.updateProject({ status: 'draft' });
                  showToast({ type: 'info', title: 'Marked “Needs revision”', message: 'Project moved back to Draft (demo).' });
                }}
              >
                Needs revision
              </Button>
              <Button variant="success" icon={<CheckCircle2 className="w-3.5 h-3.5" />} onClick={() => setConfirmApprove(true)}>
                Approve in demo
              </Button>
            </>
          )}
        </>
      }
    >
      {/* Revision pickers */}
      <div className="flex flex-wrap items-center gap-3 mb-4">
        <label className="text-xs text-fg-2 flex items-center gap-2">
          Compare
          <Select
            aria-label="Baseline revision"
            value={baseline?.id ?? ''}
            disabled={ws.revisions.length < 2}
            onChange={(e) => setRev('base', e.target.value)}
            className="w-24"
          >
            {!baseline && <option value="">—</option>}
            {ws.revisions
              .filter((r) => r.id !== selected.id)
              .map((r) => (
                <option key={r.id} value={r.id}>
                  v{r.revisionNumber}
                </option>
              ))}
          </Select>
          with
          <Select aria-label="Revision under review" value={selected.id} onChange={(e) => setRev('rev', e.target.value)} className="w-24">
            {ws.revisions.map((r) => (
              <option key={r.id} value={r.id}>
                v{r.revisionNumber}
              </option>
            ))}
          </Select>
        </label>
        <div className="flex-1" />
        <Segmented
          size="sm"
          label="Comparison layout"
          value={compareMode}
          onChange={setCompareMode}
          options={[
            { value: 'side', label: 'Side by side' },
            { value: 'tabs', label: 'Tabbed' },
          ]}
        />
      </div>

      <div className="grid grid-cols-1 xl:grid-cols-3 gap-6">
        <div className="xl:col-span-2 flex flex-col gap-6">
          {/* Posters */}
          <section aria-label="Revision comparison">
            {compareMode === 'tabs' && baseline && (
              <div role="tablist" className="flex gap-1 mb-2">
                {(['a', 'b'] as const).map((k) => (
                  <button
                    key={k}
                    role="tab"
                    aria-selected={tab === k}
                    onClick={() => setTab(k)}
                    className={cx('h-8 px-3 rounded-md text-[13px] font-medium', tab === k ? 'bg-elevated text-fg' : 'text-fg-3 hover:text-fg')}
                  >
                    v{(k === 'a' ? baseline : selected).revisionNumber}
                  </button>
                ))}
              </div>
            )}
            <div className={cx('grid gap-4', compareMode === 'side' && baseline ? 'grid-cols-1 md:grid-cols-2' : 'grid-cols-1')}>
              {compareMode === 'side' ? (
                <>
                  {baseline && <RevisionPoster revision={baseline} fps={fps} label="Baseline" />}
                  <RevisionPoster revision={selected} fps={fps} label="Under review" highlight />
                </>
              ) : (
                <RevisionPoster
                  revision={tab === 'a' && baseline ? baseline : selected}
                  fps={fps}
                  label={tab === 'a' && baseline ? 'Baseline' : 'Under review'}
                  highlight={tab === 'b' || !baseline}
                />
              )}
            </div>
          </section>

          {/* Change summary */}
          <section aria-labelledby="changes-h">
            <SectionHeading id="changes-h" title={baseline ? `Changes from v${baseline.revisionNumber} to v${selected.revisionNumber}` : 'Changes'} />
            {!diff ? (
              <EmptyState compact title="First revision" message="There is no earlier revision to compare against." />
            ) : (
              <Card className="p-4 text-[13px]">
                <p className="text-fg-2 mb-3">
                  {diff.added.length} added · {diff.removed.length} removed · {diff.changed.length} changed
                  {diff.durationDeltaFrames !== 0 && ` · duration ${diff.durationDeltaFrames > 0 ? '+' : ''}${(diff.durationDeltaFrames / fps).toFixed(1)} s`}
                </p>
                <ul className="flex flex-col gap-1.5" data-testid="revision-diff">
                  {diff.added.map((d) => (
                    <li key={`a-${d.clip.id}`} className="flex gap-2">
                      <Badge tone="success">Added</Badge>
                      <span className="text-fg">{d.clip.label}</span>
                      <span className="text-fg-3">· {d.track}</span>
                    </li>
                  ))}
                  {diff.removed.map((d) => (
                    <li key={`r-${d.clip.id}`} className="flex gap-2">
                      <Badge tone="danger">Removed</Badge>
                      <span className="text-fg">{d.clip.label}</span>
                      <span className="text-fg-3">· {d.track}</span>
                    </li>
                  ))}
                  {diff.changed.map((d) => (
                    <li key={`c-${d.after.id}`} className="flex gap-2">
                      <Badge tone="accent">Changed</Badge>
                      <span className="text-fg">{d.after.label}</span>
                      <span className="text-fg-3">· {d.what.join(', ')}</span>
                    </li>
                  ))}
                  {diff.added.length + diff.removed.length + diff.changed.length === 0 && <li className="text-fg-3">No clip-level differences.</li>}
                </ul>
                {selected.notes && <p className="mt-3 pt-3 border-t border-line text-xs text-fg-2">{selected.notes}</p>}
              </Card>
            )}
          </section>

          {/* Revision history */}
          <section aria-labelledby="hist-h">
            <SectionHeading id="hist-h" title="Revision timeline" />
            <ol className="relative border-l border-line ml-2 flex flex-col gap-3">
              {ws.revisions.map((r) => (
                <li key={r.id} className="pl-4 relative">
                  <span aria-hidden className={cx('absolute -left-[5px] top-1.5 w-2.5 h-2.5 rounded-full', r.id === selected.id ? 'bg-accent' : 'bg-line')} />
                  <button className="text-left" onClick={() => setRev('rev', r.id)}>
                    <span className="text-[13px] font-medium text-fg">v{r.revisionNumber}</span>
                    <span className="text-xs text-fg-3 ml-2">{formatDate(r.createdAtIso)}</span>
                    <span className="block text-xs text-fg-2">{r.changeSummary ?? r.notes}</span>
                  </button>
                </li>
              ))}
            </ol>
          </section>
        </div>

        <div className="flex flex-col gap-6">
          {/* QA */}
          <section aria-labelledby="qa-h">
            <SectionHeading id="qa-h" title="QA checklist" />
            <p className="text-xs text-fg-3 mb-2">
              {counts.pass} sample pass · {counts.warn} sample warning · {counts.untested} not tested. Sample results are illustrative — nothing was measured.
            </p>
            <ul className="flex flex-col gap-1.5" data-testid="qa-list">
              {ws.qaItems.map((q) => {
                const open = openQa.has(q.id);
                return (
                  <li key={q.id}>
                    <Card className="overflow-hidden">
                      <button
                        className="w-full flex items-center gap-2 px-3 py-2.5 text-left"
                        aria-expanded={open}
                        aria-controls={`qa-${q.id}`}
                        onClick={() => toggleQa(q.id)}
                      >
                        <span className="flex-1 text-[13px] text-fg">{q.title}</span>
                        <Badge tone={QA_META[q.state].tone} icon={QA_META[q.state].icon}>
                          {QA_META[q.state].label}
                        </Badge>
                        <ChevronDown aria-hidden className={cx('w-4 h-4 text-fg-3 transition-transform', open && 'rotate-180')} />
                      </button>
                      {open && (
                        <p id={`qa-${q.id}`} className="px-3 pb-3 text-xs text-fg-2 leading-relaxed">
                          {q.detail}
                        </p>
                      )}
                    </Card>
                  </li>
                );
              })}
            </ul>
          </section>

          {/* Notes */}
          <section aria-labelledby="notes-h">
            <SectionHeading id="notes-h" title={`Review notes (${ws.notes.length})`} />
            <form
              className="flex flex-col gap-2 mb-3"
              onSubmit={async (e) => {
                e.preventDefault();
                if (!noteText.trim()) return;
                await ws.addNote({ author: 'You', timecode: framesToTimecode(0, fps), frame: 0, text: noteText.trim(), resolved: false });
                setNoteText('');
                showToast({ type: 'success', title: 'Note added' });
              }}
            >
              <label htmlFor="note-text" className="sr-only">
                New review note
              </label>
              <Textarea id="note-text" rows={2} placeholder="Add a review note…" value={noteText} onChange={(e) => setNoteText(e.target.value)} />
              <Button type="submit" size="sm" variant="primary" disabled={!noteText.trim()} icon={<MessageSquarePlus className="w-3.5 h-3.5" />} className="self-end">
                Add note
              </Button>
            </form>
            {ws.notes.length === 0 ? (
              <EmptyState compact title="No notes yet" />
            ) : (
              <ul className="flex flex-col gap-2" data-testid="review-notes">
                {ws.notes.map((n) => (
                  <li key={n.id} className={cx('p-3 rounded-lg border bg-surface', n.resolved ? 'border-line opacity-70' : 'border-line')}>
                    <div className="flex items-center justify-between gap-2">
                      <span className="text-xs font-semibold text-fg">{n.author}</span>
                      <span className="text-[11px] font-mono text-fg-3">{n.timecode}</span>
                    </div>
                    <p className={cx('text-[13px] text-fg-2 mt-1', n.resolved && 'line-through')}>{n.text}</p>
                    <div className="mt-1.5 flex gap-1">
                      <Button size="sm" variant="ghost" onClick={() => ws.updateNote(n.id, { resolved: !n.resolved })}>
                        {n.resolved ? 'Reopen' : 'Resolve'}
                      </Button>
                      <Button size="sm" variant="ghost" icon={<Trash2 className="w-3.5 h-3.5" />} aria-label={`Remove note by ${n.author}`} onClick={() => ws.removeNote(n.id)}>
                        Remove
                      </Button>
                    </div>
                  </li>
                ))}
              </ul>
            )}
          </section>
        </div>
      </div>

      <ConfirmDialog
        open={confirmApprove}
        onOpenChange={setConfirmApprove}
        title={`Approve v${selected.revisionNumber} in demo?`}
        message={`Marks “${p.name}” as Approved (demo) with v${selected.revisionNumber} as the active revision. ${counts.warn} sample warnings and ${counts.untested} untested checks remain.`}
        confirmLabel="Approve in demo"
        onConfirm={async () => {
          await ws.updateProject({ status: 'approved', activeRevisionId: selected.id });
          showToast({ type: 'success', title: 'Approved in demo', message: 'No delivery was produced.' });
        }}
      />
      <ExportModal open={exportOpen} onOpenChange={setExportOpen} project={p} revision={selected} onViewActivity={() => navigate('/activity')} />
    </PageLayout>
  );
};

/** CSS poster for a revision: hook-frame mock + mini track strip. Never a rendered frame. */
const RevisionPoster: React.FC<{ revision: DemoRevision; fps: number; label: string; highlight?: boolean }> = ({ revision, fps, label, highlight }) => {
  const hookFrame = Math.min(45, revision.durationFrames - 1);
  const cap = clipAt(revision.tracks.find((t) => t.type === 'captions'), hookFrame);
  const clips = revision.tracks.reduce((n, t) => n + t.clips.length, 0);
  return (
    <Card className={cx('p-4 flex gap-4', highlight && 'border-accent/50')}>
      <div aria-hidden className="w-28 shrink-0 aspect-[9/16] rounded-md border border-line bg-gradient-to-b from-[#161F2C] to-[#0A0E17] relative overflow-hidden flex items-end justify-center p-2">
        <span className="absolute top-1.5 left-1.5 text-[9px] font-mono text-white/70 bg-black/50 px-1 rounded">v{revision.revisionNumber}</span>
        <span className="text-[10px] font-bold text-[#FFE600] text-center leading-tight uppercase">{cap?.label ?? ''}</span>
      </div>
      <div className="min-w-0 flex-1">
        <span className="text-[11px] text-fg-3">{label}</span>
        <h3 className="text-[15px] font-semibold text-fg">Revision v{revision.revisionNumber}</h3>
        <p className="text-xs text-fg-3 mt-0.5">
          {framesToTimecode(revision.durationFrames, fps)} · {clips} clips · {formatDate(revision.createdAtIso)}
        </p>
        <p className="text-xs text-fg-2 mt-2 line-clamp-3">{revision.changeSummary ?? revision.notes}</p>
        <TrackStrip revision={revision} />
        <p className="text-[10px] text-fg-3 mt-2">Poster is a placeholder, not a rendered frame.</p>
      </div>
    </Card>
  );
};
