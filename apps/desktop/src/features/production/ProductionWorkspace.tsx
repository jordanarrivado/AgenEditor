/** Production Command Center (spec §16): one workspace with focused views over the shared engine.
 *
 * Every number shown comes from the engine's recorded state (or, in the browser, from labelled
 * simulation data). Nothing here invents progress percentages, costs or QA results. */

import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { AlertTriangle, Pause, Play, Plus, RefreshCw, Search, X } from 'lucide-react';
import type {
  BatchStatusDetail, NewBatch, Overview, ProductionRepository,
} from '../../services/productionRepository';
import { Badge, Button, Switch } from '../../components/ui/primitives';
import { engineErrorText } from '../../utils/engineError';
import { ATTENTION, CLIP_STATUS, rememberName, rememberedName, seconds } from './format';
import { QueueView, TeamsView } from './WorkViews';
import { ReviewView } from './ReviewView';
import { CostsView, ResourcesView } from './SystemViews';
import { DeliveryView } from './DeliveryView';
import { ClientsView } from './ClientsView';
import { MetricsPanel } from './MetricsPanel';

const fieldBox = 'rounded-lg border border-line bg-app px-3 py-2 text-sm text-fg outline-none placeholder:text-fg-3 focus:border-accent';
export const field = `w-full ${fieldBox}`;
export const card = 'rounded-xl border border-line bg-surface';

export type ViewId = 'overview' | 'queue' | 'teams' | 'review' | 'resources' | 'costs' | 'delivery' | 'clients';
const VIEWS: { id: ViewId; label: string }[] = [
  { id: 'overview', label: 'Overview' },
  { id: 'queue', label: 'Queue' },
  { id: 'teams', label: 'Teams' },
  { id: 'review', label: 'Review' },
  { id: 'resources', label: 'Resources' },
  { id: 'costs', label: 'Costs' },
  { id: 'delivery', label: 'Delivery' },
  { id: 'clients', label: 'Clients' },
];

/** Runs one person-initiated operation; reports failure and refreshes either way. */
export type RunAction = (label: string, action: () => Promise<unknown>) => Promise<boolean>;

export interface ViewProps {
  repo: ProductionRepository;
  overview: Overview;
  detail: BatchStatusDetail | null;
  run: RunAction;
  busy: boolean;
  /** Who is acting, for approvals and delivery. Empty until the person types it. */
  person: string;
  now: number;
  projects: { id: string; name: string }[];
}

export interface ProductionWorkspaceProps {
  repository: ProductionRepository;
  /** Projects a new batch can be made from. */
  projects: { id: string; name: string }[];
  /** Milliseconds between refreshes of engine state. */
  refreshMs?: number;
  /** How long each change long-poll may wait in the engine. */
  changeWaitMs?: number;
}

