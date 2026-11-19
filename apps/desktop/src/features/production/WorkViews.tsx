/** Queue and Teams views (spec §16): task DAG state and live team assignments, from engine records only. */

import React, { useState } from 'react';
import { RotateCcw } from 'lucide-react';
import type { TaskStatus } from '../../services/productionRepository';
import { Badge, Button } from '../../components/ui/primitives';
import type { ViewProps } from './ProductionWorkspace';
import { TASK_STATUS, clock, elapsed, taskTypeLabel } from './format';

const RETRYABLE: TaskStatus[] = ['failed', 'needs_human'];
const FILTERS: { id: 'all' | 'active' | 'waiting' | 'problems' | 'done'; label: string; match: TaskStatus[] }[] = [
  { id: 'all', label: 'All', match: [] },
  { id: 'active', label: 'Running', match: ['leased', 'running'] },
  { id: 'waiting', label: 'Waiting', match: ['created', 'ready', 'blocked', 'retry_wait'] },
  { id: 'problems', label: 'Problems', match: ['needs_human', 'failed'] },
  { id: 'done', label: 'Done', match: ['succeeded', 'cancelled'] },
];

const blockText = (reason: string | null) => {
  if (!reason) return '';
  if (reason === 'dependencies') return 'waiting for earlier steps';
  if (reason === 'waiting_for_assets') return 'waiting for an approved asset';
  return reason.replace(/_/g, ' ');
};

export const QueueView: React.FC<ViewProps> = ({ detail, repo, run, busy, now }) => {
  const [filter, setFilter] = useState<(typeof FILTERS)[number]['id']>('all');
  if (!detail) return <p className="text-sm text-fg-3">Choose a batch to see its queue.</p>;
  const titles = new Map(detail.clips.map((clip) => [clip.id, clip.title]));
  const rule = FILTERS.find((f) => f.id === filter)!;
  const tasks = detail.tasks.filter((task) => rule.match.length === 0 || rule.match.includes(task.status));

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-2">
        <div role="radiogroup" aria-label="Filter tasks" className="inline-flex rounded-md border border-line bg-app p-0.5">
          {FILTERS.map((item) => {
            const count = item.match.length === 0 ? detail.tasks.length : detail.tasks.filter((t) => item.match.includes(t.status)).length;
            return (
              <button
                key={item.id}
                type="button"
                role="radio"
                aria-checked={filter === item.id}
                onClick={() => setFilter(item.id)}
                className={`rounded px-2.5 py-1 text-xs font-medium ${filter === item.id ? 'bg-elevated text-fg' : 'text-fg-3 hover:text-fg'}`}
              >
                {item.label} <span className="tabular-nums">{count}</span>
              </button>
            );
          })}
        </div>
        {detail.batch.paused && <Badge tone="warning">Batch paused: no new tasks start</Badge>}
      </div>
      {tasks.length === 0 ? <p className="text-sm text-fg-3">No tasks here.</p> : (
        <table className="w-full text-left text-sm">
          <caption className="sr-only">Tasks</caption>
          <thead className="text-xs text-fg-3">
            <tr>
              <th className="py-1 pr-3 font-medium">Task</th>
              <th className="py-1 pr-3 font-medium">Clip</th>
              <th className="py-1 pr-3 font-medium">State</th>
              <th className="py-1 pr-3 font-medium">Attempts</th>
              <th className="py-1 pr-3 font-medium">Why / error</th>
              <th className="py-1 pr-3 font-medium">Updated</th>
              <th className="py-1 font-medium"><span className="sr-only">Actions</span></th>
            </tr>
          </thead>
          <tbody>
            {tasks.map((task) => (
              <tr key={task.id} className="border-t border-line align-top">
                <td className="py-1.5 pr-3 capitalize">{taskTypeLabel(task.type)}{task.cycle > 0 && <span className="text-fg-3"> · round {task.cycle + 1}</span>}</td>
                <td className="py-1.5 pr-3 text-fg-2">{task.clip_id ? titles.get(task.clip_id) ?? task.clip_id : 'Batch'}</td>
                <td className="py-1.5 pr-3"><Badge tone={TASK_STATUS[task.status].tone}>{TASK_STATUS[task.status].label}</Badge></td>
                <td className="py-1.5 pr-3 tabular-nums">{task.attempt_count}/{task.max_attempts}</td>
                <td className="py-1.5 pr-3 text-fg-3">
                  {task.error ? <span className="text-danger">{task.error.code}: {task.error.message}</span> : blockText(task.block_reason)}
                  {task.status === 'retry_wait' && task.not_before && <span className="block">next try after {clock(task.not_before)}</span>}
                </td>
                <td className="py-1.5 pr-3 text-fg-3" title={clock(task.updated_at)}>{elapsed(task.updated_at, now)} ago</td>
                <td className="py-1.5">
                  {RETRYABLE.includes(task.status) && (
                    <Button size="sm" icon={<RotateCcw className="h-3.5 w-3.5" />} disabled={busy} onClick={() => void run('Retry task', () => repo.retryTask(task.id))}>
                      Retry
                    </Button>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
};

export const TeamsView: React.FC<ViewProps> = ({ overview, detail, now }) => {
  const titles = new Map((detail?.clips ?? []).map((clip) => [clip.id, clip.title]));
  const clipName = (id: string | null) => (id ? titles.get(id) ?? id : 'Batch');
  const capacity = overview.resources.pools.team?.capacity ?? null;

  return (
    <div className="space-y-6">
      <section aria-labelledby="teams-title" className="space-y-2">
        <h2 id="teams-title" className="text-sm font-semibold text-fg-2">
          Production teams {capacity !== null && <span className="font-normal text-fg-3">· {overview.teams.length} of {capacity} slots in use</span>}
        </h2>
        {overview.teams.length === 0 ? <p className="text-sm text-fg-3">No team is working on a clip right now.</p> : (
          <ul className="grid gap-2 md:grid-cols-2">
            {overview.teams.map((team) => (
              <li key={team.team_id} className="rounded-lg border border-line bg-surface p-3 text-sm">
                <p className="font-medium">{clipName(team.clip_id)}</p>
                <p className="text-xs text-fg-3">{team.team_id} · assigned {elapsed(team.assigned_at, now)} ago</p>
              </li>
            ))}
          </ul>
        )}
      </section>
      <section aria-labelledby="running-title" className="space-y-2">
        <h2 id="running-title" className="text-sm font-semibold text-fg-2">Running now</h2>
        {overview.running.length === 0 ? <p className="text-sm text-fg-3">Nothing is running.</p> : (
          <table className="w-full text-left text-sm">
            <caption className="sr-only">Running work</caption>
            <thead className="text-xs text-fg-3">
              <tr><th className="py-1 pr-3 font-medium">Task</th><th className="py-1 pr-3 font-medium">Clip</th><th className="py-1 pr-3 font-medium">Worker</th><th className="py-1 font-medium">Running for</th></tr>
            </thead>
            <tbody>
              {overview.running.map((work) => (
                <tr key={work.attempt_id} className="border-t border-line">
                  <td className="py-1.5 pr-3 capitalize">{taskTypeLabel(work.type)}</td>
                  <td className="py-1.5 pr-3 text-fg-2">{clipName(work.clip_id)}</td>
                  <td className="py-1.5 pr-3 text-fg-2">{work.adapter}{work.model ? ` · ${work.model}` : ''}</td>
                  <td className="py-1.5 tabular-nums text-fg-3" title={`started ${clock(work.started_at)}`}>{elapsed(work.started_at, now)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>
    </div>
  );
};
