import React, { useCallback, useEffect, useRef, useState } from 'react';
import { Check, ChevronDown, Download, Info, Loader2, LogIn, RefreshCw } from 'lucide-react';
import {
  liveEngineRepository as engine,
  type ClaudeSetupStatus,
  type SpeechModelStatus,
} from '../../services/liveEngineRepository';
import { Button } from '../../components/ui/primitives';
import { engineErrorText } from '../../utils/engineError';

const SIGN_IN_POLL_MS = 3000;
const SIGN_IN_WAIT_MS = 5 * 60 * 1000;
const PLAN_LABELS: Record<string, string> = { pro: 'Pro', max: 'Max', team: 'Team', enterprise: 'Enterprise' };

type Item = {
  id: string;
  title: string;
  text: string;
  required: boolean;
  ready: boolean | null; // null while checking
  status: string;
  action?: React.ReactNode;
};

/**
 * "Set up this PC": everything AgenEditor needs outside its own installer. The engine, FFmpeg
 * and fonts are bundled; Claude Code must be installed and signed in; the speech model is
 * optional. Every check is real, and installs only start when the person clicks.
 */
export const SetupPanel: React.FC<{ engineConnected: boolean; onChanged?: () => void }> = ({ engineConnected, onChanged }) => {
  const [claude, setClaude] = useState<ClaudeSetupStatus | null>(null);
  const [speech, setSpeech] = useState<SpeechModelStatus | null>(null);
  const [speechFailed, setSpeechFailed] = useState(false);
  const [busy, setBusy] = useState<'install' | 'model' | null>(null);
  const [waitingSignIn, setWaitingSignIn] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [expanded, setExpanded] = useState<boolean | null>(null);
  const waitUntil = useRef(0);

  const check = useCallback(async () => {
    let failed = false;
    const [claudeStatus, speechStatus] = await Promise.all([
      engine.claudeStatus(),
      engine.speechModelStatus().catch(() => { failed = true; return null; }),
    ]);
    setClaude(claudeStatus);
    setSpeech(speechStatus);
    setSpeechFailed(failed);
    return claudeStatus;
  }, []);

  useEffect(() => { void check().catch((e) => setError(engineErrorText(e))); }, [check]);
  // The engine answers the speech-model check only once it is running.
  useEffect(() => {
    if (engineConnected) void engine.speechModelStatus().then((status) => { setSpeech(status); setSpeechFailed(false); }).catch(() => setSpeechFailed(true));
  }, [engineConnected]);

  // After "Sign in", poll until Claude Code reports a signed-in account (or we give up).
  useEffect(() => {
    if (!waitingSignIn) return;
    const timer = window.setInterval(() => {
      void engine.claudeStatus().then((status) => {
        setClaude(status);
        if (status.signed_in) { setWaitingSignIn(false); onChanged?.(); }
        else if (Date.now() > waitUntil.current) setWaitingSignIn(false);
      }).catch(() => undefined);
    }, SIGN_IN_POLL_MS);
    return () => window.clearInterval(timer);
  }, [waitingSignIn, onChanged]);

  const installClaude = () => {
    setBusy('install');
    setError(null);
    void engine.installClaudeCode()
      .then((status) => { setClaude(status); onChanged?.(); })
      .catch((e) => setError(engineErrorText(e)))
      .finally(() => setBusy(null));
  };

  const signIn = () => {
    setError(null);
    void engine.openClaudeSignIn()
      .then(() => { waitUntil.current = Date.now() + SIGN_IN_WAIT_MS; setWaitingSignIn(true); })
      .catch((e) => setError(engineErrorText(e)));
  };

  const installModel = () => {
    setBusy('model');
    setError(null);
    void engine.cacheModel('tiny')
      .then(() => engine.speechModelStatus().then((status) => { setSpeech(status); setSpeechFailed(false); }))
      .catch((e) => setError(engineErrorText(e)))
      .finally(() => setBusy(null));
  };

  const recheck = () => { setError(null); void check().then(() => onChanged?.()).catch((e) => setError(engineErrorText(e))); };

  const plan = claude?.plan ? PLAN_LABELS[claude.plan] ?? claude.plan : null;
  const items: Item[] = [
    {
      id: 'engine', title: 'AgenEditor engine', required: true,
      text: 'Video engine, FFmpeg and caption fonts come with AgenEditor. Nothing to install.',
      ready: engineConnected ? true : null, status: engineConnected ? 'Built in · running' : 'Starting…',
    },
    {
      id: 'claude', title: 'Claude Code', required: true,
      text: "Claude edits your video through Claude Code. Install runs Anthropic's official installer for this Windows user (no admin needed).",
      ready: claude ? claude.installed : null,
      status: !claude ? 'Checking…' : claude.installed ? `Installed${claude.version ? ` · ${claude.version.replace(/\s*\(Claude Code\)/, '')}` : ''}` : busy === 'install' ? 'Installing… this can take a few minutes' : claude.problem ?? 'Not installed',
      action: claude && !claude.installed && !claude.problem && (
        <Button variant="primary" size="lg" disabled={busy !== null} onClick={installClaude}
          icon={busy === 'install' ? <Loader2 className="h-4 w-4 animate-spin" /> : <Download className="h-4 w-4" />}>
          {busy === 'install' ? 'Installing…' : 'Install Claude Code'}
        </Button>
      ),
    },
    {
      id: 'account', title: 'Claude account', required: true,
      text: 'Needs a Claude Pro, Max, Team or Enterprise plan, or an Anthropic Console account. Sign-in opens in your browser.',
      ready: claude ? claude.installed && claude.signed_in : null,
      status: !claude ? 'Checking…' : claude.signed_in ? `Signed in${plan ? ` · ${plan} plan` : ''}` : !claude.installed ? 'Install Claude Code first' : waitingSignIn ? 'Finish signing in in your browser…' : 'Not signed in',
      action: claude && claude.installed && !claude.signed_in && (
        waitingSignIn
          ? <Button size="lg" icon={<RefreshCw className="h-4 w-4" />} onClick={recheck}>Check again</Button>
          : <Button variant="primary" size="lg" icon={<LogIn className="h-4 w-4" />} onClick={signIn}>Sign in</Button>
      ),
    },
    {
      id: 'speech', title: 'Speech model', required: false,
      text: 'Optional, for footage with talking: lets Claude cut on exact words. About 75 MB, downloaded once.',
      ready: speech ? speech.tiny || speech.base || speech.small : speechFailed ? false : null,
      status: !speech ? (speechFailed ? 'Could not check; Install will download it if missing' : engineConnected ? 'Checking…' : 'Waiting for the engine') : speech.tiny || speech.base || speech.small ? 'Installed' : busy === 'model' ? 'Downloading…' : 'Not installed',
      action: ((speech && !(speech.tiny || speech.base || speech.small)) || speechFailed) && (
        <Button size="lg" disabled={busy !== null} onClick={installModel}
          icon={busy === 'model' ? <Loader2 className="h-4 w-4 animate-spin" /> : <Download className="h-4 w-4" />}>
          {busy === 'model' ? 'Downloading…' : 'Install'}
        </Button>
      ),
    },
  ];
  const required = items.filter((item) => item.required);
  const readyCount = required.filter((item) => item.ready).length;
  const allReady = readyCount === required.length;
  const open = expanded ?? !allReady;

  return (
    <section aria-labelledby="setup-title" className={`rounded-2xl border ${allReady ? 'border-line bg-surface/60' : 'border-accent/50 bg-surface'} p-5`}>
      <button type="button" className="flex w-full items-center gap-3 text-left" aria-expanded={open} onClick={() => setExpanded(!open)}>
        <span aria-hidden className={`flex h-9 w-9 shrink-0 items-center justify-center rounded-full ${allReady ? 'bg-success/15 text-success' : 'bg-accent/15 text-accent'}`}>
          {allReady ? <Check className="h-5 w-5" strokeWidth={3} /> : <Download className="h-4 w-4" />}
        </span>
        <span className="min-w-0 flex-1">
          <span id="setup-title" className="block font-semibold">{allReady ? 'This PC is ready' : 'Set up this PC'}</span>
          <span className="block text-xs text-fg-3">{readyCount} of {required.length} required items ready{allReady ? '' : ' · click Install or Sign in below'}</span>
        </span>
        <ChevronDown aria-hidden className={`h-4 w-4 text-fg-3 transition-transform ${open ? 'rotate-180' : ''}`} />
      </button>

      {open && (
        <div className="mt-4 space-y-2">
          <ul className="divide-y divide-line overflow-hidden rounded-xl border border-line" aria-label="Setup checklist">
            {items.map((item) => (
              <li key={item.id} className="flex flex-wrap items-center gap-3 bg-app/40 px-4 py-3">
                <span aria-hidden className={`flex h-7 w-7 shrink-0 items-center justify-center rounded-full ${item.ready ? 'bg-success text-on-accent' : item.ready === null ? 'border border-line text-fg-3' : 'border border-warning/60 text-warning'}`}>
                  {item.ready ? <Check className="h-4 w-4" strokeWidth={3} /> : item.ready === null ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : '!'}
                </span>
                <div className="min-w-0 flex-1">
                  <p className="font-semibold">{item.title}{!item.required && <span className="ml-1.5 text-xs font-normal text-fg-3">optional</span>}</p>
                  <p className="text-xs text-fg-3">{item.text}</p>
                  <p className={`mt-0.5 text-xs font-medium ${item.ready ? 'text-success' : item.ready === null ? 'text-fg-3' : 'text-warning'}`} data-testid={`setup-${item.id}`}>{item.status}</p>
                </div>
                {item.action}
              </li>
            ))}
          </ul>
          {error && <p role="alert" className="text-sm text-danger">{error}</p>}
          <div className="flex flex-wrap items-center gap-3 pt-1">
            <Button variant="ghost" icon={<RefreshCw className="h-4 w-4" />} onClick={recheck}>Check again</Button>
            <span className="flex items-center gap-1 text-xs text-fg-3"><Info className="h-3.5 w-3.5 shrink-0" /> No Python, Node.js or other tools are needed; the installer includes everything else.</span>
          </div>
        </div>
      )}
    </section>
  );
};
