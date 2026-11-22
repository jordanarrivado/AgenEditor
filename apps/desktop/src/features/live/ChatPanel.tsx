import React, { useCallback, useEffect, useState } from 'react';
import { ArrowRight, Check, KeyRound, Loader2, Lock } from 'lucide-react';
import {
  liveEngineRepository as engine,
  type ChatMessageRecord,
  type ChatProposalRecord,
  type ChatProviderId,
  type ChatStatus,
} from '../../services/liveEngineRepository';
import { engineErrorText } from '../../utils/engineError';
import { Badge, Button } from '../../components/ui/primitives';

const PROVIDERS: { id: ChatProviderId; label: string }[] = [
  { id: 'anthropic', label: 'Claude (Anthropic)' },
  { id: 'openai', label: 'OpenAI' },
  { id: 'gemini', label: 'Gemini (Google)' },
];
const field = 'w-full rounded-lg border border-line bg-app px-2.5 py-1.5 text-sm text-fg outline-none placeholder:text-fg-3 focus:border-accent';
const STATUS_TEXT: Record<ChatProposalRecord['status'], { text: string; tone: 'success' | 'warning' | 'danger' | 'neutral' }> = {
  proposed: { text: 'Checked · not applied', tone: 'warning' },
  applied: { text: 'Applied', tone: 'success' },
  stale: { text: 'Out of date', tone: 'neutral' },
  rejected: { text: 'Did not pass the checks', tone: 'danger' },
};

export interface ChatPanelProps {
  projectId: string;
  /** Current revision; a proposal applies only on top of the revision it was made against. */
  revision: number;
  /** Called after a proposal becomes a new revision. */
  onApplied: () => void;
}

/**
 * Built-in chat with the person's own API key (Phase 6). The model proposes; AgenEditor checks every plan, and
 * only the Apply button turns a checked plan into a new revision. Keys stay in Windows Credential Manager.
 */