export const ProductionWorkspace: React.FC<ProductionWorkspaceProps> = ({ repository: repo, projects, refreshMs = 15_000, changeWaitMs = 20_000 }) => {
  const [overview, setOverview] = useState<Overview | null>(null);
  const [batchId, setBatchId] = useState<string | null>(null);
  const [detail, setDetail] = useState<BatchStatusDetail | null>(null);
  const [view, setView] = useState<ViewId>('overview');
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [person, setPerson] = useState(rememberedName);
  const [creating, setCreating] = useState(false);
  const [now, setNow] = useState(Date.now());
  const selected = useRef(batchId);
  selected.current = batchId;

  const refresh = useCallback(async () => {
    try {
      const next = await repo.overview();
      setOverview(next);
      const id = selected.current ?? next.batches[0]?.batch.id ?? null;
      if (id !== selected.current) setBatchId(id);
      setDetail(id ? await repo.status(id) : null);
      setNow(Date.now());
    } catch (err) {
      setError(engineErrorText(err));
    }
  }, [repo]);

  useEffect(() => {
    void refresh();
    const timer = window.setInterval(() => void refresh(), refreshMs);
    return () => window.clearInterval(timer);
  }, [refresh, refreshMs, batchId]);

  // Live updates: the engine answers this long-poll as soon as production state changes, so the
  // fallback interval above only matters if the stream fails.
  useEffect(() => {
    let stopped = false;
    let cursor: string | null = null;
    void (async () => {
      while (!stopped) {
        try {
          const notice = await repo.waitForChanges(cursor, changeWaitMs);
          if (stopped) return;
          cursor = notice.cursor;
          if (notice.changed) await refresh();
        } catch {
          if (stopped) return;
          await new Promise((resolve) => window.setTimeout(resolve, 5000));
        }
      }
    })();
    return () => { stopped = true; };
  }, [repo, refresh, changeWaitMs]);

  // Elapsed times ("running for", "updated … ago") advance between refreshes.
  useEffect(() => {
    const timer = window.setInterval(() => setNow(Date.now()), 5000);
    return () => window.clearInterval(timer);
  }, []);

  const run: RunAction = useCallback(async (label, action) => {
    setBusy(true);
    setError(null);
    try {
      await action();
      return true;
    } catch (err) {
      setError(`${label}: ${engineErrorText(err)}`);
      return false;
    } finally {
      setBusy(false);
      await refresh();
    }
  }, [refresh]);

  const choosePerson = (name: string) => {
    setPerson(name);
    rememberName(name);
  };

  if (!overview) {
    return (
      <main className="flex min-h-0 flex-1 items-center justify-center text-sm text-fg-3">
        {error ? <p role="alert" className="text-danger">{error}</p> : 'Loading production state…'}
      </main>
    );
  }

  const props: ViewProps = { repo, overview, detail, run, busy, person, now, projects };
  const reviewCount = detail?.clips.filter((c) => c.status === 'review_ready' || c.status === 'needs_human').length ?? 0;

  return (
    <main aria-labelledby="production-title" className="flex min-h-0 flex-1 flex-col">
      <div className="space-y-3 border-b border-line px-6 pt-5">
        <div className="flex flex-wrap items-center gap-3">
          <h1 id="production-title" className="text-xl font-semibold tracking-tight">Production</h1>
          {repo.simulated && <Badge tone="warning">Demo Mode · Simulation</Badge>}
          <select
            aria-label="Batch"
            className={`${fieldBox} h-9 w-72 max-w-full py-0`}
            value={batchId ?? ''}
            onChange={(e) => setBatchId(e.target.value || null)}
          >
            {overview.batches.length === 0 && <option value="">No batches yet</option>}
            {overview.batches.map((item) => (
              <option key={item.batch.id} value={item.batch.id}>{item.project_name} · {item.batch.name}</option>
            ))}
          </select>
          <Button icon={<Plus className="h-4 w-4" />} onClick={() => setCreating(true)} disabled={projects.length === 0}>New batch</Button>
          <span className="ml-auto" />
          <label className="flex items-center gap-2 whitespace-nowrap text-xs text-fg-2">
            Acting as
            <input aria-label="Your name" className={`${fieldBox} h-8 w-40 py-0`} placeholder="Your name" value={person} maxLength={80} onChange={(e) => choosePerson(e.target.value)} />
          </label>
          <Button variant="ghost" icon={<RefreshCw className="h-4 w-4" />} onClick={() => void refresh()}>Refresh</Button>
        </div>
        {repo.simulated && (
          <p className="text-xs text-fg-3">Sample data held in this browser tab. No media is analysed, rendered, reviewed or packaged, and nothing is sent anywhere.</p>
        )}
        <div role="tablist" aria-label="Production views" className="flex gap-1">
          {VIEWS.map((item) => (
            <button
              key={item.id}
              type="button"
              role="tab"
              aria-selected={view === item.id}
              onClick={() => setView(item.id)}
              className={`-mb-px border-b-2 px-3 py-2 text-sm font-medium ${view === item.id ? 'border-accent text-fg' : 'border-transparent text-fg-3 hover:text-fg'}`}
            >
              {item.label}
              {item.id === 'review' && reviewCount > 0 && <Badge tone="success" className="ml-1.5">{reviewCount}</Badge>}
            </button>
          ))}
        </div>
      </div>

      {error && (
        <div role="alert" className="mx-6 mt-3 flex items-start gap-3 rounded-lg border border-danger/40 bg-danger/10 px-4 py-2 text-sm">
          <AlertTriangle aria-hidden className="mt-0.5 h-4 w-4 shrink-0 text-danger" />
          <span className="min-w-0 flex-1 break-words">{error}</span>
          <button type="button" aria-label="Dismiss error" className="text-fg-3 hover:text-fg" onClick={() => setError(null)}><X className="h-4 w-4" /></button>
        </div>
      )}

      <div role="tabpanel" aria-label={VIEWS.find((v) => v.id === view)?.label} className="min-h-0 flex-1 overflow-y-auto px-6 py-5">
        {creating && (
          <NewBatchForm
            projects={projects}
            busy={busy}
            onCancel={() => setCreating(false)}
            onCreate={async (request) => {
              let created: string | null = null;
              const ok = await run('Create batch', async () => {
                const result = await repo.createBatch(request);
                created = result.batch.id;
                if (result.conflicts.length) {
                  setError(`Instruction conflicts need a decision: ${result.conflicts.map((c) => `${c.field} (${c.message})`).join('; ')}`);
                }
              });
              if (ok) {
                setCreating(false);
                setBatchId(created);
                setView('overview');
              }
            }}
          />
        )}
        {view === 'overview' && <OverviewView {...props} onOpenBatch={setBatchId} />}
        {view === 'queue' && <QueueView {...props} />}
        {view === 'teams' && <TeamsView {...props} />}
        {view === 'review' && <ReviewView {...props} />}
        {view === 'resources' && <ResourcesView {...props} />}
        {view === 'costs' && <CostsView {...props} />}
        {view === 'delivery' && <DeliveryView {...props} />}
        {view === 'clients' && <ClientsView {...props} />}
      </div>
    </main>
  );
};

