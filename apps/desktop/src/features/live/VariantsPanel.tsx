import React, { useState } from 'react';
import { Download, Film, Layers, Loader2, Play } from 'lucide-react';
import { liveEngineRepository as engine, type EngineJob, type EngineQA, type EngineVariantSummary } from '../../services/liveEngineRepository';
import { engineErrorText } from '../../utils/engineError';
import { Badge, Button } from '../../components/ui/primitives';

const KIND_LABEL: Record<EngineVariantSummary['variant']['kind'], string> = {
  hook: 'Different hook', language: 'Language', localized: 'Localized shots', other: 'Variant',
};

export interface VariantsPanelProps {
  projectId: string;
  variants: EngineVariantSummary[];
  /** All project jobs; the panel picks each variant's latest successful preview. */
  jobs: EngineJob[];
  /** Current main revision; variant renders are computed against it. */
  revision: number;
  /** Claude is working or another action is running. */
  disabled?: boolean;
  /** Called after a render starts so the page can refresh its job list. */
  onJobsChanged: () => void;
  onNotice: (text: string) => void;
}

const status = (item: EngineVariantSummary): { text: string; tone: 'success' | 'warning' | 'danger' } => {
  if (item.errors.length > 0) return { text: "Doesn't fit the current edit", tone: 'danger' };
  if (item.pending_requests.length > 0) {
    const count = item.pending_requests.length;
    return { text: `Waiting for ${count} file${count === 1 ? '' : 's'}`, tone: 'warning' };
  }
  return { text: 'Ready to render', tone: 'success' };
};

/**
 * Variants of the main edit (roadmap Phase 2c). Read-only: Claude creates and changes variants through
 * MCP. Each variant is overrides on the shared main edit and is recomputed from its current revision.
 */