export const ChatPanel: React.FC<ChatPanelProps> = ({ projectId, revision, onApplied }) => {
  const [status, setStatus] = useState<ChatStatus | null>(null);
  const [messages, setMessages] = useState<ChatMessageRecord[]>([]);
  const [proposals, setProposals] = useState<Record<string, ChatProposalRecord>>({});
  const [provider, setProvider] = useState<ChatProviderId>('anthropic');
  const [model, setModel] = useState('');
  const [key, setKey] = useState('');
  const [text, setText] = useState('');
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    const [nextStatus, nextHistory] = await Promise.all([engine.chatStatus(projectId), engine.chatHistory(projectId)]);
    setStatus(nextStatus);
    setMessages(nextHistory.messages);
    setProposals(Object.fromEntries(nextHistory.proposals.map((item) => [item.id, item])));
  }, [projectId]);
  useEffect(() => { void refresh().catch((failure) => setError(engineErrorText(failure))); }, [refresh]);
  useEffect(() => { if (status) setModel((current) => current || status.default_models[provider] || ''); }, [status, provider]);

  const run = (label: string, task: () => Promise<void>) => {
    setBusy(label);
    setError(null);
    void task().catch((failure) => setError(engineErrorText(failure))).finally(() => setBusy(null));
  };

  const saveKey = () => run('key', async () => {
    await engine.chatSetKey(provider, key);
    setKey('');
    await refresh();
  });
  const removeKey = () => run('key', async () => { await engine.chatRemoveKey(provider); await refresh(); });
  const allow = (consent: boolean) => run('consent', async () => { await engine.chatSetConsent(projectId, consent); await refresh(); });
  const send = () => run('send', async () => {
    const turn = await engine.chatSend(projectId, provider, model.trim(), text.trim());
    setText('');
    setMessages((all) => [...all, ...turn.messages]);
    if (turn.proposal) setProposals((all) => ({ ...all, [turn.proposal!.id]: turn.proposal! }));
  });
  const apply = (item: ChatProposalRecord) => run(item.id, async () => {
    const applied = await engine.chatApply(projectId, item.id, revision);
    setProposals((all) => ({ ...all, [applied.id]: applied }));
    onApplied();
  });

  if (!status) {
    return <div className="flex flex-1 items-center justify-center text-sm text-fg-3">{error ?? <Loader2 className="h-4 w-4 animate-spin" />}</div>;
  }
  const hasKey = status.keys[provider];
  const ready = hasKey && status.consent && model.trim().length > 0;
  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <ol aria-label="Chat" className="flex min-h-0 flex-1 flex-col gap-2.5 overflow-y-auto px-4 py-4 text-sm">
        {messages.length === 0 && <li className="mt-auto text-fg-2">Ask for an edit. The model proposes a cut from your transcript; AgenEditor checks it and you decide whether to apply it.</li>}
        {messages.map((message) => {
          const item = message.proposal_id ? proposals[message.proposal_id] : undefined;
          return (
            <li key={message.id} className={`rounded-xl px-3 py-2 ${message.role === 'user' ? 'ml-6 bg-accent/12' : 'mr-6 border border-line bg-surface'}`}>
              <p className="whitespace-pre-wrap">{message.text}</p>
              {message.role === 'assistant' && message.model && <p className="mt-1 text-[11px] text-fg-3">{message.model}</p>}
              {item && (
                <div className="mt-2 space-y-1.5 rounded-lg border border-line bg-app p-2.5" data-testid={`proposal-${item.id}`}>
                  <div className="flex flex-wrap items-center justify-between gap-2">
                    <span className="text-xs font-medium">Proposed cut · {item.plan.segments.length} passages</span>
                    <Badge tone={STATUS_TEXT[item.status].tone}>{STATUS_TEXT[item.status].text}</Badge>
                  </div>
                  {item.result.issues.length > 0 && (
                    <ul className="space-y-0.5 text-xs">
                      {item.result.issues.slice(0, 5).map((issue, index) => (
                        <li key={index} className={issue.severity === 'error' ? 'text-danger' : 'text-warning'}>{issue.message}</li>
                      ))}
                    </ul>
                  )}
                  {item.status === 'proposed' && (
                    <Button size="sm" variant="primary" disabled={busy !== null || item.base_revision !== revision} onClick={() => apply(item)}
                      icon={busy === item.id ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Check className="h-3.5 w-3.5" />}>
                      Apply as revision {revision + 1}
                    </Button>
                  )}
                  {item.status === 'proposed' && item.base_revision !== revision && <p className="text-xs text-fg-3">The edit changed since this proposal; ask again.</p>}
                  {item.status === 'applied' && <p className="text-xs text-success">Saved as revision {item.applied_revision}. Render a preview to see it.</p>}
                </div>
              )}
            </li>
          );
        })}
        {busy === 'send' && <li className="flex items-center gap-2 text-fg-3"><Loader2 className="h-4 w-4 animate-spin" /> Waiting for {PROVIDERS.find((p) => p.id === provider)?.label}…</li>}
      </ol>

      <div className="shrink-0 space-y-3 border-t border-line p-4">
        <div className="grid grid-cols-2 gap-2">
          <label className="text-xs text-fg-2">Provider
            <select className={`${field} mt-1`} value={provider} disabled={busy !== null}
              onChange={(e) => { setProvider(e.target.value as ChatProviderId); setModel(status.default_models[e.target.value as ChatProviderId] || ''); }}>
              {PROVIDERS.map((item) => <option key={item.id} value={item.id}>{item.label}</option>)}
            </select>
          </label>
          <label className="text-xs text-fg-2">Model
            <input className={`${field} mt-1`} value={model} maxLength={100} placeholder="model name" disabled={busy !== null} onChange={(e) => setModel(e.target.value)} />
          </label>
        </div>
        {hasKey ? (
          <p className="flex items-center gap-2 text-xs text-fg-3"><KeyRound aria-hidden className="h-3.5 w-3.5" /> Key saved in Windows Credential Manager.
            <button type="button" className="text-accent hover:underline" disabled={busy !== null} onClick={removeKey}>Remove</button></p>
        ) : (
          <form className="flex items-center gap-2" onSubmit={(e) => { e.preventDefault(); saveKey(); }}>
            <input type="password" autoComplete="off" aria-label="API key" className={field} placeholder="Paste your API key" value={key} onChange={(e) => setKey(e.target.value)} />
            <Button type="submit" size="sm" disabled={busy !== null || !key.trim()}>Save key</Button>
          </form>
        )}
        {!status.consent && (
          <div className="space-y-1.5 rounded-lg border border-warning/40 bg-warning/10 p-2.5 text-xs">
            <p className="font-medium">Each message sends this text to the provider you choose:</p>
            <ul className="list-disc pl-4 text-fg-2">{status.data_sent.map((item) => <li key={item}>{item}</li>)}</ul>
            <Button size="sm" disabled={busy !== null} onClick={() => allow(true)}>Allow for this project</Button>
          </div>
        )}
        <div className="rounded-xl border border-line bg-app focus-within:border-accent">
          <label htmlFor="chat-input" className="sr-only">Message</label>
          <textarea id="chat-input" className="block w-full resize-none bg-transparent px-3.5 pt-3 text-sm text-fg outline-none placeholder:text-fg-3" rows={3} maxLength={4000}
            placeholder={ready ? 'Describe the edit you want…' : 'Add a key, choose a model and allow chat first'} value={text} disabled={!ready}
            onChange={(e) => setText(e.target.value)} onKeyDown={(e) => { if (e.key === 'Enter' && (e.ctrlKey || e.metaKey) && ready && text.trim()) { e.preventDefault(); send(); } }} />
          <div className="flex items-center gap-2 px-3 pb-3 pt-1">
            {status.consent && <button type="button" className="text-xs text-fg-3 hover:text-fg" disabled={busy !== null} onClick={() => allow(false)}>Stop allowing</button>}
            <Button variant="primary" size="lg" className="ml-auto" disabled={!ready || !text.trim() || busy !== null} onClick={send}>Send <ArrowRight className="h-4 w-4" /></Button>
          </div>
        </div>
        {error && <p role="alert" className="text-xs text-danger">{error}</p>}
        <p className="flex items-start gap-1.5 text-xs text-fg-3"><Lock aria-hidden className="mt-px h-3.5 w-3.5 shrink-0" /> Text only; video files never leave this PC. Nothing changes until you press Apply.</p>
      </div>
    </div>
  );
};
