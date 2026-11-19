import React, { useEffect, useState } from 'react';
import { NavLink, Outlet, useLocation, useNavigate } from 'react-router-dom';
import { Activity, Factory, FolderOpen, HelpCircle, Home, Keyboard, LayoutTemplate, Plus, RotateCcw, Search, Settings } from 'lucide-react';
import { ToastContainer, showToast } from './Toast';
import { KeyboardShortcutsModal } from './KeyboardShortcutsModal';
import { CommandPalette, OPEN_PALETTE_EVENT } from './CommandPalette';
import { ConfirmDialog } from '../ui/Modal';
import { Tooltip, TooltipProvider, cx } from '../ui/primitives';
import { useAppData } from '../../app/AppDataContext';
import { isTypingTarget } from '../../hooks/useViewport';
import { BrandMark } from './Brand';

const NAV = [
  { to: '/home', label: 'Home', icon: Home },
  { to: '/projects', label: 'Projects', icon: FolderOpen, end: false },
  { to: '/production', label: 'Production', icon: Factory },
  { to: '/activity', label: 'Activity', icon: Activity },
  { to: '/templates', label: 'Templates', icon: LayoutTemplate },
  { to: '/settings', label: 'Settings', icon: Settings },
  { to: '/help', label: 'Help', icon: HelpCircle },
];

export const AppShell: React.FC = () => {
  const [shortcutsOpen, setShortcutsOpen] = useState(false);
  const [paletteOpen, setPaletteOpen] = useState(false);
  const [resetOpen, setResetOpen] = useState(false);
  const { resetDemoData, jobs } = useAppData();
  const navigate = useNavigate();
  const location = useLocation();
  const running = jobs.filter((j) => j.status === 'running' || j.status === 'queued').length;

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'k') {
        e.preventDefault();
        setPaletteOpen(true);
        return;
      }
      if (e.key === '?' && !isTypingTarget(e.target) && !document.querySelector('[role="dialog"]')) {
        e.preventDefault();
        setShortcutsOpen(true);
      }
    };
    const onOpen = () => setPaletteOpen(true);
    window.addEventListener('keydown', onKey);
    window.addEventListener(OPEN_PALETTE_EVENT, onOpen);
    return () => {
      window.removeEventListener('keydown', onKey);
      window.removeEventListener(OPEN_PALETTE_EVENT, onOpen);
    };
  }, []);

  // Move focus to main content on route change for screen-reader users.
  useEffect(() => {
    document.getElementById('main')?.focus({ preventScroll: true });
  }, [location.pathname]);

  const railBtn = 'w-10 h-10 rounded-lg flex items-center justify-center transition-colors';

  return (
    <TooltipProvider>
      <div className="flex h-screen w-screen bg-app text-fg overflow-hidden">
        <a href="#main" className="sr-only focus:not-sr-only focus:absolute focus:z-[90] focus:m-2 focus:px-3 focus:py-2 focus:bg-elevated focus:rounded-md">
          Skip to content
        </a>
        <aside aria-label="Primary" className="w-16 shrink-0 bg-sidebar border-r border-line flex flex-col items-center justify-between py-3">
          <div className="flex flex-col items-center gap-2">
            <Tooltip content="AgenEditor — Home" side="right">
              <NavLink to="/home" aria-label="AgenEditor home" className="mb-1">
                <BrandMark />
              </NavLink>
            </Tooltip>
            <Tooltip content="New Project" side="right">
              <button
                type="button"
                aria-label="New Project"
                onClick={() => navigate('/projects/new')}
                className={cx(railBtn, 'bg-accent text-on-accent hover:bg-accent-hover')}
              >
                <Plus className="w-5 h-5" />
              </button>
            </Tooltip>
            <div className="w-6 h-px bg-line my-1" />
            <nav aria-label="Main navigation" className="flex flex-col items-center gap-1">
              {NAV.map(({ to, label, icon: Icon }) => {
                // Computed here (not via NavLink's className fn) because Radix Slot string-joins className.
                const active = location.pathname === to || location.pathname.startsWith(`${to}/`);
                return (
                <Tooltip key={to} content={label} side="right">
                  <NavLink
                    to={to}
                    aria-label={label}
                    aria-current={active ? 'page' : undefined}
                    className={cx(railBtn, 'relative', active ? 'bg-elevated text-accent' : 'text-fg-3 hover:text-fg hover:bg-surface')}
                  >
                    <Icon className="w-[18px] h-[18px]" />
                    {to === '/activity' && running > 0 && (
                      <span className="absolute top-1.5 right-1.5 w-2 h-2 rounded-full bg-accent" aria-label={`${running} simulation jobs running`} />
                    )}
                  </NavLink>
                </Tooltip>
                );
              })}
            </nav>
          </div>
          <div className="flex flex-col items-center gap-1">
            <Tooltip content="Search (Ctrl+K)" side="right">
              <button type="button" aria-label="Search projects (Ctrl+K)" onClick={() => setPaletteOpen(true)} className={cx(railBtn, 'text-fg-3 hover:text-fg hover:bg-surface')}>
                <Search className="w-[18px] h-[18px]" />
              </button>
            </Tooltip>
            <Tooltip content="Keyboard shortcuts (?)" side="right">
              <button type="button" aria-label="Keyboard shortcuts" onClick={() => setShortcutsOpen(true)} className={cx(railBtn, 'text-fg-3 hover:text-fg hover:bg-surface')}>
                <Keyboard className="w-[18px] h-[18px]" />
              </button>
            </Tooltip>
            <Tooltip content="Reset Demo Data" side="right">
              <button type="button" aria-label="Reset Demo Data" onClick={() => setResetOpen(true)} className={cx(railBtn, 'text-fg-3 hover:text-danger hover:bg-surface')}>
                <RotateCcw className="w-4 h-4" />
              </button>
            </Tooltip>
          </div>
        </aside>

        <main id="main" tabIndex={-1} className="flex-1 min-w-0 flex flex-col overflow-hidden focus:outline-none">
          <Outlet />
        </main>

        <KeyboardShortcutsModal open={shortcutsOpen} onOpenChange={setShortcutsOpen} />
        <CommandPalette open={paletteOpen} onOpenChange={setPaletteOpen} />
        <ConfirmDialog
          open={resetOpen}
          onOpenChange={setResetOpen}
          destructive
          title="Reset demo data?"
          message="All demo projects, timelines, notes and simulation jobs return to their original fixtures. Your settings are kept."
          confirmLabel="Reset demo data"
          onConfirm={async () => {
            await resetDemoData();
            showToast({ type: 'success', title: 'Demo data restored', message: 'Fixtures reloaded.' });
            navigate('/home');
          }}
        />
        <ToastContainer />
      </div>
    </TooltipProvider>
  );
};
