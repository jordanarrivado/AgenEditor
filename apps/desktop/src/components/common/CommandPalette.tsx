import React, { useMemo, useState } from 'react';
import * as Dialog from '@radix-ui/react-dialog';
import { useNavigate } from 'react-router-dom';
import { ArrowRight, FolderOpen, Search } from 'lucide-react';
import { useAppData } from '../../app/AppDataContext';
import { cx } from '../ui/primitives';
import { useReturnFocus } from '../ui/Modal';

/** Dispatch `new Event(OPEN_PALETTE_EVENT)` on window to open the palette from anywhere. */
export const OPEN_PALETTE_EVENT = 'ageneditor:open-palette';

interface Item {
  id: string;
  label: string;
  hint: string;
  to: string;
  kind: 'project' | 'nav';
}

const NAV: Item[] = [
  { id: 'n-home', label: 'Home', hint: 'Dashboard', to: '/home', kind: 'nav' },
  { id: 'n-projects', label: 'Projects', hint: 'Project library', to: '/projects', kind: 'nav' },
  { id: 'n-new', label: 'New Project', hint: 'Start the wizard', to: '/projects/new', kind: 'nav' },
  { id: 'n-activity', label: 'Activity', hint: 'Simulation jobs', to: '/activity', kind: 'nav' },
  { id: 'n-templates', label: 'Templates', hint: 'Editing styles', to: '/templates', kind: 'nav' },
  { id: 'n-settings', label: 'Settings', hint: 'Preferences', to: '/settings', kind: 'nav' },
  { id: 'n-help', label: 'Help', hint: 'Shortcuts & limitations', to: '/help', kind: 'nav' },
];

export const CommandPalette: React.FC<{ open: boolean; onOpenChange: (o: boolean) => void }> = ({ open, onOpenChange }) => {
  const { projects } = useAppData();
  const navigate = useNavigate();
  const [q, setQ] = useState('');
  const [active, setActive] = useState(0);
  const returnFocus = useReturnFocus(open);

  const items = useMemo(() => {
    const all: Item[] = [
      ...projects.map((p) => ({ id: p.id, label: p.name, hint: 'Open in editor', to: `/projects/${p.id}/editor`, kind: 'project' as const })),
      ...NAV,
    ];
    const s = q.trim().toLowerCase();
    return s ? all.filter((i) => i.label.toLowerCase().includes(s)) : all;
  }, [projects, q]);

  const go = (i: Item) => {
    onOpenChange(false);
    setQ('');
    navigate(i.to);
  };

  return (
    <Dialog.Root
      open={open}
      onOpenChange={(o) => {
        onOpenChange(o);
        if (!o) setQ('');
        setActive(0);
      }}
    >
      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-0 z-50 bg-black/50" />
        <Dialog.Content onCloseAutoFocus={returnFocus} className="fixed z-50 left-1/2 top-[14vh] -translate-x-1/2 w-[min(560px,calc(100vw-32px))] rounded-xl border border-line bg-surface shadow-2xl overflow-hidden">
          <Dialog.Title className="sr-only">Command palette</Dialog.Title>
          <Dialog.Description className="sr-only">Search projects and screens</Dialog.Description>
          <div className="flex items-center gap-2 px-3 border-b border-line">
            <Search aria-hidden className="w-4 h-4 text-fg-3" />
            <input
              autoFocus
              role="combobox"
              aria-expanded
              aria-controls="cmdk-list"
              aria-activedescendant={items[active] ? `cmdk-${items[active].id}` : undefined}
              aria-label="Search projects and screens"
              placeholder="Search projects and screens…"
              value={q}
              onChange={(e) => {
                setQ(e.target.value);
                setActive(0);
              }}
              onKeyDown={(e) => {
                if (e.key === 'ArrowDown') {
                  e.preventDefault();
                  setActive((a) => Math.min(items.length - 1, a + 1));
                } else if (e.key === 'ArrowUp') {
                  e.preventDefault();
                  setActive((a) => Math.max(0, a - 1));
                } else if (e.key === 'Enter' && items[active]) {
                  go(items[active]);
                }
              }}
              className="flex-1 h-11 bg-transparent text-[14px] text-fg placeholder:text-fg-3 focus:outline-none"
            />
          </div>
          <ul id="cmdk-list" role="listbox" className="max-h-80 overflow-y-auto p-1.5">
            {items.length === 0 && <li className="p-4 text-center text-[13px] text-fg-3">No results</li>}
            {items.map((i, idx) => (
              <li
                key={i.id}
                id={`cmdk-${i.id}`}
                role="option"
                aria-selected={idx === active}
                onMouseEnter={() => setActive(idx)}
                onClick={() => go(i)}
                className={cx('flex items-center gap-2.5 px-2.5 py-2 rounded-md cursor-pointer', idx === active && 'bg-elevated')}
              >
                {i.kind === 'project' ? <FolderOpen aria-hidden className="w-4 h-4 text-fg-3" /> : <ArrowRight aria-hidden className="w-4 h-4 text-fg-3" />}
                <span className="text-[13px] text-fg flex-1 truncate">{i.label}</span>
                <span className="text-xs text-fg-3">{i.hint}</span>
              </li>
            ))}
          </ul>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
};
