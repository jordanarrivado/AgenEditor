import React, { useEffect, useState } from 'react';
import { Check, ClipboardCopy, ImageIcon, Loader2, RotateCcw, Undo2, Upload, Video, X } from 'lucide-react';
import {
  liveEngineRepository as engine,
  type EngineAsset,
  type EngineAssetChecklist,
} from '../../services/liveEngineRepository';
import type { AssetRequest } from '../../utils/assetRequests';
import { isStoryboard, reviewProgress, reviewState, videoOfStoryboard, type ReviewState } from '../../utils/assetChecklist';
import { copyText } from '../../utils/clipboard';
import { engineErrorText } from '../../utils/engineError';
import { Badge, Button } from '../../components/ui/primitives';

type Site = 'chatgpt' | 'flow';
const SITE_LABEL: Record<Site, string> = { chatgpt: 'ChatGPT', flow: 'Google Flow' };

/** Images and storyboards are made in ChatGPT; videos in Google Flow. */
export const siteFor = (request: AssetRequest): Site => (request.kind === 'video' ? 'flow' : 'chatgpt');
/** The tool name written on the imported file so it is labelled AI-generated (external). */
export const toolFor = (request: AssetRequest) => SITE_LABEL[siteFor(request)];
const kindLabel = (request: AssetRequest) => (isStoryboard(request.id) ? 'Storyboard image' : request.kind === 'image' ? 'Image' : 'Video');

const STATE_BADGE: Record<ReviewState, { text: string; tone: 'neutral' | 'warning' | 'success' | 'accent' }> = {
  waiting: { text: 'To make', tone: 'neutral' },
  locked: { text: 'Waiting for storyboard', tone: 'neutral' },
  review: { text: 'Needs your review', tone: 'warning' },
  approved: { text: 'Approved', tone: 'success' },
  skipped: { text: 'Skipped', tone: 'neutral' },
};

const AssetPreview: React.FC<{ projectId: string; asset: EngineAsset }> = ({ projectId, asset }) => {
  const [url, setUrl] = useState<string | null>(null);
  const [failed, setFailed] = useState(false);
  useEffect(() => {
    let live = true;
    setUrl(null);
    setFailed(false);
    engine.assetUrl(projectId, asset.id).then((value) => { if (live) setUrl(value); }).catch(() => { if (live) setFailed(true); });
    return () => { live = false; };
  }, [projectId, asset.id]);
  if (failed) return <p className="text-xs text-danger">Could not load a preview of {asset.original_filename}.</p>;
  if (!url) return <div className="flex h-24 items-center justify-center rounded-lg bg-black"><Loader2 className="h-4 w-4 animate-spin text-fg-3" /></div>;
  return asset.kind === 'image'
    ? <img src={url} alt={`Preview of ${asset.original_filename}`} className="max-h-56 w-auto max-w-full rounded-lg border border-line bg-black object-contain" />
    : <video src={url} controls aria-label={`Preview of ${asset.original_filename}`} className="max-h-56 w-auto max-w-full rounded-lg border border-line bg-black" />;
};

export interface BrollPanelProps {
  projectId: string;
  checklist: EngineAssetChecklist;
  assets: EngineAsset[];
  /** Claude is working; changes wait until it stops. */
  disabled?: boolean;
  /** Request id whose file is being added right now. */
  attaching: string | null;
  /** Opens the file picker for a request (dropping a file on its card is handled by the page). */
  onBrowse: (request: AssetRequest) => void;
  onChange: (list: EngineAssetChecklist) => void;
  onNotice: (text: string) => void;
}

/**
 * "B-roll to make": the images and videos the plan asks for, made in ChatGPT and Google Flow.
 * AgenEditor only copies the prompt and opens the site; the person makes the file, drops it here,
 * and approves it. Execute stays locked until every request is approved or skipped.
 */