// ------------------------------------------------------------------ Overview

const OverviewView: React.FC<ViewProps & { onOpenBatch: (id: string) => void }> = ({ overview, detail, run, repo, busy, person, onOpenBatch }) => {
  const summary = overview.batches.find((item) => item.batch.id === detail?.batch.id);
  const [confirmCancel, setConfirmCancel] = useState(false);

  if (overview.batches.length === 0) {
    return <p className="text-sm text-fg-3">No batches yet. Choose <strong>New batch</strong> to discover clips in a project's footage.</p>;
  }

  return (
    <div className="space-y-6">
      <section aria-labelledby="batches-title" className="space-y-2">
        <h2 id="batches-title" className="text-sm font-semibold text-fg-2">All batches</h2>
        <ul className="grid gap-2 md:grid-cols-2 xl:grid-cols-3">
          {overview.batches.map((item) => (
            <li key={item.batch.id}>
              <button
                type="button"
                onClick={() => onOpenBatch(item.batch.id)}
                aria-current={item.batch.id === detail?.batch.id ? 'true' : undefined}
                className={`${card} w-full space-y-1.5 p-3 text-left hover:border-accent ${item.batch.id === detail?.batch.id ? 'border-accent' : ''}`}
              >
                <span className="flex items-center gap-2 font-medium">
                  <span className="min-w-0 flex-1 truncate">{item.batch.name}</span>
                  <Badge>{item.batch.status.replace(/_/g, ' ')}</Badge>
                  {item.batch.paused && <Badge tone="warning">Paused</Badge>}
                </span>
                <span className="block text-xs text-fg-3">{item.project_name} · {item.clips_total} clips · {item.tasks_active} running · {item.tasks_waiting} waiting</span>
                <span className="flex gap-1.5">
                  {item.review_ready > 0 && <Badge tone="success">{item.review_ready} to review</Badge>}
                  {item.attention > 0 && <Badge tone="danger">{item.attention} need attention</Badge>}
                </span>
              </button>
            </li>
          ))}
        </ul>
      </section>

      {detail && (
        <section aria-labelledby="batch-title" className={`${card} space-y-4 p-4`}>
          <div className="flex flex-wrap items-center gap-2">
            <h2 id="batch-title" className="text-lg font-semibold">{detail.batch.name}</h2>
            <Badge>{detail.batch.status.replace(/_/g, ' ')}</Badge>
            {detail.batch.paused && <Badge tone="warning">Paused</Badge>}
            <span className="ml-auto" />
            {detail.batch.status === 'created' && (
              <Button variant="primary" icon={<Search className="h-4 w-4" />} disabled={busy} onClick={() => void run('Start discovery', () => repo.startDiscovery(detail.batch.id))}>
                Find clips
              </Button>
            )}
            {detail.batch.status !== 'completed' && detail.batch.status !== 'cancelled' && (
              <>
                <Button
                  icon={detail.batch.paused ? <Play className="h-4 w-4" /> : <Pause className="h-4 w-4" />}
                  disabled={busy}
                  onClick={() => void run(detail.batch.paused ? 'Resume' : 'Pause', () => repo.setPaused(detail.batch.id, !detail.batch.paused))}
                >
                  {detail.batch.paused ? 'Resume' : 'Pause'}
                </Button>
                {confirmCancel ? (
                  <>
                    <Button variant="danger" disabled={busy} onClick={() => { setConfirmCancel(false); void run('Cancel batch', () => repo.cancelBatch(detail.batch.id)); }}>
                      Confirm cancel
                    </Button>
                    <Button variant="ghost" onClick={() => setConfirmCancel(false)}>Keep</Button>
                  </>
                ) : <Button variant="ghost" onClick={() => setConfirmCancel(true)}>Cancel batch</Button>}
              </>
            )}
          </div>
          {summary && (
            <dl className="grid grid-cols-2 gap-3 text-sm md:grid-cols-4">
              <Stat label="Clips" value={summary.clips_total} />
              <Stat label="Tasks running" value={summary.tasks_active} />
              <Stat label="Tasks waiting" value={summary.tasks_waiting} />
              <Stat label="Ready for review" value={summary.review_ready} />
            </dl>
          )}
          <MetricsPanel
            repo={repo}
            batchId={detail.batch.id}
            refreshKey={JSON.stringify([detail.clip_counts, detail.task_counts, detail.usage.length, detail.deliveries.length])}
          />
          {detail.batch.status === 'awaiting_selection' && <CandidatePicker detail={detail} repo={repo} run={run} busy={busy} person={person} />}
          <Blockers detail={detail} />
          <ClipList detail={detail} repo={repo} run={run} busy={busy} />
        </section>
      )}
    </div>
  );
};

