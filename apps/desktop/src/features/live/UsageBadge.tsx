import React, { useEffect, useState } from 'react';
import * as Popover from '@radix-ui/react-popover';
import { Gauge, Loader2, RefreshCw } from 'lucide-react';
import { liveEngineRepository as engine, type ClaudeUsage, type UsageWindow } from '../../services/liveEngineRepository';
import { Button } from '../../components/ui/primitives';
import { engineErrorText } from '../../utils/engineError';

const MODEL_LABELS: Record<string, string> = {
  'claude-opus-5-5': 'Opus 5.5', 'claude-sonnet-5-5': 'Sonnet 5.5', 'claude-haiku-5-5': 'Haiku 5.5', 'claude-fable-5-2': 'Fable 5.2',
};
const STAGE_LABELS: Record<string, string> = { plan: 'Plan', execute: 'Execute', review: 'Review' };

export const percent = (window: UsageWindow | null) => (window ? Math.round(window.utilization * 100) : null);
const tone = (window: UsageWindow | null) => {
  const value = percent(window);
  if (!window || value === null) return 'text-fg-3';
  if (window.status !== 'allowed' || value >= 90) return 'text-danger';
  return value >= 70 ? 'text-warning' : 'text-success';
};
const barTone = (window: UsageWindow | null) => tone(window).replace('text-', 'bg-').replace('bg-fg-3', 'bg-line');

const resetText = (seconds: number | null, now: number) => {
  if (!seconds) return '';
  const date = new Date(seconds * 1000);
  const sameDay = date.toDateString() === new Date(now).toDateString();
  const time = new Intl.DateTimeFormat(undefined, { hour: 'numeric', minute: '2-digit' }).format(date);
  return sameDay ? `resets ${time}` : `resets ${new Intl.DateTimeFormat(undefined, { weekday: 'short', month: 'short', day: 'numeric' }).format(date)}, ${time}`;
};
const ago = (seconds: number, now: number) => {
  const minutes = Math.max(0, Math.round((now / 1000 - seconds) / 60));
  if (minutes < 1) return 'just now';
  if (minutes < 60) return `${minutes} min ago`;
  const hours = Math.round(minutes / 60);
  return hours < 48 ? `${hours} h ago` : `${Math.round(hours / 24)} days ago`;
};
const duration = (ms: number | null) => {
  if (!ms) return null;
  const s = Math.round(ms / 1000);
  return s < 60 ? `${s}s` : `${Math.floor(s / 60)}m ${s % 60}s`;
};

const Meter: React.FC<{ label: string; window: UsageWindow | null; now: number }> = ({ label, window, now }) => {
  const value = percent(window);
  return (
    <div className="space-y-1">
      <div className="flex items-baseline justify-between gap-3 text-sm">
        <span className="font-medium">{label}</span>
        <span className={`font-semibold tabular-nums ${tone(window)}`}>{value === null ? 'no data' : `${value}% used`}</span>
      </div>
      <div className="h-1.5 overflow-hidden rounded-full bg-elevated" role="meter" aria-label={label} aria-valuemin={0} aria-valuemax={100} aria-valuenow={value ?? undefined}>
        <div className={`h-full rounded-full ${barTone(window)}`} style={{ width: `${Math.min(100, value ?? 0)}%` }} />
      </div>
      {window && <p className="text-xs text-fg-3">{window.status !== 'allowed' ? 'Limit reached · ' : ''}{resetText(window.resets_at, now)}</p>}
    </div>
  );
};

/**
 * Header badge with the Claude plan's 5-hour and weekly usage. The numbers are exactly what
 * Claude Code reported during the last Claude run (or a manual refresh), with their age.
 */
export const UsageBadge: React.FC = () => {
  const [usage, setUsage] = useState<ClaudeUsage | null>(null);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [now, setNow] = useState(() => Date.now());

  useEffect(() => {
    let unlisten: (() => void) | undefined;
    let cancelled = false;
    void engine.claudeUsage().then((value) => { if (!cancelled && value) setUsage(value); }).catch(() => undefined);
    engine.onClaudeUsage((value) => { setUsage(value); setNow(Date.now()); })
      .then((stop) => { if (cancelled) stop(); else unlisten = stop; })
      .catch(() => undefined);
    const timer = window.setInterval(() => setNow(Date.now()), 30_000);
    return () => { cancelled = true; unlisten?.(); window.clearInterval(timer); };
  }, []);

  const refresh = () => {
    setRefreshing(true);
    setError(null);
    void engine.refreshClaudeUsage()
      .then((value) => { setUsage(value); setNow(Date.now()); })
      .catch((e) => setError(engineErrorText(e)))
      .finally(() => setRefreshing(false));
  };

  const session = usage?.five_hour ?? null;
  const week = usage?.seven_day ?? null;
  const limited = Boolean(usage && usage.status !== 'allowed');
  const last = usage?.last_run;

  return (
    <Popover.Root>
      <Popover.Trigger asChild>
        <button type="button" aria-label="Claude usage" className="inline-flex shrink-0 items-center gap-2.5 rounded-full border border-line bg-surface px-3.5 py-1.5 text-xs hover:bg-elevated">
          <Gauge aria-hidden className={`h-3.5 w-3.5 ${limited ? 'text-danger' : 'text-fg-3'}`} />
          <span className="text-fg-2">Claude</span>
          {usage ? (
            <>
              <span className={`tabular-nums ${tone(session)}`}>5h {percent(session) ?? '–'}%</span>
              <span aria-hidden className="text-fg-3">·</span>
              <span className={`tabular-nums ${tone(week)}`}>week {percent(week) ?? '–'}%</span>
            </>
          ) : <span className="text-fg-3">usage</span>}
        </button>
      </Popover.Trigger>
      <Popover.Portal>
        <Popover.Content align="end" sideOffset={8} collisionPadding={12} className="z-50 w-80 space-y-4 rounded-xl border border-line bg-surface p-4 text-fg shadow-2xl">
          <div>
            <p className="font-semibold">Claude plan usage</p>
            <p className="text-xs text-fg-3">{usage ? `As reported by Claude Code ${ago(usage.captured_at, now)}.` : 'No numbers yet. They appear after your first Claude run, or press Refresh.'}</p>
          </div>
          {usage && (
            <>
              <Meter label="Current session (5 hours)" window={session} now={now} />
              <Meter label="This week" window={week} now={now} />
              {limited && <p className="text-xs text-danger">Claude is at its limit until the reset time{usage.using_overage ? '; extra usage is being used' : ''}.</p>}
              {last && (
                <p className="text-xs text-fg-2">
                  Last run: {STAGE_LABELS[last.stage] ?? last.stage} · {MODEL_LABELS[last.model] ?? last.model}
                  {duration(last.duration_ms) ? ` · ${duration(last.duration_ms)}` : ''}
                  {last.cost_usd !== null ? ` · $${last.cost_usd.toFixed(2)} at API list prices` : ''}
                </p>
              )}
            </>
          )}
          {error && <p role="alert" className="text-xs text-danger">{error}</p>}
          <div className="flex items-center justify-between gap-3 border-t border-line pt-3">
            <span className="text-[11px] leading-snug text-fg-3">Limits are shared with Claude.ai and Claude Code. Refresh sends one tiny request.</span>
            <Button size="sm" className="shrink-0" disabled={refreshing} onClick={refresh} icon={refreshing ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <RefreshCw className="h-3.5 w-3.5" />}>
              {refreshing ? 'Checking…' : 'Refresh'}
            </Button>
          </div>
        </Popover.Content>
      </Popover.Portal>
    </Popover.Root>
  );
};
