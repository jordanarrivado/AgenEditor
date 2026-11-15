import React from 'react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { UsageBadge } from '../../apps/desktop/src/features/live/UsageBadge';
import type { ClaudeUsage } from '../../apps/desktop/src/services/liveEngineRepository';

const engine = vi.hoisted(() => ({ claudeUsage: vi.fn(), refreshClaudeUsage: vi.fn(), onClaudeUsage: vi.fn() }));
vi.mock('../../apps/desktop/src/services/liveEngineRepository', () => ({ liveEngineRepository: engine }));

const nowSeconds = () => Math.floor(Date.now() / 1000);
const usage = (session: number, week: number, extra: Partial<ClaudeUsage> = {}): ClaudeUsage => ({
  status: 'allowed',
  five_hour: { utilization: session, resets_at: nowSeconds() + 3600, status: 'allowed' },
  seven_day: { utilization: week, resets_at: nowSeconds() + 5 * 86400, status: 'allowed' },
  using_overage: false,
  captured_at: nowSeconds() - 120,
  ...extra,
});

let emit: (value: ClaudeUsage) => void = () => undefined;

beforeEach(() => {
  vi.resetAllMocks();
  engine.onClaudeUsage.mockImplementation(async (handler) => { emit = handler; return () => undefined; });
});

describe('usage badge', () => {
  it('shows the last reported 5-hour and weekly usage in the header', async () => {
    engine.claudeUsage.mockResolvedValue(usage(0.26, 0.16));
    render(<UsageBadge />);
    const badge = await screen.findByRole('button', { name: 'Claude usage' });
    await waitFor(() => expect(badge).toHaveTextContent('5h 26%'));
    expect(badge).toHaveTextContent('week 16%');
  });

  it('says there is no data instead of inventing numbers', async () => {
    engine.claudeUsage.mockResolvedValue(null);
    render(<UsageBadge />);
    const badge = await screen.findByRole('button', { name: 'Claude usage' });
    expect(badge).toHaveTextContent('usage');
    expect(badge).not.toHaveTextContent('%');
    fireEvent.click(badge);
    expect(await screen.findByText(/No numbers yet/)).toBeInTheDocument();
  });

  it('updates live from a Claude run and shows details with the last run', async () => {
    engine.claudeUsage.mockResolvedValue(usage(0.26, 0.16));
    render(<UsageBadge />);
    const badge = await screen.findByRole('button', { name: 'Claude usage' });
    await waitFor(() => expect(badge).toHaveTextContent('5h 26%'));
    act(() => emit(usage(0.93, 0.4, {
      last_run: { stage: 'execute', model: 'claude-sonnet-5-5', cost_usd: 0.4213, duration_ms: 134000, finished_at: nowSeconds() },
    })));
    expect(badge).toHaveTextContent('5h 93%');
    fireEvent.click(badge);
    expect(await screen.findByText('93% used')).toHaveClass('text-danger');
    expect(screen.getByText('40% used')).toHaveClass('text-success');
    expect(screen.getByText(/Last run: Execute · Sonnet 5.5 · 2m 14s · \$0.42 at API list prices/)).toBeInTheDocument();
    expect(screen.getByText(/As reported by Claude Code/)).toBeInTheDocument();
  });

  it('refreshes on request and reports a failure', async () => {
    engine.claudeUsage.mockResolvedValue(null);
    engine.refreshClaudeUsage.mockRejectedValueOnce('Claude Code did not report usage. Check that you are signed in (Home → Set up this PC).');
    render(<UsageBadge />);
    fireEvent.click(await screen.findByRole('button', { name: 'Claude usage' }));
    fireEvent.click(await screen.findByRole('button', { name: /Refresh/ }));
    expect(await screen.findByRole('alert')).toHaveTextContent('did not report usage');
    engine.refreshClaudeUsage.mockResolvedValueOnce(usage(0.5, 0.2));
    fireEvent.click(screen.getByRole('button', { name: /Refresh/ }));
    expect(await screen.findByText('50% used')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Claude usage' })).toHaveTextContent('5h 50%');
  });
});