const Stat: React.FC<{ label: string; value: React.ReactNode }> = ({ label, value }) => (
  <div className="rounded-lg border border-line bg-app px-3 py-2">
    <dt className="text-xs text-fg-3">{label}</dt>
    <dd className="text-lg font-semibold tabular-nums">{value}</dd>
  </div>
);

const Blockers: React.FC<{ detail: BatchStatusDetail }> = ({ detail }) => {
  const urgent = detail.clips.filter((clip) => ATTENTION.includes(clip.status));
  if (urgent.length === 0) return null;
  return (
    <div className="space-y-1.5">
      <h3 className="text-sm font-semibold text-danger">Needs attention</h3>
      <ul className="space-y-1">
        {urgent.map((clip) => (
          <li key={clip.id} className="flex flex-wrap items-center gap-2 text-sm">
            <Badge tone={CLIP_STATUS[clip.status].tone}>{CLIP_STATUS[clip.status].label}</Badge>
            <span className="font-medium">{clip.title}</span>
            {clip.status_reason && <span className="text-fg-3">— {clip.status_reason}</span>}
          </li>
        ))}
      </ul>
    </div>
  );
};

const ClipList: React.FC<{ detail: BatchStatusDetail; repo: ProductionRepository; run: RunAction; busy: boolean }> = ({ detail, repo, run, busy }) => {
  const [editing, setEditing] = useState<string | null>(null);
  const [text, setText] = useState('');
  if (detail.clips.length === 0) return null;
  return (
    <table className="w-full text-left text-sm">
      <caption className="sr-only">Clips in this batch</caption>
      <thead className="text-xs text-fg-3">
        <tr><th className="py-1 pr-3 font-medium">#</th><th className="py-1 pr-3 font-medium">Clip</th><th className="py-1 pr-3 font-medium">State</th><th className="py-1 pr-3 font-medium">Revision</th><th className="py-1 font-medium">Detail</th></tr>
      </thead>
      <tbody>
        {detail.clips.map((clip) => {
          // The engine accepts clip instructions only until planning finishes.
          const open = !clip.plan_id && clip.status === 'discovered';
          return (
            <React.Fragment key={clip.id}>
              <tr className="border-t border-line">
                <td className="py-1.5 pr-3 tabular-nums text-fg-3">{clip.ordinal}</td>
                <td className="py-1.5 pr-3 font-medium">{clip.title}</td>
                <td className="py-1.5 pr-3"><Badge tone={CLIP_STATUS[clip.status].tone}>{CLIP_STATUS[clip.status].label}</Badge></td>
                <td className="py-1.5 pr-3 tabular-nums text-fg-2">{clip.current_revision ?? '—'}</td>
                <td className="py-1.5 text-fg-3">
                  {clip.status_reason}
                  {clip.instructions && <span className="block text-xs">Clip instructions: {clip.instructions}</span>}
                  {open && editing !== clip.id && (
                    <button type="button" className="block text-xs text-accent hover:underline" onClick={() => { setEditing(clip.id); setText(clip.instructions ?? ''); }}>
                      {clip.instructions ? 'Edit clip instructions' : 'Add clip instructions'}
                    </button>
                  )}
                </td>
              </tr>
              {open && editing === clip.id && (
                <tr>
                  <td colSpan={5} className="pb-3">
                    <form
                      className="space-y-2"
                      onSubmit={(e) => {
                        e.preventDefault();
                        void run('Save clip instructions', () => repo.setClipInstructions(clip.id, text.trim(), {})).then((ok) => ok && setEditing(null));
                      }}
                    >
                      <textarea aria-label={`Instructions for ${clip.title}`} className={`${field} min-h-16`} value={text} maxLength={10000} onChange={(e) => setText(e.target.value)} placeholder="Only for this clip: hook, emphasis, what to cut…" />
                      <p className="text-xs text-fg-3">Used only if saved before this clip is planned. Pause the batch for more time.</p>
                      <div className="flex gap-2">
                        <Button type="submit" variant="primary" size="sm" disabled={busy}>Save</Button>
                        <Button size="sm" variant="ghost" onClick={() => setEditing(null)}>Cancel</Button>
                      </div>
                    </form>
                  </td>
                </tr>
              )}
            </React.Fragment>
          );
        })}
      </tbody>
    </table>
  );
};

