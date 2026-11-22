import React from 'react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { ChatPanel } from '../../apps/desktop/src/features/live/ChatPanel';
import type { ChatProposalRecord, ChatStatus } from '../../apps/desktop/src/services/liveEngineRepository';

const engine = vi.hoisted(() => ({
  chatStatus: vi.fn(), chatHistory: vi.fn(), chatSetConsent: vi.fn(), chatSetKey: vi.fn(), chatRemoveKey: vi.fn(),
  chatSend: vi.fn(), chatApply: vi.fn(),
}));
vi.mock('../../apps/desktop/src/services/liveEngineRepository', () => ({ liveEngineRepository: engine }));

const status = (extra: Partial<ChatStatus> = {}): ChatStatus => ({
  consent: true, keys: { anthropic: true, openai: false, gemini: false },
  default_models: { anthropic: 'claude-opus-5-5', openai: '', gemini: '' },
  data_sent: ['your message', 'transcript words with their word IDs and times (up to 1,500 words)'], ...extra,
});
const proposal = (extra: Partial<ChatProposalRecord> = {}): ChatProposalRecord => ({
  id: 'proposal_1', base_revision: 2, status: 'proposed', applied_revision: null,
  plan: { target_duration_frames: 1200, segments: [{ asset_id: 'a', first_word_id: 'w1', last_word_id: 'w9', rationale: 'r' }, { asset_id: 'a', first_word_id: 'w10', last_word_id: 'w19', rationale: 'r' }] },
  result: { valid: true, disposition: 'valid', issues: [{ message: 'cut may end mid-thought', severity: 'warning' }] }, ...extra,
});
const onApplied = vi.fn();

beforeEach(() => {
  vi.resetAllMocks();
  engine.chatStatus.mockResolvedValue(status());
  engine.chatHistory.mockResolvedValue({ messages: [], proposals: [] });
});

describe('built-in chat panel', () => {
  it('asks for a key and for consent before a message can be sent, without showing the key again', async () => {
    engine.chatStatus.mockResolvedValueOnce(status({ consent: false, keys: { anthropic: false, openai: false, gemini: false } }))
      .mockResolvedValue(status({ consent: false }));
    engine.chatSetKey.mockResolvedValue({ provider: 'anthropic', has_key: true });
    render(<ChatPanel projectId="p" revision={2} onApplied={onApplied} />);
    await waitFor(() => expect(screen.getByLabelText('Model')).toHaveValue('claude-opus-5-5'));
    expect(screen.getByLabelText('Message')).toBeDisabled();
    fireEvent.change(screen.getByLabelText('API key'), { target: { value: 'sk-secret' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save key' }));
    await waitFor(() => expect(engine.chatSetKey).toHaveBeenCalledWith('anthropic', 'sk-secret'));
    expect(await screen.findByText(/Key saved in Windows Credential Manager/)).toBeInTheDocument();
    expect(screen.queryByDisplayValue('sk-secret')).toBeNull();
    expect(screen.getByText(/transcript words with their word IDs/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Allow for this project' }));
    await waitFor(() => expect(engine.chatSetConsent).toHaveBeenCalledWith('p', true));
  });

  it('sends a message, shows the checked proposal and applies it only when asked', async () => {
    engine.chatSend.mockResolvedValue({
      messages: [
        { id: 'm1', role: 'user', text: 'Make a 40 second cut', provider: 'anthropic', model: 'claude-opus-5-5', proposal_id: null, created_at: '' },
        { id: 'm2', role: 'assistant', text: 'Two passages that explain it.', provider: 'anthropic', model: 'claude-opus-5-5', proposal_id: 'proposal_1', created_at: '' },
      ],
      proposal: proposal(),
    });
    engine.chatApply.mockResolvedValue(proposal({ status: 'applied', applied_revision: 3 }));
    render(<ChatPanel projectId="p" revision={2} onApplied={onApplied} />);
    fireEvent.change(await screen.findByLabelText('Message'), { target: { value: 'Make a 40 second cut' } });
    fireEvent.click(screen.getByRole('button', { name: /Send/ }));
    await waitFor(() => expect(engine.chatSend).toHaveBeenCalledWith('p', 'anthropic', 'claude-opus-5-5', 'Make a 40 second cut'));
    const card = within(await screen.findByTestId('proposal-proposal_1'));
    expect(card.getByText('Checked · not applied')).toBeInTheDocument();
    expect(card.getByText('cut may end mid-thought')).toBeInTheDocument();
    expect(engine.chatApply).not.toHaveBeenCalled();
    fireEvent.click(card.getByRole('button', { name: 'Apply as revision 3' }));
    await waitFor(() => expect(engine.chatApply).toHaveBeenCalledWith('p', 'proposal_1', 2));
    expect(await card.findByText(/Saved as revision 3/)).toBeInTheDocument();
    expect(onApplied).toHaveBeenCalled();
  });

  it('does not offer to apply a proposal made against an older revision, and shows engine errors', async () => {
    engine.chatHistory.mockResolvedValue({
      messages: [{ id: 'm2', role: 'assistant', text: 'Here is a cut.', provider: 'openai', model: 'x', proposal_id: 'proposal_1', created_at: '' }],
      proposals: [proposal({ base_revision: 1 })],
    });
    engine.chatSend.mockRejectedValue(new Error('OpenAI rate limit or quota reached; try again later'));
    render(<ChatPanel projectId="p" revision={2} onApplied={onApplied} />);
    const card = within(await screen.findByTestId('proposal-proposal_1'));
    expect(card.getByRole('button', { name: /Apply as revision/ })).toBeDisabled();
    expect(card.getByText(/The edit changed since this proposal/)).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText('Message'), { target: { value: 'again' } });
    fireEvent.click(screen.getByRole('button', { name: /Send/ }));
    expect(await screen.findByRole('alert')).toHaveTextContent('rate limit');
  });
});
