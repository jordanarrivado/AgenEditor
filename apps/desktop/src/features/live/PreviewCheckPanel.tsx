import React, { useEffect, useState } from 'react';
import { Check, Loader2, ScanSearch, TriangleAlert } from 'lucide-react';
import { liveEngineRepository as engine, type EnginePreviewQA } from '../../services/liveEngineRepository';
import { engineErrorText } from '../../utils/engineError';
import { Badge, Button } from '../../components/ui/primitives';

const LABEL: Record<string, string> = {
  preview_format: 'Size, frame rate and length',
  flat_frames: 'Black or blank frames',
  black_bars: 'Black bars',
  caption_audio: 'Captions over silence',
};

export interface PreviewCheckPanelProps {
  projectId: string;
  /** The preview render to check (the latest main-edit preview). */
  jobId: string;
  /** Claude is working; the check waits until it stops. */
  disabled?: boolean;
}

/**
 * Preview check (roadmap Phase 2c): measured on the preview file before the final render. It only warns;
 * the frame grid is for the person to look at, since AgenEditor cannot judge the picture.
 */
export const PreviewCheckPanel: React.FC<PreviewCheckPanelProps> = ({ projectId, jobId, disabled }) => {
  const [result, setResult] = useState<{ report: EnginePreviewQA | null; gridUrl: string | null } | null>(null);
  const [running, setRunning] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let live = true;
    setResult(null);
    setError(null);
    engine.previewQA(projectId, jobId, false).then((value) => { if (live) setResult(value); }).catch(() => undefined);
    return () => { live = false; };
  }, [projectId, jobId]);

  const run = async () => {
    setRunning(true);
    setError(null);
    try {
      setResult(await engine.previewQA(projectId, jobId, true));
    } catch (failure) {
      setError(engineErrorText(failure));
    } finally {
      setRunning(false);
    }
  };

  const report = result?.report ?? null;
  return (
    <section aria-labelledby="preview-check-title" className="space-y-3 rounded-2xl border border-line bg-surface p-4 text-sm">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div>
          <h3 id="preview-check-title" className="flex items-center gap-2 font-semibold"><ScanSearch className="h-4 w-4" /> Preview check</h3>
          <p className="text-xs text-fg-3">Measured on the preview file before the final render. It only points at things to look at.</p>
        </div>
        <div className="flex items-center gap-2">
          {report && <Badge tone={report.overall === 'pass' ? 'success' : 'warning'}>{report.overall === 'pass' ? 'Nothing flagged' : 'Look at the flagged items'}</Badge>}
          <Button size="sm" disabled={disabled || running} onClick={() => void run()}
            icon={running ? <Loader2 className="h-4 w-4 animate-spin" /> : <ScanSearch className="h-4 w-4" />}>
            {report ? 'Check again' : 'Check this preview'}
          </Button>
        </div>
      </div>
      {error && <p className="text-danger">{error}</p>}
      {report && (
        <>
          <ul className="space-y-1" aria-label="Preview check results">
            {report.checks.map((check) => (
              <li key={check.check_id} className="flex items-start gap-2">
                {check.status === 'warn'
                  ? <TriangleAlert aria-hidden className="mt-0.5 h-4 w-4 shrink-0 text-warning" />
                  : <Check aria-hidden className={`mt-0.5 h-4 w-4 shrink-0 ${check.status === 'pass' ? 'text-success' : 'text-fg-3'}`} />}
                <span>
                  <span className="font-medium">{LABEL[check.check_id] ?? check.check_id}</span>
                  <span className="text-fg-2">
                    {' · '}{check.status === 'skipped' ? `not checked (${check.message})` : check.measured}
                  </span>
                  {check.status === 'warn' && check.message && <span className="block text-xs text-fg-3">{check.message}</span>}
                </span>
              </li>
            ))}
          </ul>
          {result?.gridUrl && (
            <figure className="space-y-1">
              <img src={result.gridUrl} alt={`Frame grid of the preview: ${report.frames.length} frames, one from the middle of each clip`}
                className="max-h-96 w-auto max-w-full rounded-lg border border-line bg-black" />
              <figcaption className="text-xs text-fg-3">One frame from the middle of each clip, in order. Check framing, faces and text.</figcaption>
            </figure>
          )}
        </>
      )}
    </section>
  );
};
