import React from 'react';
import { Link } from 'react-router-dom';
import { PageLayout } from '../../components/projects/ProjectBits';
import { Card } from '../../components/ui/primitives';
import { ShortcutsTable } from '../../components/common/KeyboardShortcutsModal';

const Section: React.FC<{ id: string; title: string; children: React.ReactNode }> = ({ id, title, children }) => (
  <section aria-labelledby={id} className="mb-6">
    <h2 id={id} className="text-lg font-semibold text-fg mb-2">
      {title}
    </h2>
    <Card className="p-5 text-[13px] text-fg-2 leading-relaxed flex flex-col gap-2">{children}</Card>
  </section>
);

export const HelpPage: React.FC = () => (
  <PageLayout title="Help & About" width="narrow" description="How AgenEditor works, what this prototype can and cannot do, and keyboard shortcuts.">
    <Section id="h-philosophy" title="What AgenEditor is">
      <p>
        AgenEditor is a local-first desktop editor that turns <strong className="text-fg">your own</strong> raw footage, B-roll and music into a finished cut, guided by a
        written brief. An AI agent (Codex or Claude Code, via MCP) proposes edits; you review every change as a timeline revision.
      </p>
      <p>
        It is <strong className="text-fg">editing only</strong>. AgenEditor does not generate video, images, voices or music, and has no stock-media or text-to-video
        features.
      </p>
    </Section>

    <Section id="h-demo" title="Demo Mode limitations">
      <ul className="list-disc pl-5 flex flex-col gap-1">
        <li>All projects, assets, transcripts, timelines, QA results and jobs are sample fixtures.</li>
        <li>The preview moves a simulated playhead over placeholder artwork. No video or audio is decoded or played.</li>
        <li>Timeline edits change demo data only; they are not source-accurate media operations.</li>
        <li>Export shows proposed settings and planned artifacts. No file is rendered, written or offered for download.</li>
        <li>Jobs in Activity are scripted simulations with deterministic progress.</li>
        <li>The MCP connection to Codex / Claude Code is not available; the handoff brief is text only.</li>
        <li>
          Demo edits are kept in this browser's local storage. Use <em>Reset Demo Data</em> (left rail or Settings → About) to restore fixtures.
        </li>
      </ul>
    </Section>

    <Section id="h-import" title="Importing media & permissions">
      <p>
        In the desktop release, importing registers files by reference: AgenEditor reads metadata, creates proxies and waveforms inside the project folder, and never
        uploads footage. Agents can only address assets by ID within the project — they cannot read arbitrary paths.
      </p>
      <p>
        In this prototype, the file picker in the New Project wizard only lists the names you choose; file contents are never read.
      </p>
    </Section>

    <Section id="h-privacy" title="Privacy & licensing">
      <p>No telemetry, accounts or cloud upload. Bundled artwork is CSS-generated; no stock photos or third-party footage are included.</p>
      <p>
        See <Link to="/settings" className="text-accent hover:underline">Settings → Privacy</Link> for external-model data disclosure.
      </p>
    </Section>

    <section aria-labelledby="h-keys" className="mb-6">
      <h2 id="h-keys" className="text-lg font-semibold text-fg mb-2">
        Keyboard shortcuts
      </h2>
      <Card className="p-5">
        <ShortcutsTable />
      </Card>
    </section>
  </PageLayout>
);
