/** Resources and Costs views (spec §11, §14, §16). Estimates are always labelled as estimates. */

import React, { useEffect, useState } from 'react';
import { Cloud, Save } from 'lucide-react';
import type { ProviderInfo, ResourceConfig, UsageRow } from '../../services/productionRepository';
import { Badge, Button, Switch } from '../../components/ui/primitives';
import { engineErrorText } from '../../utils/engineError';
import { card, field, type ViewProps } from './ProductionWorkspace';
import { COST_KIND, bytes, clock, taskTypeLabel, usd } from './format';

const POOL_LABEL: Record<string, string> = {
  team: 'Production teams', analysis: 'Analysis (transcription)', render: 'Rendering', qa: 'Editorial QA',
};
const SLOTS: { key: keyof ResourceConfig; label: string; min: number; max: number }[] = [
  { key: 'team_slots', label: 'Production teams', min: 1, max: 8 },
  { key: 'analysis_slots', label: 'Analysis slots', min: 1, max: 4 },
  { key: 'render_slots', label: 'Render slots', min: 1, max: 4 },
  { key: 'qa_slots', label: 'QA slots', min: 1, max: 4 },
  { key: 'lease_seconds', label: 'Worker lease (s)', min: 10, max: 3600 },
  { key: 'host_timeout_seconds', label: 'Agent run timeout (s)', min: 30, max: 7200 },
];

export const ResourcesView: React.FC<ViewProps> = ({ overview, repo, run, busy }) => {
  const [config, setConfig] = useState<ResourceConfig | null>(null);
  const [providers, setProviders] = useState<ProviderInfo[]>([]);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [version, setVersion] = useState(0);
  const snapshot = overview.resources;

  useEffect(() => {
    let live = true;
    Promise.all([repo.resources(), repo.providers()])
      .then(([resources, list]) => { if (live) { setConfig(resources.config); setProviders(list); } })
      .catch((err) => { if (live) setLoadError(engineErrorText(err)); });
    return () => { live = false; };
  }, [repo, version]);

  const invalid = config ? SLOTS.find((s) => !Number.isInteger(config[s.key]) || config[s.key] < s.min || config[s.key] > s.max) : null;

  return (
    <div className="space-y-6">
      {loadError && <p role="alert" className="text-sm text-danger">{loadError}</p>}
      <section aria-labelledby="pools-title" className="space-y-2">
        <h2 id="pools-title" className="text-sm font-semibold text-fg-2">Execution permits</h2>
        <ul className="grid gap-2 md:grid-cols-2 xl:grid-cols-4">
          {Object.entries(snapshot.pools).map(([name, pool]) => (
            <li key={name} className={`${card} p-3`}>
              <p className="text-xs text-fg-3">{POOL_LABEL[name] ?? name}</p>
              <p className="text-lg font-semibold tabular-nums">{pool.in_use} / {pool.capacity}</p>
              <p className="text-xs text-fg-3">{pool.in_use >= pool.capacity ? 'Full: new work waits' : 'Free'}</p>
            </li>
          ))}
        </ul>
        <p className="text-xs text-fg-3">
          This computer: {snapshot.cpu_count ?? 'unknown'} CPUs{snapshot.cpu_busy_permille != null ? ` (${Math.round(snapshot.cpu_busy_permille / 10)}% busy)` : ''} · memory {bytes(snapshot.memory_available_bytes)} free of {bytes(snapshot.memory_total_bytes)} · disk {bytes(snapshot.disk_free_bytes)} free · measured {clock(snapshot.measured_at)}
        </p>
      </section>

      {config && (
        <form
          aria-label="Capacity settings"
          className={`${card} space-y-3 p-4`}
          onSubmit={(e) => { e.preventDefault(); void run('Save capacity', () => repo.saveResources(config)).then(() => setVersion((v) => v + 1)); }}
        >
          <h2 className="text-sm font-semibold">Capacity</h2>
          <p className="text-xs text-fg-3">Defaults are conservative, not measured. More teams do not make encoding faster; raise render slots only after benchmarking this computer.</p>
          <div className="grid gap-3 md:grid-cols-3">
            {SLOTS.map((slot) => (
              <label key={slot.key} className="space-y-1 text-sm">{slot.label}
                <input
                  type="number"
                  className={field}
                  min={slot.min}
                  max={slot.max}
                  value={config[slot.key]}
                  onChange={(e) => setConfig({ ...config, [slot.key]: Number(e.target.value) })}
                />
              </label>
            ))}
          </div>
          {invalid && <p className="text-xs text-warning">{invalid.label} must be {invalid.min}–{invalid.max}.</p>}
          <Button type="submit" variant="primary" icon={<Save className="h-4 w-4" />} disabled={busy || Boolean(invalid)}>Save capacity</Button>
        </form>
      )}

      <section aria-labelledby="providers-title" className="space-y-2">
        <h2 id="providers-title" className="text-sm font-semibold text-fg-2">AI providers</h2>
        <p className="text-xs text-fg-3">Deterministic engine tools run first. A provider is used only when it is enabled here, the batch allows cloud AI, and the budget has room.</p>
        <ul className="space-y-2">
          {providers.map((provider) => (
            <li key={provider.id} className={`${card} space-y-1 p-3`}>
              <Switch
                checked={provider.enabled}
                disabled={busy}
                label={provider.label}
                description={provider.adapter_ready ? 'Ready on this computer' : `Not ready: ${provider.adapter_reason}`}
                onChange={(enabled) => void run('Change provider', () => repo.setProviderEnabled(provider.id, enabled)).then(() => setVersion((v) => v + 1))}
              />
              <p className="flex flex-wrap gap-1.5">
                {provider.sends_data_off_device && <Badge tone="warning" icon={<Cloud className="h-3 w-3" />}>Sends data off this computer</Badge>}
                {provider.models.map((m) => <Badge key={m.model}>{m.model}: {m.task_types.map(taskTypeLabel).join(', ')}</Badge>)}
              </p>
            </li>
          ))}
        </ul>
      </section>
    </div>
  );
};