export const BrollPanel: React.FC<BrollPanelProps> = ({ projectId, checklist, assets, disabled, attaching, onBrowse, onChange, onNotice }) => {
  const [working, setWorking] = useState<string | null>(null);
  const [confirmClear, setConfirmClear] = useState(false);
  const progress = reviewProgress(checklist);
  const assetFor = (requestId: string) => assets.find((asset) => asset.id === checklist.links[requestId]);

  const run = (requestId: string, task: () => Promise<void>) => {
    setWorking(requestId);
    void task().catch((error) => onNotice(engineErrorText(error))).finally(() => setWorking(null));
  };

  const copyAndOpen = async (request: AssetRequest) => {
    const site = siteFor(request);
    if (!(await copyText(request.prompt))) {
      onNotice('Could not copy automatically. Open "Show the prompt", select the text and copy it yourself.');
      return;
    }
    try {
      await engine.openGenerator(site);
      onNotice(`Prompt copied. ${SITE_LABEL[site]} is opening: paste it there, make the ${request.kind}, save it, then drop it on this card.`);
    } catch (error) {
      onNotice(`Prompt copied, but ${SITE_LABEL[site]} could not be opened (${engineErrorText(error)}). Open it yourself and paste.`);
    }
  };

  const approve = (request: AssetRequest) => run(request.id, async () => {
    onChange(await engine.approveChecklistRequest(projectId, request.id, checklist.version));
  });

  const reset = (request: AssetRequest, andCopy: boolean) => run(request.id, async () => {
    const previous = checklist.links[request.id];
    onChange(await engine.resetChecklistRequest(projectId, request.id, checklist.version));
    // The old file is no longer linked, so take it out of the project (best effort; originals stay on disk).
    if (previous && previous !== 'dropped') void engine.removeAsset(projectId, previous).catch(() => undefined);
    if (andCopy) await copyAndOpen(request);
  });

  const clearList = () => {
    if (!confirmClear) { setConfirmClear(true); return; }
    setConfirmClear(false);
    run('__list', async () => {
      onChange(await engine.saveChecklist(projectId, null, checklist.version));
      onNotice('B-roll list cleared. Files you already added stay in your Assets.');
    });
  };

  const skip = (request: AssetRequest) => run(request.id, async () => {
    const previous = checklist.links[request.id];
    const links = { ...checklist.links, [request.id]: 'dropped' };
    onChange(await engine.saveChecklist(projectId, {
      requests: checklist.requests, links, accepted: {}, review_required: checklist.review_required,
      approved: (checklist.approved ?? []).filter((id) => id !== request.id),
    }, checklist.version));
    if (previous && previous !== 'dropped') void engine.removeAsset(projectId, previous).catch(() => undefined);
  });

  return (
    <section aria-labelledby="broll-title" className="space-y-4 rounded-2xl border border-accent/40 bg-surface p-5">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div>
          <h3 id="broll-title" className="font-semibold">B-roll to make</h3>
          <p className="text-xs text-fg-3">
            Made in ChatGPT and Google Flow, not by AgenEditor. Copy a prompt, make the file there, drop it on its card, then approve it.
          </p>
        </div>
        <div className="flex items-center gap-2">
          <Badge tone={progress.complete ? 'success' : 'warning'} className="px-2 py-1 text-xs">
            {progress.done} of {progress.total} ready{progress.complete ? '' : ' · Execute is locked'}
          </Badge>
          <Button variant={confirmClear ? 'danger' : 'ghost'} size="sm" disabled={disabled || working !== null} onClick={clearList} onBlur={() => setConfirmClear(false)}>
            {confirmClear ? 'Click again to clear' : 'Clear list'}
          </Button>
        </div>
      </div>

      <ul className="space-y-3" aria-label="B-roll requests">
        {checklist.requests.map((request) => {
          const state = reviewState(checklist, request.id);
          const asset = assetFor(request.id);
          const busy = disabled || working === request.id || attaching === request.id;
          const site = siteFor(request);
          const storyboardOf = isStoryboard(request.id) ? videoOfStoryboard(request.id) : null;
          const hasStoryboard = request.kind === 'video' && checklist.requests.some((item) => item.id === `${request.id}_storyboard`);
          const badge = STATE_BADGE[state];
          return (
            <li key={request.id} data-testid={`broll-${request.id}`} data-request={request.id}
              className={`space-y-3 rounded-xl border border-dashed p-4 ${state === 'review' ? 'border-warning/60' : 'border-line'} ${state === 'locked' ? 'opacity-70' : ''}`}>
              <div className="flex flex-wrap items-start gap-3">
                <span aria-hidden className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-accent/12 text-accent">
                  {request.kind === 'video' ? <Video className="h-4 w-4" /> : <ImageIcon className="h-4 w-4" />}
                </span>
                <div className="min-w-0 flex-1">
                  <p className="font-semibold">{request.purpose || request.id}</p>
                  <p className="text-xs text-fg-3">
                    {kindLabel(request)} · {request.aspect} · {SITE_LABEL[site]}
                    {storyboardOf && ` · guides video ${storyboardOf}`}
                  </p>
                </div>
                <Badge tone={badge.tone}>{state === 'approved' && <Check className="h-3 w-3" strokeWidth={3} />}{badge.text}</Badge>
              </div>

              {state === 'locked' && <p className="text-sm text-fg-2">Approve (or skip) the storyboard above first. Then you will attach it in Google Flow with this prompt.</p>}

              {(state === 'waiting' || state === 'review' || state === 'approved') && (
                <details className="rounded-lg border border-line bg-app/50 px-3 py-2 text-sm">
                  <summary className="cursor-pointer font-medium">Show the prompt</summary>
                  <textarea readOnly aria-label={`Prompt for ${request.id}`} value={request.prompt} rows={5} className="mt-2 w-full resize-y rounded-md border border-line bg-app p-2 text-xs text-fg" />
                </details>
              )}

              {asset && state !== 'waiting' && <AssetPreview projectId={projectId} asset={asset} />}

              {state === 'waiting' && (
                <div className="space-y-2">
                  {hasStoryboard && <p className="text-xs text-fg-2">In Google Flow, attach the approved storyboard image you saved from ChatGPT, then paste this prompt.</p>}
                  <div className="flex flex-wrap items-center gap-2">
                    <Button variant="primary" size="lg" disabled={busy} onClick={() => void copyAndOpen(request)} icon={<ClipboardCopy className="h-4 w-4" />}>
                      Copy prompt &amp; open {SITE_LABEL[site]}
                    </Button>
                    <Button size="lg" disabled={busy} onClick={() => onBrowse(request)}
                      icon={attaching === request.id ? <Loader2 className="h-4 w-4 animate-spin" /> : <Upload className="h-4 w-4" />}>Browse…</Button>
                    <Button variant="ghost" size="lg" disabled={busy} onClick={() => skip(request)}>Skip · edit without it</Button>
                  </div>
                  <p className="text-xs text-fg-3">Or drop the finished {request.kind} anywhere on this card.</p>
                </div>
              )}

              {state === 'locked' && (
                <Button variant="ghost" size="lg" disabled={busy} onClick={() => skip(request)}>Skip · edit without it</Button>
              )}

              {state === 'review' && (
                <div className="space-y-2">
                  <p className="text-sm text-fg-2">
                    {isStoryboard(request.id)
                      ? 'Check the frames are in the right order and the style is consistent. Approving unlocks the video.'
                      : `Watch or look at it before approving. AgenEditor cannot judge the ${request.kind} for you.`}
                  </p>
                  <div className="flex flex-wrap items-center gap-2">
                    <Button variant="primary" size="lg" disabled={busy} onClick={() => approve(request)} icon={<Check className="h-4 w-4" />}>
                      {isStoryboard(request.id) ? 'Approve storyboard' : 'Approve'}
                    </Button>
                    <Button size="lg" disabled={busy} onClick={() => reset(request, true)} icon={<RotateCcw className="h-4 w-4" />}>Redo</Button>
                    <Button size="lg" disabled={busy} onClick={() => onBrowse(request)} icon={<Upload className="h-4 w-4" />}>Replace file…</Button>
                    <Button variant="ghost" size="lg" disabled={busy} onClick={() => skip(request)} icon={<X className="h-4 w-4" />}>Skip</Button>
                  </div>
                </div>
              )}

              {state === 'approved' && (
                <div className="flex flex-wrap items-center gap-2">
                  <Button size="lg" disabled={busy} onClick={() => reset(request, true)} icon={<RotateCcw className="h-4 w-4" />}>Redo</Button>
                  <Button variant="ghost" size="lg" disabled={busy} onClick={() => onBrowse(request)} icon={<Upload className="h-4 w-4" />}>Replace file…</Button>
                  {asset?.provenance?.origin === 'generated-external' && <span className="text-xs text-warning">AI-generated (external) · {asset.provenance.tool_name}</span>}
                </div>
              )}

              {state === 'skipped' && (
                <div className="flex flex-wrap items-center gap-2">
                  <p className="text-sm text-fg-2">Skipped. The edit will go ahead without it.</p>
                  <Button variant="ghost" size="lg" disabled={busy} onClick={() => reset(request, false)} icon={<Undo2 className="h-4 w-4" />}>Undo skip</Button>
                </div>
              )}
            </li>
          );
        })}
      </ul>
    </section>
  );
};
