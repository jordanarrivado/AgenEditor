import React from 'react';
import * as Popover from '@radix-ui/react-popover';
import { FlaskConical } from 'lucide-react';
import { Link } from 'react-router-dom';

/** "Demo Mode" marker with an explanatory popover (spec §4.13). */
export const DemoModeBadge: React.FC<{ compact?: boolean }> = ({ compact }) => (
  <Popover.Root>
    <Popover.Trigger asChild>
      <button
        type="button"
        className="inline-flex items-center gap-1 h-6 px-2 rounded-md border border-warning/40 bg-warning/10 text-warning text-[11px] font-semibold hover:bg-warning/20"
        aria-label="Demo Mode — what does this mean?"
      >
        <FlaskConical aria-hidden className="w-3.5 h-3.5" />
        {compact ? 'Demo' : 'Demo Mode'}
      </button>
    </Popover.Trigger>
    <Popover.Portal>
      <Popover.Content
        sideOffset={6}
        collisionPadding={12}
        className="z-[70] w-80 rounded-lg border border-line bg-elevated p-3.5 text-[13px] text-fg shadow-xl"
      >
        <p className="font-semibold mb-1">UI prototype · Demo Mode</p>
        <ul className="list-disc pl-4 text-fg-2 space-y-1 leading-relaxed">
          <li>Projects, transcripts, timelines, jobs and QA results are sample data.</li>
          <li>The preview is a simulated playhead — no video or audio is decoded or rendered.</li>
          <li>Exports only preview settings; no files are created.</li>
          <li>The Codex / Claude Code MCP connection is not available yet.</li>
        </ul>
        <Link to="/help" className="inline-block mt-2 text-accent font-medium hover:underline">
          Read about demo limitations
        </Link>
        <Popover.Arrow className="fill-[var(--bg-elevated)]" />
      </Popover.Content>
    </Popover.Portal>
  </Popover.Root>
);
