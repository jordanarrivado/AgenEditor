/** Review view (spec §12, §15): centralized QA findings, the pinned preview, and the person's decision.
 *
 * Approval is only ever a person's click. It binds the exact reviewed revision and render hash; the
 * engine refuses it if the clip changed after review. */

import React, { useEffect, useState } from 'react';
import { Check, MessageSquareWarning, ShieldCheck } from 'lucide-react';
import type { ClipDetail, Finding } from '../../services/productionRepository';
import { Badge, Button } from '../../components/ui/primitives';
import { engineErrorText } from '../../utils/engineError';
import { card, field, type ViewProps } from './ProductionWorkspace';
import { CLIP_STATUS, REVIEWABLE, clock, type Tone } from './format';

const SEVERITY: Record<Finding['severity'], Tone> = { blocker: 'danger', major: 'warning', minor: 'accent', info: 'neutral' };
const SOURCE: Record<Finding['source'], string> = { ai: 'AI reviewer', human: 'Person', technical: 'Technical check' };

export const ReviewView: React.FC<ViewProps> = ({ detail, repo, run, busy, person }) => {
  const clips = (detail?.clips ?? []).filter((clip) => [...REVIEWABLE, 'human_approved', 'exported', 'delivered'].includes(clip.status));
  const [clipId, setClipId] = useState<string | null>(null);
  const [info, setInfo] = useState<ClipDetail | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [note, setNote] = useState('');
  const [checked, setChecked] = useState<string[]>([]);
  const current = clips.find((clip) => clip.id === clipId) ?? clips[0] ?? null;
  const stamp = current ? `${current.id}:${current.status}:${current.draft_revision}:${current.updated_at}` : '';

  useEffect(() => {
    if (!current) { setInfo(null); return; }
    let live = true;
    repo.clipDetail(current.id)
      .then((next) => { if (live) { setInfo(next); setLoadError(null); } })
      .catch((err) => { if (live) setLoadError(engineErrorText(err)); });
    return () => { live = false; };
    // Reload when the clip's recorded state changes, not on every poll.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [repo, stamp]);

  if (!detail) return <p className="text-sm text-fg-3">Choose a batch to review its clips.</p>;
  if (!current) return <p className="text-sm text-fg-3">No clip has reached review yet. Clips appear here after their preview render and QA.</p>;

  const approvable = clips.filter((clip) => clip.status === 'review_ready');
  const who = person.trim();
  const toggle = (id: string) => setChecked((all) => (all.includes(id) ? all.filter((x) => x !== id) : [...all, id]));
  const preview = info?.artifacts.find((a) => a.kind === 'preview_render' && a.timeline_revision === current.draft_revision) ?? null;
  const previewUrl = preview ? repo.fileUrl(preview.path) : null;
  const findings = (info?.findings ?? []).filter((f) => f.review_revision === current.draft_revision || f.state === 'open');
  const approval = info?.approvals.find((a) => !a.revoked_at) ?? null;

  return (
    <div className="grid gap-5 lg:grid-cols-[18rem_minmax(0,1fr)]">
      <aside aria-label="Clips to review" className="space-y-3">
        <ul className="space-y-1.5">
          {clips.map((clip) => (
            <li key={clip.id} className="flex items-center gap-2">
              {clip.status === 'review_ready' && (
                <input type="checkbox" aria-label={`Include ${clip.title} in approval`} checked={checked.includes(clip.id)} onChange={() => toggle(clip.id)} />
              )}
              <button
                type="button"
                aria-current={clip.id === current.id ? 'true' : undefined}
                onClick={() => setClipId(clip.id)}
                className={`${card} min-w-0 flex-1 p-2.5 text-left text-sm hover:border-accent ${clip.id === current.id ? 'border-accent' : ''}`}
              >
                <span className="block truncate font-medium">{clip.title}</span>
                <Badge tone={CLIP_STATUS[clip.status].tone}>{CLIP_STATUS[clip.status].label}</Badge>
              </button>
            </li>
          ))}
        </ul>
        {approvable.length > 0 && (
          <Button
            variant="success"
            className="w-full justify-center"
            icon={<ShieldCheck className="h-4 w-4" />}
            disabled={busy || !who || checked.filter((id) => approvable.some((c) => c.id === id)).length === 0}
            onClick={() => void run('Approve', () => repo.approve(checked.filter((id) => approvable.some((c) => c.id === id)), who)).then((ok) => ok && setChecked([]))}
          >
            Approve {checked.length || ''} selected
          </Button>
        )}
        {!who && approvable.length > 0 && <p className="text-xs text-warning">Type your name in “Acting as” to approve.</p>}
      </aside>

      <section aria-labelledby="review-clip-title" className="min-w-0 space-y-4">
        <div className="flex flex-wrap items-center gap-2">
          <h2 id="review-clip-title" className="text-lg font-semibold">{current.title}</h2>
          <Badge tone={CLIP_STATUS[current.status].tone}>{CLIP_STATUS[current.status].label}</Badge>
          {current.draft_revision !== null && <Badge>revision {current.draft_revision}</Badge>}
        </div>
        {current.status_reason && <p className="text-sm text-fg-3">{current.status_reason}</p>}
        {loadError && <p role="alert" className="text-sm text-danger">{loadError}</p>}

        <div className="overflow-hidden rounded-xl border border-line bg-black">
          {previewUrl ? (
            <video aria-label={`Preview of revision ${current.draft_revision}`} src={previewUrl} controls className="mx-auto max-h-[28rem]" />
          ) : (
            <p className="p-8 text-center text-sm text-fg-3">
              {repo.simulated ? 'Simulation: no video was rendered.' : preview ? 'The preview file is not playable here.' : 'No verified preview of this revision yet.'}
            </p>
          )}
        </div>
        {preview && <p className="break-all text-xs text-fg-3">Preview SHA-256 {preview.sha256}</p>}

        {approval && (
          <p className="flex items-center gap-2 text-sm text-success">
            <Check className="h-4 w-4" /> Approved by {approval.approved_by} on {clock(approval.approved_at)} · revision {approval.timeline_revision}
          </p>
        )}

        <div className="space-y-2">
          <h3 className="text-sm font-semibold">Reviews</h3>
          {(info?.reviews ?? []).length === 0 ? <p className="text-sm text-fg-3">No review recorded.</p> : (
            <ul className="space-y-2">
              {info!.reviews.map((review) => (
                <li key={review.id} className="rounded-lg border border-line bg-app p-3 text-sm">
                  <p className="flex flex-wrap items-center gap-2">
                    <span className="font-medium">{review.reviewer}</span>
                    <Badge>revision {review.timeline_revision}</Badge>
                    {review.verdict && <Badge tone={review.verdict === 'pass' ? 'success' : 'warning'}>{review.verdict.replace(/_/g, ' ')}</Badge>}
                    <span className="text-xs text-fg-3">{clock(review.created_at)}</span>
                  </p>
                  {review.summary && <p className="mt-1 text-fg-2">{review.summary}</p>}
                  {review.evidence_basis.length > 0 && <p className="mt-1 text-xs text-fg-3">Based on: {review.evidence_basis.join(', ')}</p>}
                </li>
              ))}
            </ul>
          )}
        </div>

        <div className="space-y-2">
          <h3 className="text-sm font-semibold">Findings</h3>
          {findings.length === 0 ? <p className="text-sm text-fg-3">No findings for this revision.</p> : (
            <ul className="space-y-2">
              {findings.map((finding) => (
                <li key={finding.id} className="rounded-lg border border-line bg-app p-3 text-sm">
                  <p className="flex flex-wrap items-center gap-2">
                    <Badge tone={SEVERITY[finding.severity]}>{finding.severity}</Badge>
                    <span className="font-medium capitalize">{finding.category.replace(/_/g, ' ')}</span>
                    <Badge>{finding.state}</Badge>
                    <span className="text-xs text-fg-3">{SOURCE[finding.source]} · confidence {Math.round(finding.confidence * 100)}% · {finding.reference.kind}{finding.reference.id ? ` ${finding.reference.id}` : ''}</span>
                  </p>
                  <p className="mt-1 text-fg-2">{finding.evidence}</p>
                  {finding.suggested_correction && <p className="mt-1 text-xs text-fg-3">Suggested: {finding.suggested_correction}</p>}
                </li>
              ))}
            </ul>
          )}
        </div>

        {(current.status === 'review_ready' || current.status === 'needs_human') && (
          <div className={`${card} space-y-2 p-3`}>
            <h3 className="text-sm font-semibold">Your decision</h3>
            <textarea aria-label="What should change" className={`${field} min-h-16`} value={note} maxLength={2000} onChange={(e) => setNote(e.target.value)} placeholder="What should change? (for a revision request)" />
            <div className="flex flex-wrap gap-2">
              {current.status === 'review_ready' && (
                <Button
                  variant="success"
                  icon={<ShieldCheck className="h-4 w-4" />}
                  disabled={busy || !who}
                  onClick={() => void run('Approve', () => repo.approve([current.id], who, note))}
                >
                  Approve this revision
                </Button>
              )}
              <Button
                icon={<MessageSquareWarning className="h-4 w-4" />}
                disabled={busy || !note.trim()}
                onClick={() => void run('Request changes', () => repo.requestRevision(current.id, note.trim(), who || 'person')).then((ok) => ok && setNote(''))}
              >
                Request changes
              </Button>
              {current.status === 'needs_human' && (
                <Button disabled={busy || !who} onClick={() => void run('Accept findings', () => repo.acceptFindings(current.id, who, note.trim()))}>
                  Accept open findings
                </Button>
              )}
            </div>
            <p className="text-xs text-fg-3">Approval applies to revision {current.draft_revision ?? '—'} and its verified preview only. Any later change needs a new review.</p>
          </div>
        )}
      </section>
    </div>
  );
};