// ------------------------------------------------------------------ Discovery selection

const CandidatePicker: React.FC<{ detail: BatchStatusDetail; repo: ProductionRepository; run: RunAction; busy: boolean; person: string }> = ({
  detail, repo, run, busy, person,
}) => {
  const proposed = detail.candidates.filter((c) => c.status === 'proposed');
  const [chosen, setChosen] = useState<string[]>([]);
  const toggle = (id: string) => setChosen((all) => (all.includes(id) ? all.filter((x) => x !== id) : [...all, id]));

  if (proposed.length === 0) {
    return <p className="text-sm text-fg-3">Discovery found no candidates that fit this batch. Nothing was padded to reach the target.</p>;
  }
  return (
    <fieldset className="space-y-2">
      <legend className="text-sm font-semibold">Choose the clips to produce</legend>
      <p className="text-xs text-fg-3">Every quote and time range below comes from the transcript of your footage.</p>
      <ul className="space-y-2">
        {proposed.map((candidate) => (
          <li key={candidate.id}>
            <label className="flex cursor-pointer gap-3 rounded-lg border border-line bg-app p-3 hover:border-accent">
              <input type="checkbox" className="mt-1" checked={chosen.includes(candidate.id)} onChange={() => toggle(candidate.id)} aria-label={`Select ${candidate.topic}`} />
              <span className="min-w-0 flex-1 space-y-1">
                <span className="flex flex-wrap items-center gap-2 font-medium">
                  #{candidate.rank} {candidate.topic}
                  <Badge>{seconds(candidate.duration_us)}</Badge>
                  <Badge tone="accent">score {candidate.overall_score.toFixed(2)}</Badge>
                  {!candidate.narrative_complete && <Badge tone="warning">Incomplete story</Badge>}
                  <Badge>{candidate.method === 'agent' ? 'Proposed by AI, checked by engine' : 'Deterministic'}</Badge>
                </span>
                <span className="block text-sm text-fg-2">“{candidate.quote}”</span>
                {candidate.caveats.map((caveat) => <span key={caveat} className="block text-xs text-warning">{caveat}</span>)}
              </span>
            </label>
          </li>
        ))}
      </ul>
      <Button
        variant="primary"
        disabled={busy || chosen.length === 0}
        onClick={() => void run('Confirm selection', () => repo.confirmSelection(detail.batch.id, chosen, person.trim() || 'person'))}
      >
        Produce {chosen.length || ''} {chosen.length === 1 ? 'clip' : 'clips'}
      </Button>
    </fieldset>
  );
};

// ------------------------------------------------------------------ New batch