export const VariantsPanel: React.FC<VariantsPanelProps> = ({ projectId, variants, jobs, revision, disabled, onJobsChanged, onNotice }) => {
  const [playing, setPlaying] = useState<{ variantId: string; url: string } | null>(null);
  const [loading, setLoading] = useState<string | null>(null);
  const [working, setWorking] = useState<string | null>(null);
  const [qa, setQA] = useState<Record<string, EngineQA>>({});

  const act = async (variantId: string, task: () => Promise<void>) => {
    setWorking(variantId);
    try {
      await task();
    } catch (error) {
      onNotice(engineErrorText(error));
    } finally {
      setWorking(null);
    }
  };

  const renderFinal = (item: EngineVariantSummary) => act(item.variant.id, async () => {
    const job = await engine.startRender(projectId, revision, true, item.variant.id);
    onJobsChanged();
    onNotice(`Final render of "${item.variant.name}" (variant revision ${job.variant_revision ?? item.variant.revision}, main revision ${job.timeline_revision}) started.`);
  });

  /** The same gate as the main edit: technical QA must not fail before the file can be saved. */
  const checkAndSave = (item: EngineVariantSummary, job: EngineJob) => act(item.variant.id, async () => {
    const report = qa[job.id] ?? await engine.runQA(projectId, job.id);
    setQA((all) => ({ ...all, [job.id]: report }));
    if (report.overall === 'fail') throw new Error(`Technical check failed for "${item.variant.name}", so the file was not saved.`);
    const path = await engine.saveExport(projectId, job.id);
    if (path) onNotice(`Saved "${item.variant.name}" to ${path}`);
  });

  const play = async (variantId: string, job: EngineJob) => {
    setLoading(variantId);
    try {
      setPlaying({ variantId, url: await engine.mediaUrl(projectId, job.id) });
    } catch (error) {
      onNotice(`Could not open the preview: ${engineErrorText(error)}`);
    } finally {
      setLoading(null);
    }
  };

  return (
    <section aria-labelledby="variants-title" className="space-y-3 rounded-2xl border border-line bg-surface p-5">
      <div>
        <h3 id="variants-title" className="flex items-center gap-2 font-semibold"><Layers className="h-4 w-4" /> Variants</h3>
        <p className="text-xs text-fg-3">
          Other versions of this edit. Each one shares the main edit and only changes what is listed, so changes to the main edit reach every variant.
        </p>
      </div>
      <ul className="space-y-3" aria-label="Variants">
        {variants.map((item) => {
          const { variant } = item;
          const badge = status(item);
          const preview = jobs.find((job) => job.variant_id === variant.id && job.kind === 'preview_render' && job.status === 'succeeded');
          const current = preview && preview.variant_revision === variant.revision && preview.timeline_revision === item.main_revision;
          const final = jobs.find((job) => job.variant_id === variant.id && job.kind === 'final_render');
          const finalCurrent = final && final.variant_revision === variant.revision && final.timeline_revision === item.main_revision;
          const finalRunning = final && (final.status === 'queued' || final.status === 'running');
          const report = final ? qa[final.id] : undefined;
          const busy = disabled || working !== null;
          return (
            <li key={variant.id} data-testid={`variant-${variant.id}`} className="space-y-2 rounded-xl border border-line p-4">
              <div className="flex flex-wrap items-start justify-between gap-2">
                <div className="min-w-0">
                  <p className="font-semibold">{variant.name}</p>
                  <p className="text-xs text-fg-3">
                    {KIND_LABEL[variant.kind]}{variant.language ? ` · ${variant.language}` : ''} · revision {variant.revision} on main revision {item.main_revision}
                  </p>
                </div>
                <Badge tone={badge.tone}>{badge.text}</Badge>
              </div>
              {variant.note && <p className="text-sm text-fg-2">{variant.note}</p>}
              {item.errors.length > 0 && <p className="text-sm text-danger">{item.errors[0]} Ask Claude to update this variant.</p>}
              {item.warnings.length > 0 && <p className="text-xs text-warning">{item.warnings.join(' ')}</p>}
              {preview ? (
                <div className="space-y-2">
                  <div className="flex flex-wrap items-center gap-2">
                    <Button size="sm" disabled={loading === variant.id} onClick={() => void play(variant.id, preview)}
                      icon={loading === variant.id ? <Loader2 className="h-4 w-4 animate-spin" /> : <Play className="h-4 w-4" />}>
                      Play preview
                    </Button>
                    {!current && <span className="text-xs text-fg-3">Preview is of an earlier version; ask Claude to render it again.</span>}
                  </div>
                  {playing?.variantId === variant.id && (
                    <video key={playing.url} src={playing.url} controls aria-label={`Preview of ${variant.name}`} className="max-h-72 w-auto max-w-full rounded-lg border border-line bg-black" />
                  )}
                </div>
              ) : (
                <p className="text-xs text-fg-3">Not rendered yet.</p>
              )}
              <div className="flex flex-wrap items-center gap-2 border-t border-line pt-2">
                <Button size="sm" disabled={busy || !item.renderable || Boolean(finalRunning)} onClick={() => void renderFinal(item)}
                  icon={working === variant.id && !final ? <Loader2 className="h-4 w-4 animate-spin" /> : <Film className="h-4 w-4" />}>
                  {final && !finalCurrent ? 'Render final again' : 'Render final'}
                </Button>
                {finalRunning && <span className="text-xs text-fg-3">Final render {Math.round(final.progress * 100)}%</span>}
                {final?.status === 'failed' && <span className="text-xs text-danger">Final render failed{final.error ? `: ${final.error.message}` : ''}</span>}
                {final?.status === 'succeeded' && (
                  <Button size="sm" variant="primary" disabled={busy} onClick={() => void checkAndSave(item, final)}
                    icon={working === variant.id ? <Loader2 className="h-4 w-4 animate-spin" /> : <Download className="h-4 w-4" />}>
                    Check &amp; save
                  </Button>
                )}
                {final?.status === 'succeeded' && !finalCurrent && <span className="text-xs text-fg-3">This final is of an earlier version.</span>}
              </div>
              {report && (
                <p className={`text-xs ${report.overall === 'pass' ? 'text-success' : report.overall === 'fail' ? 'text-danger' : 'text-warning'}`}>
                  Technical check: {report.overall}
                  {report.checks.filter((check) => check.status !== 'pass').map((check) => ` · ${check.check_id}: ${check.measured ?? check.message}`).join('')}
                </p>
              )}
            </li>
          );
        })}
      </ul>
    </section>
  );
};
