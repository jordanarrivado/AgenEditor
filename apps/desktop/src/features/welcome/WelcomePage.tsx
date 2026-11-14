import React from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { ArrowRight, ClipboardCheck, FolderInput, MessageSquareText } from 'lucide-react';
import { useAppData } from '../../app/AppDataContext';
import { useSettings } from '../../app/SettingsContext';
import { Wordmark } from '../../components/common/Brand';
import { Badge, Button } from '../../components/ui/primitives';

const DEMO_ID = 'reel_001';

const STEPS = [
  { icon: <FolderInput className="w-5 h-5" />, title: 'Bring your footage', body: 'Interviews, B-roll and music you already own.' },
  { icon: <MessageSquareText className="w-5 h-5" />, title: 'Give creative direction', body: 'Describe the cut: hook, pacing, captions, length.' },
  { icon: <ClipboardCheck className="w-5 h-5" />, title: 'Review the AI-assisted edit', body: 'Inspect every cut, compare revisions, then export.' },
];

export const WelcomePage: React.FC = () => {
  const navigate = useNavigate();
  const { settings, update } = useSettings();
  const { projects, status, resetDemoData } = useAppData();
  const returning = settings.hasSeenWelcome;
  const demoAvailable = projects.some((p) => p.id === DEMO_ID);

  const go = (to: string) => {
    update({ hasSeenWelcome: true });
    navigate(to);
  };

  return (
    <div className="min-h-screen h-screen overflow-y-auto bg-app text-fg">
      <div
        className="relative min-h-full flex flex-col"
        style={{ backgroundImage: 'radial-gradient(ellipse 80% 50% at 50% -10%, color-mix(in srgb, var(--accent) 22%, transparent), transparent)' }}
      >
        <header className="px-6 py-5 flex items-center justify-between">
          <Wordmark />
          <Badge tone="warning">UI Prototype · Demo Mode</Badge>
        </header>

        <main className="flex-1 flex flex-col items-center justify-center px-6 py-10 text-center">
          <p className="text-sm text-accent font-medium mb-3">{returning ? 'Welcome back' : 'Your footage. Your vision. AI edited.'}</p>
          <h1 className="text-4xl md:text-5xl font-semibold tracking-tight max-w-3xl leading-[1.1]">Turn raw footage into a finished edit.</h1>
          <p className="text-base text-fg-2 mt-4 max-w-xl leading-relaxed">
            Import your clips, give creative direction, and review an AI-assisted edit. No generated footage.
          </p>

          <div className="mt-8 flex flex-wrap gap-3 justify-center">
            <Button
              variant="primary"
              size="lg"
              disabled={status !== 'ready' || !demoAvailable}
              onClick={() => go(`/projects/${DEMO_ID}/editor`)}
              icon={<ArrowRight className="w-4 h-4" />}
            >
              Explore Demo Project
            </Button>
            <Button size="lg" onClick={() => go('/projects/new')}>
              Create Project
            </Button>
            {returning && (
              <Button size="lg" variant="ghost" onClick={() => go('/home')}>
                Go to Home
              </Button>
            )}
          </div>

          {status === 'ready' && !demoAvailable && (
            <div role="alert" className="mt-5 p-3 rounded-lg border border-warning/30 bg-warning/10 text-[13px] text-fg max-w-md">
              The demo project fixture is unavailable (it may have been archived).{' '}
              <button className="text-accent font-semibold hover:underline" onClick={() => void resetDemoData()}>
                Restore demo data
              </button>
            </div>
          )}

          <ol className="mt-14 grid grid-cols-1 md:grid-cols-3 gap-4 max-w-4xl w-full text-left">
            {STEPS.map((s, i) => (
              <li key={s.title} className="rounded-xl border border-line bg-surface/80 p-5">
                <div className="flex items-center gap-3">
                  <span className="w-9 h-9 rounded-lg bg-accent/12 text-accent flex items-center justify-center">{s.icon}</span>
                  <span className="font-mono text-xs text-fg-3">Step {i + 1}</span>
                </div>
                <h2 className="text-[15px] font-semibold mt-3">{s.title}</h2>
                <p className="text-[13px] text-fg-2 mt-1 leading-relaxed">{s.body}</p>
              </li>
            ))}
          </ol>
        </main>

        <footer className="px-6 py-4 text-center text-xs text-fg-3">
          Local-first: nothing leaves your machine. AgenEditor edits only user-supplied media.{' '}
          <Link to="/help" onClick={() => update({ hasSeenWelcome: true })} className="text-fg-2 hover:text-fg underline">
            Privacy &amp; licensing overview
          </Link>
        </footer>
      </div>
    </div>
  );
};