const NewBatchForm: React.FC<{
  projects: { id: string; name: string }[];
  busy: boolean;
  onCancel: () => void;
  onCreate: (request: NewBatch) => void;
}> = ({ projects, busy, onCancel, onCreate }) => {
  const [projectId, setProjectId] = useState(projects[0]?.id ?? '');
  const [name, setName] = useState('');
  const [instructions, setInstructions] = useState('');
  const [count, setCount] = useState('');
  const [minSec, setMinSec] = useState('20');
  const [maxSec, setMaxSec] = useState('60');
  const [qa, setQA] = useState(true);
  const [egress, setEgress] = useState(false);
  const [budget, setBudget] = useState('');

  const parsed = useMemo(() => {
    const min = Number(minSec);
    const max = Number(maxSec);
    const target = count.trim() ? Number(count) : null;
    const cap = budget.trim() ? Number(budget) : null;
    if (!projectId || !name.trim()) return { error: 'Choose a project and name the batch.' };
    if (!Number.isInteger(min) || !Number.isInteger(max) || min < 1 || max > 600 || min > max) return { error: 'Durations are whole seconds, 1–600, minimum ≤ maximum.' };
    if (target !== null && (!Number.isInteger(target) || target < 1 || target > 50)) return { error: 'Number of clips is 1–50, or blank for automatic.' };
    if (cap !== null && (!Number.isFinite(cap) || cap < 0)) return { error: 'Budget is a dollar amount, or blank for no cap.' };
    const request: NewBatch = {
      project_id: projectId,
      name: name.trim(),
      instructions,
      policy: {
        target_mode: target ? 'count' : 'auto',
        target_count: target,
        min_duration_us: min * 1_000_000,
        max_duration_us: max * 1_000_000,
        editorial_qa: qa ? 'required' : 'skip',
        allow_data_egress: egress,
        budget_micros: cap === null ? null : Math.round(cap * 1_000_000),
      },
    };
    return { request };
  }, [projectId, name, instructions, count, minSec, maxSec, qa, egress, budget]);

  return (
    <form
      aria-label="New batch"
      className={`${card} mb-6 space-y-3 p-4`}
      onSubmit={(e) => { e.preventDefault(); if (parsed.request) onCreate(parsed.request); }}
    >
      <h2 className="font-semibold">New batch</h2>
      <div className="grid gap-3 md:grid-cols-2">
        <label className="space-y-1 text-sm">Project
          <select className={field} value={projectId} onChange={(e) => setProjectId(e.target.value)}>
            {projects.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
          </select>
        </label>
        <label className="space-y-1 text-sm">Batch name
          <input className={field} value={name} maxLength={80} onChange={(e) => setName(e.target.value)} placeholder="Episode 12 shorts" />
        </label>
        <label className="space-y-1 text-sm">Number of clips
          <input className={field} inputMode="numeric" value={count} onChange={(e) => setCount(e.target.value)} placeholder="Automatic" />
        </label>
        <div className="grid grid-cols-2 gap-3">
          <label className="space-y-1 text-sm">Min length (s)
            <input className={field} inputMode="numeric" value={minSec} onChange={(e) => setMinSec(e.target.value)} />
          </label>
          <label className="space-y-1 text-sm">Max length (s)
            <input className={field} inputMode="numeric" value={maxSec} onChange={(e) => setMaxSec(e.target.value)} />
          </label>
        </div>
        <label className="space-y-1 text-sm md:col-span-2">Batch instructions
          <textarea className={`${field} min-h-20`} value={instructions} maxLength={10000} onChange={(e) => setInstructions(e.target.value)} placeholder="Audience, tone, what to avoid…" />
        </label>
        <label className="space-y-1 text-sm">AI budget (USD)
          <input className={field} inputMode="decimal" value={budget} onChange={(e) => setBudget(e.target.value)} placeholder="No cap" />
        </label>
        <div className="space-y-1">
          <Switch checked={qa} onChange={setQA} label="Editorial QA" description="An independent AI reviewer checks each clip before you do." />
          <Switch checked={egress} onChange={setEgress} label="Allow cloud AI" description="Lets enabled providers receive transcripts and frames. Off keeps everything on this computer." />
        </div>
      </div>
      {parsed.error && <p className="text-xs text-warning">{parsed.error}</p>}
      <div className="flex gap-2">
        <Button variant="primary" type="submit" disabled={busy || !parsed.request}>Create batch</Button>
        <Button variant="ghost" onClick={onCancel}>Cancel</Button>
      </div>
    </form>
  );
};
