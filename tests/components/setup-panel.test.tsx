import React from 'react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { SetupPanel } from '../../apps/desktop/src/features/live/SetupPanel';

const engine = vi.hoisted(() => ({
  claudeStatus: vi.fn(), installClaudeCode: vi.fn(), openClaudeSignIn: vi.fn(), speechModelStatus: vi.fn(), cacheModel: vi.fn(),
}));
vi.mock('../../apps/desktop/src/services/liveEngineRepository', () => ({ liveEngineRepository: engine }));

const missing = { installed: false, version: null, signed_in: false, plan: null, method: null };
const installed = { installed: true, version: '2.1.318 (Claude Code)', signed_in: false, plan: null, method: null };
const ready = { ...installed, signed_in: true, plan: 'team', method: 'claude.ai' };
const noSpeech = { tiny: false, base: false, small: false };

beforeEach(() => {
  vi.resetAllMocks();
  engine.speechModelStatus.mockResolvedValue(noSpeech);
});

describe('setup panel', () => {
  it('on a fresh PC asks to install Claude Code and only counts real checks', async () => {
    engine.claudeStatus.mockResolvedValue(missing);
    render(<SetupPanel engineConnected />);
    expect(await screen.findByText('Set up this PC')).toBeInTheDocument();
    expect(screen.getByText('1 of 3 required items ready · click Install or Sign in below')).toBeInTheDocument();
    expect(screen.getByTestId('setup-engine')).toHaveTextContent('Built in · running');
    expect(screen.getByTestId('setup-claude')).toHaveTextContent('Not installed');
    expect(screen.getByTestId('setup-account')).toHaveTextContent('Install Claude Code first');
    expect(screen.queryByRole('button', { name: 'Sign in' })).toBeNull();
  });

  it('installs Claude Code on click and reports the installer error honestly', async () => {
    const onChanged = vi.fn();
    engine.claudeStatus.mockResolvedValue(missing);
    engine.installClaudeCode.mockRejectedValueOnce('The Claude Code installer failed. Check your internet connection and try again.');
    render(<SetupPanel engineConnected onChanged={onChanged} />);
    fireEvent.click(await screen.findByRole('button', { name: /Install Claude Code/ }));
    expect(await screen.findByRole('alert')).toHaveTextContent('installer failed');
    expect(onChanged).not.toHaveBeenCalled();

    engine.installClaudeCode.mockResolvedValueOnce(installed);
    fireEvent.click(screen.getByRole('button', { name: /Install Claude Code/ }));
    await waitFor(() => expect(screen.getByTestId('setup-claude')).toHaveTextContent('Installed · 2.1.318'));
    expect(onChanged).toHaveBeenCalled();
    expect(screen.getByRole('button', { name: 'Sign in' })).toBeInTheDocument();
  });

  it('opens sign-in and waits until Claude Code reports a signed-in account', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    try {
      engine.claudeStatus.mockResolvedValue(installed);
      engine.openClaudeSignIn.mockResolvedValue(undefined);
      render(<SetupPanel engineConnected />);
      fireEvent.click(await screen.findByRole('button', { name: 'Sign in' }));
      await waitFor(() => expect(screen.getByTestId('setup-account')).toHaveTextContent('Finish signing in in your browser'));
      engine.claudeStatus.mockResolvedValue(ready);
      await act(async () => { await vi.advanceTimersByTimeAsync(3100); });
      await waitFor(() => expect(screen.getByText('This PC is ready')).toBeInTheDocument());
    } finally {
      vi.useRealTimers();
    }
  });

  it('says why a Claude Code file is refused instead of offering a pointless install', async () => {
    engine.claudeStatus.mockResolvedValue({ ...missing, problem: 'Claude Code was found at C:\\x\\claude.exe but its digital signature could not be confirmed as Anthropic\'s, so AgenEditor will not run it.' });
    render(<SetupPanel engineConnected />);
    expect(await screen.findByTestId('setup-claude')).toHaveTextContent('digital signature could not be confirmed');
    expect(screen.queryByRole('button', { name: /Install Claude Code/ })).toBeNull();
  });

  it('reports a failed speech-model check instead of spinning forever', async () => {
    engine.claudeStatus.mockResolvedValue(ready);
    engine.speechModelStatus.mockRejectedValue('the speech model cache could not be checked');
    render(<SetupPanel engineConnected />);
    fireEvent.click(await screen.findByRole('button', { name: /This PC is ready/ }));
    expect(await screen.findByTestId('setup-speech')).toHaveTextContent('Could not check');
    expect(screen.getByRole('button', { name: /Install/ })).toBeInTheDocument();
  });

  it('collapses when everything required is ready and keeps the optional speech model', async () => {
    engine.claudeStatus.mockResolvedValue(ready);
    engine.cacheModel.mockResolvedValue('tiny model cached');
    render(<SetupPanel engineConnected />);
    const toggle = await screen.findByRole('button', { name: /This PC is ready/ });
    expect(toggle).toHaveAttribute('aria-expanded', 'false');
    fireEvent.click(toggle);
    const list = within(screen.getByRole('list', { name: 'Setup checklist' }));
    expect(list.getByTestId('setup-account')).toHaveTextContent('Signed in · Team plan');
    expect(list.getByTestId('setup-speech')).toHaveTextContent('Not installed');
    engine.speechModelStatus.mockResolvedValue({ tiny: true, base: false, small: false });
    fireEvent.click(list.getByRole('button', { name: /Install/ }));
    await waitFor(() => expect(engine.cacheModel).toHaveBeenCalledWith('tiny'));
    await waitFor(() => expect(list.getByTestId('setup-speech')).toHaveTextContent('Installed'));
  });
});