/** Sums cost by kind so measured and estimated money are never added together. */
export function costTotals(rows: UsageRow[]): Partial<Record<UsageRow['cost_kind'], number>> {
  return rows.reduce<Partial<Record<UsageRow['cost_kind'], number>>>((acc, row) => {
    if (row.cost_micros === null) return { ...acc, unknown: acc.unknown ?? 0 };
    return { ...acc, [row.cost_kind]: (acc[row.cost_kind] ?? 0) + row.cost_micros };
  }, {});
}

export const CostsView: React.FC<ViewProps> = ({ detail }) => {
  if (!detail) return <p className="text-sm text-fg-3">Choose a batch to see its AI usage.</p>;
  const rows = detail.usage;
  const totals = costTotals(rows);
  const unpriced = rows.filter((r) => r.cost_micros === null).length;
  const budget = detail.batch.policy.budget_micros;

  return (
    <div className="space-y-4">
      <dl className="grid grid-cols-2 gap-3 text-sm md:grid-cols-4">
        <div className={`${card} p-3`}><dt className="text-xs text-fg-3">Budget</dt><dd className="text-lg font-semibold">{budget === null ? 'No cap' : usd(budget)}</dd></div>
        {(['measured', 'provider_reported', 'estimated'] as const).map((kind) => (
          <div key={kind} className={`${card} p-3`}>
            <dt className="text-xs text-fg-3">{COST_KIND[kind]}</dt>
            <dd className="text-lg font-semibold tabular-nums">{totals[kind] !== undefined ? usd(totals[kind]!) : '—'}</dd>
          </div>
        ))}
      </dl>
      {unpriced > 0 && <p className="text-xs text-warning">{unpriced} requests have no known price; they are not included in any total.</p>}
      {rows.length === 0 ? <p className="text-sm text-fg-3">No AI requests recorded for this batch. Engine-only work costs nothing here.</p> : (
        <table className="w-full text-left text-sm">
          <caption className="sr-only">AI usage</caption>
          <thead className="text-xs text-fg-3">
            <tr>
              <th className="py-1 pr-3 font-medium">When</th><th className="py-1 pr-3 font-medium">Task</th><th className="py-1 pr-3 font-medium">Provider · model</th>
              <th className="py-1 pr-3 font-medium">Tokens in / out</th><th className="py-1 pr-3 font-medium">Cost</th><th className="py-1 font-medium">Kind</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((row, index) => (
              <tr key={`${row.created_at}-${index}`} className="border-t border-line">
                <td className="py-1.5 pr-3 text-fg-3">{clock(row.created_at)}</td>
                <td className="py-1.5 pr-3 capitalize">{taskTypeLabel(row.task_type)}</td>
                <td className="py-1.5 pr-3 text-fg-2">{row.provider_id} · {row.model}</td>
                <td className="py-1.5 pr-3 tabular-nums">{row.tokens_in ?? '—'} / {row.tokens_out ?? '—'}</td>
                <td className="py-1.5 pr-3 tabular-nums">{row.cost_micros === null ? '—' : usd(row.cost_micros)}</td>
                <td className="py-1.5"><Badge tone={row.cost_kind === 'estimated' || row.cost_kind === 'unknown' ? 'warning' : 'neutral'}>{COST_KIND[row.cost_kind]}</Badge></td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
};
