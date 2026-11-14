import React from 'react';
import { Keyboard } from 'lucide-react';
import { Modal } from '../ui/Modal';
import { Kbd } from '../ui/primitives';

export const SHORTCUTS: { keys: string[]; desc: string; scope: 'Global' | 'Editor' }[] = [
  { keys: ['Ctrl', 'K'], desc: 'Open command palette / project search', scope: 'Global' },
  { keys: ['?'], desc: 'Open this shortcuts list', scope: 'Global' },
  { keys: ['Esc'], desc: 'Close dialog or menu; clear clip selection', scope: 'Global' },
  { keys: ['Space'], desc: 'Play / pause the simulated playhead', scope: 'Editor' },
  { keys: ['←', '→'], desc: 'Move playhead one frame', scope: 'Editor' },
  { keys: ['Shift', '← / →'], desc: 'Move playhead one second', scope: 'Editor' },
  { keys: ['Home', 'End'], desc: 'Jump to start / end of the timeline', scope: 'Editor' },
  { keys: ['+', '−'], desc: 'Zoom timeline in / out', scope: 'Editor' },
  { keys: ['Ctrl', 'Z'], desc: 'Undo demo edit', scope: 'Editor' },
  { keys: ['Ctrl', 'Shift', 'Z'], desc: 'Redo demo edit', scope: 'Editor' },
  { keys: ['Delete'], desc: 'Remove selected clip (undo available)', scope: 'Editor' },
  { keys: ['S'], desc: 'Toggle snapping', scope: 'Editor' },
  { keys: ['Z'], desc: 'Toggle safe-zone overlay', scope: 'Editor' },
  { keys: ['G'], desc: 'Toggle framing guides', scope: 'Editor' },
];

export const ShortcutsTable: React.FC = () => (
  <div className="flex flex-col gap-4">
    {(['Global', 'Editor'] as const).map((scope) => (
      <section key={scope}>
        <h3 className="text-[11px] font-semibold uppercase tracking-wide text-fg-3 mb-1.5">{scope}</h3>
        <dl className="divide-y divide-line-subtle">
          {SHORTCUTS.filter((s) => s.scope === scope).map((s) => (
            <div key={s.desc} className="flex items-center justify-between gap-4 py-1.5">
              <dt className="text-[13px] text-fg-2">{s.desc}</dt>
              <dd className="flex gap-1 shrink-0">
                {s.keys.map((k) => (
                  <Kbd key={k}>{k}</Kbd>
                ))}
              </dd>
            </div>
          ))}
        </dl>
      </section>
    ))}
  </div>
);

export const KeyboardShortcutsModal: React.FC<{ open: boolean; onOpenChange: (o: boolean) => void }> = ({ open, onOpenChange }) => (
  <Modal open={open} onOpenChange={onOpenChange} title="Keyboard shortcuts" icon={<Keyboard className="w-5 h-5" />}>
    <ShortcutsTable />
  </Modal>
);
