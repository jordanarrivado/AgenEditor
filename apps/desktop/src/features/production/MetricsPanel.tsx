/** Measured performance for one batch (spec §20 success indicators).
 *
 * Shows only what the engine recorded. Rates are computed from recorded counts and elapsed times;
 * anything not observed is shown as "not measured yet", never as zero or an estimate. */

import React, { useEffect, useState } from 'react';
import type { ProductionMetrics, ProductionRepository } from '../../services/productionRepository';
import { engineErrorText } from '../../utils/engineError';
import { bytes, durationMs, usd } from './format';

const NOT_YET = 'not measured yet';

/** Approved clips per hour of batch time, to one decimal; null until a clip is approved. */
export function approvedPerHour(metrics: ProductionMetrics): string | null {
  if (!metrics.clips_approved || !metrics.last_approval_ms) return null;
  const perHour = (metrics.clips_approved * 3_600_000) / metrics.last_approval_ms;
  return perHour.toFixed(1);
}

/** Recorded (measured + provider-reported) spend per approved clip; estimates are reported separately. */
export function costPerApproved(metrics: ProductionMetrics): { recorded: string | null; estimated: string | null } {
  if (!metrics.clips_approved) return { recorded: null, estimated: null };
  const recorded = metrics.cost.measured_micros + metrics.cost.provider_reported_micros;
  return {
    recorded: recorded ? usd(Math.round(recorded / metrics.clips_approved)) : null,
    estimated: metrics.cost.estimated_micros ? usd(Math.round(metrics.cost.estimated_micros / metrics.clips_approved)) : null,
  };
}

const percent = (permille: number | null) => (permille === null ? null : `${(permille / 10).toFixed(0)}%`);
const POOL_LABEL: Record<string, string> = { team: 'Team', analysis: 'Analysis', render: 'Render', qa: 'QA' };

/** "x% avg · y% peak", or null when nothing was sampled. */
export function pressureText(mean: number | null, max: number | null): string | null {
  return mean === null || max === null ? null : `${percent(mean)} avg · ${percent(max)} peak`;
}

const Row: React.FC<{ label: string; value: React.ReactNode; hint?: string }> = ({ label, value, hint }) => (
  <div className="rounded-lg border border-line bg-app px-3 py-2">
    <dt className="text-xs text-fg-3">{label}</dt>
    <dd className="text-sm font-semibold tabular-nums">{value}</dd>
    {hint && <dd className="text-xs text-fg-3">{hint}</dd>}
  </div>
);

export const MetricsPanel: React.FC<{ repo: ProductionRepository; batchId: string; refreshKey: string }> = ({ repo, batchId, refreshKey }) => {
  const [metrics, setMetrics] = useState<ProductionMetrics | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let live = true;
    repo.metrics(batchId)
      .then((next) => { if (live) { setMetrics(next); setError(null); } })
      .catch((err) => { if (live) setError(engineErrorText(err)); });
    return () => { live = false; };
  }, [repo, batchId, refreshKey]);

  if (error) return <p role="alert" className="text-sm text-danger">Measurements unavailable: {error}</p>;
  if (!metrics) return null;

  const rate = approvedPerHour(metrics);
  const cost = costPerApproved(metrics);
  const stats = (s: ProductionMetrics['queue_wait']) =>
    s.samples ? `${durationMs(s.mean_ms)} avg · ${durationMs(s.p95_ms)} p95` : NOT_YET;

  return (
    <section aria-labelledby="metrics-title" className="space-y-2">
      <h3 id="metrics-title" className="text-sm font-semibold">Measured performance</h3>
      <dl className="grid grid-cols-2 gap-2 md:grid-cols-4">
        <Row
          label="Approved clips per hour"
          value={rate ?? NOT_YET}
          hint={rate ? `${metrics.clips_approved} approved in ${durationMs(metrics.last_approval_ms)}` : undefined}
        />
        <Row label="First clip ready for review" value={metrics.first_review_ready_ms === null ? NOT_YET : durationMs(metrics.first_review_ready_ms)} hint="after the batch was created" />
        <Row label="Queue wait" value={stats(metrics.queue_wait)} hint={metrics.queue_wait.samples ? `${metrics.queue_wait.samples} tasks` : undefined} />
        <Row label="Work time per attempt" value={stats(metrics.attempt_run)} hint={metrics.attempt_run.samples ? `${metrics.attempt_run.samples} attempts` : undefined} />
        <Row
          label="Revision rounds"
          value={metrics.clips_reached_review ? `${metrics.revision_tasks} for ${metrics.clips_reached_review} reviewed clips` : NOT_YET}
        />
        <Row label="Checkpoint reuse" value={`${metrics.checkpoint_reuses} of ${metrics.tasks_succeeded} finished tasks`} />
        <Row
          label="Unsuccessful attempts"
          value={`${metrics.attempts_unsuccessful} of ${metrics.attempts_total}`}
          hint={metrics.tasks_failed ? `${metrics.tasks_failed} tasks failed` : undefined}
        />
        <Row
          label="AI cost per approved clip"
          value={cost.recorded ?? (cost.estimated ? `${cost.estimated} (estimated)` : NOT_YET)}
          hint={cost.recorded && cost.estimated ? `plus ${cost.estimated} estimated` : metrics.cost.unpriced_requests ? `${metrics.cost.unpriced_requests} unpriced requests not included` : undefined}
        />
      </dl>
      <h3 className="pt-1 text-sm font-semibold">Machine pressure during this batch</h3>
      {metrics.pressure.samples === 0 ? (
        <p className="text-sm text-fg-3">No samples yet. The engine records CPU, memory, disk and slot use every 30 seconds while it runs.</p>
      ) : (
        <dl className="grid grid-cols-2 gap-2 md:grid-cols-4">
          <Row label="CPU busy" value={pressureText(metrics.pressure.cpu_busy_mean_permille, metrics.pressure.cpu_busy_max_permille) ?? NOT_YET} hint={`${metrics.pressure.samples} samples`} />
          <Row
            label="Memory used"
            value={pressureText(metrics.pressure.memory_used_mean_permille, metrics.pressure.memory_used_max_permille) ?? NOT_YET}
            hint={metrics.pressure.memory_available_min_bytes !== null ? `lowest free ${bytes(metrics.pressure.memory_available_min_bytes)}` : undefined}
          />
          <Row label="Lowest free disk" value={metrics.pressure.disk_free_min_bytes === null ? NOT_YET : bytes(metrics.pressure.disk_free_min_bytes)} />
          <Row
            label="Slots full"
            value={Object.entries(metrics.pressure.pool_full_samples).filter(([, n]) => n > 0).map(([pool, n]) => `${POOL_LABEL[pool] ?? pool} ${n}×`).join(' · ') || 'never'}
            hint={`of ${metrics.pressure.samples} samples; full slots make new work wait`}
          />
        </dl>
      )}
      <p className="text-xs text-fg-3">
        {repo.simulated
          ? 'Simulation: sample values, not measurements.'
          : 'From engine records on this computer. Compare runs only with the same footage, settings, budget and hardware.'}
      </p>
    </section>
  );
};
