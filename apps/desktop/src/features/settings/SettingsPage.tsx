import React, { useState } from 'react';
import { useAppData } from '../../app/AppDataContext';
import { useSettings } from '../../app/SettingsContext';
import { CAPTION_PRESETS } from '../../mocks/fixtures';
import { MCP_CONFIG_SNIPPET } from '../../components/editor/AgentHandoffPanel';
import { PageLayout } from '../../components/projects/ProjectBits';
import { Badge, Button, Card, Field, Input, Segmented, Select, Switch, Unavailable, cx } from '../../components/ui/primitives';
import { ConfirmDialog } from '../../components/ui/Modal';
import { ShortcutsTable } from '../../components/common/KeyboardShortcutsModal';
import { showToast } from '../../components/common/Toast';
import type { AspectRatio } from '../../types';
import { AGENT_ROLES, EFFORTS, ROLE_META, ROLE_MODELS, type AgentEffort } from '../../utils/agentRoles';

const SECTIONS = [
  { id: 'general', label: 'General' },
  { id: 'editing', label: 'Editing defaults' },
  { id: 'engine', label: 'Engine' },
  { id: 'agent', label: 'Agent / MCP' },
  { id: 'privacy', label: 'Privacy' },
  { id: 'about', label: 'About' },
] as const;
type SectionId = (typeof SECTIONS)[number]['id'];

const Row: React.FC<{ title: string; description?: React.ReactNode; children: React.ReactNode }> = ({ title, description, children }) => (
  <div className="flex flex-wrap items-center justify-between gap-3 py-3 border-b border-line-subtle last:border-0">
    <div className="min-w-0 max-w-md">
      <p className="text-[13px] font-medium text-fg">{title}</p>
      {description && <p className="text-xs text-fg-3 mt-0.5">{description}</p>}
    </div>
    <div className="shrink-0">{children}</div>
  </div>
);

export const SettingsPage: React.FC = () => {
  const { settings, update, reset } = useSettings();
  const { resetDemoData, persistsLocally } = useAppData();
  const [section, setSection] = useState<SectionId>('general');
  const [confirmReset, setConfirmReset] = useState(false);
  const tz = Intl.DateTimeFormat().resolvedOptions().timeZone;

  return (
    <PageLayout title="Settings" description="Preferences apply immediately. Items marked “Available after engine integration” are shown for design review only.">
      <div className="flex flex-col md:flex-row gap-6">
        <nav aria-label="Settings sections" className="md:w-48 shrink-0">
          <ul role="tablist" aria-orientation="vertical" className="flex md:flex-col gap-1 overflow-x-auto">
            {SECTIONS.map((s) => (
              <li key={s.id}>
                <button
                  role="tab"
                  aria-selected={section === s.id}
                  aria-controls={`set-${s.id}`}
                  onClick={() => setSection(s.id)}
                  className={cx(
                    'w-full text-left h-8 px-3 rounded-md text-[13px] whitespace-nowrap',
                    section === s.id ? 'bg-elevated text-fg font-medium' : 'text-fg-2 hover:text-fg hover:bg-surface'
                  )}
                >
                  {s.label}
                </button>
              </li>
            ))}
          </ul>
        </nav>

        <Card className="flex-1 p-5 min-w-0" role="tabpanel" id={`set-${section}`}>
          {section === 'general' && (
            <>
              <Row title="Theme" description="Dark is the default production theme.">
                <Segmented
                  label="Theme"
                  value={settings.theme}
                  onChange={(theme) => update({ theme })}
                  options={[
                    { value: 'dark', label: 'Dark' },
                    { value: 'light', label: 'Light' },
                  ]}
                />
              </Row>
              <Row title="Reduce motion" description="Disables animations and transitions. The OS setting is also honoured.">
                <Switch label="Reduce motion" checked={settings.reducedMotion} onChange={(v) => update({ reducedMotion: v })} />
              </Row>
              <Row title="Language">
                <Select aria-label="Language" value="en" onChange={() => undefined} className="w-40">
                  <option value="en">English</option>
                </Select>
              </Row>
              <Row title="Time zone" description="Fixture dates are shown in UTC so screenshots stay reproducible.">
                <span className="font-mono text-xs text-fg-2">{tz}</span>
              </Row>
              <Row title="Default project folder" description="Available after engine integration.">
                <div className="flex gap-2">
                  <Input aria-label="Default project folder" readOnly value="Documents\AgenEditor" className="w-56 text-fg-3" />
                  <Unavailable reason="Folder selection requires desktop integration">
                    <Button>Change…</Button>
                  </Unavailable>
                </div>
              </Row>
              <Row title="Show welcome screen" description="Show the first-run welcome next time the app opens at “/”.">
                <Switch label="Show welcome screen" checked={!settings.hasSeenWelcome} onChange={(v) => update({ hasSeenWelcome: !v })} />
              </Row>
            </>
          )}

          {section === 'editing' && (
            <>
              <p className="text-xs text-fg-3 mb-2">Used as starting values in the New Project wizard and when opening the editor.</p>
              <Row title="Aspect ratio">
                <Segmented
                  label="Default aspect ratio"
                  value={settings.defaultAspectRatio}
                  onChange={(v: AspectRatio) => update({ defaultAspectRatio: v })}
                  options={[
                    { value: '9:16', label: '9:16' },
                    { value: '16:9', label: '16:9' },
                    { value: '1:1', label: '1:1' },
                  ]}
                />
              </Row>
              <Row title="Target duration">
                <Field label="Seconds" htmlFor="def-dur">
                  <Input
                    id="def-dur"
                    type="number"
                    min={15}
                    max={180}
                    value={settings.defaultDurationSeconds}
                    onChange={(e) => {
                      const v = Math.round(Number(e.target.value));
                      if (v >= 15 && v <= 180) update({ defaultDurationSeconds: v });
                    }}
                    className="w-24 font-mono"
                  />
                </Field>
              </Row>
              <Row title="Frame rate">
                <Select aria-label="Default frame rate" value={settings.defaultFps} onChange={(e) => update({ defaultFps: Number(e.target.value) as 24 | 30 | 60 })} className="w-28">
                  <option value={24}>24 fps</option>
                  <option value={30}>30 fps</option>
                  <option value={60}>60 fps</option>
                </Select>
              </Row>
              <Row title="Caption preset">
                <Select aria-label="Default caption preset" value={settings.defaultCaptionPresetId} onChange={(e) => update({ defaultCaptionPresetId: e.target.value })} className="w-52">
                  {CAPTION_PRESETS.map((c) => (
                    <option key={c.id} value={c.id}>
                      {c.name}
                    </option>
                  ))}
                </Select>
              </Row>
              <Row title="Snapping on by default">
                <Switch label="Snapping on by default" checked={settings.defaultSnapping} onChange={(v) => update({ defaultSnapping: v })} />
              </Row>
              <Row title="Preview quality" description="Available after engine integration — the prototype has no rendered preview.">
                <Unavailable>
                  <Select aria-label="Preview quality" value={settings.previewQuality} className="w-36">
                    <option value="draft">Draft</option>
                    <option value="standard">Standard</option>
                  </Select>
                </Unavailable>
              </Row>
            </>
          )}

          {section === 'engine' && (
            <>
              <p className="text-xs text-fg-3 mb-2">
                The local editing engine (Python + FFmpeg) is not connected to the UI prototype. Nothing on this page is detected or measured.
              </p>
              <Row title="FFmpeg / FFprobe" description="Used for probing, proxies, rendering and output QA.">
                <Badge>Not checked in UI prototype</Badge>
              </Row>
              <Row title="Transcription model" description="faster-whisper, running locally.">
                <Badge>Not checked in UI prototype</Badge>
              </Row>
              <Row title="Processing mode" description="CPU is the default; GPU acceleration is enabled only after capability detection.">
                <Unavailable>
                  <Select aria-label="Processing mode" value="cpu" className="w-36">
                    <option value="cpu">CPU</option>
                    <option value="gpu">GPU (if detected)</option>
                  </Select>
                </Unavailable>
              </Row>
              <Row title="Local storage" description="Project folders hold originals, proxies, transcripts, timelines, renders and reports.">
                <Badge>Available after engine integration</Badge>
              </Row>
            </>
          )}

          {section === 'agent' && (
            <div className="flex flex-col gap-4">
              <div className="flex items-center gap-2">
                <p className="text-[13px] font-medium text-fg">MCP server</p>
                <Badge tone="warning">Disconnected in UI prototype</Badge>
              </div>
              <p className="text-[13px] text-fg-2 leading-relaxed">
                AgenEditor will expose project-scoped editing tools to Codex or Claude Code over the Model Context Protocol. Agents will reference assets by ID, work only
                inside the project folder, and never get an unrestricted shell. Every timeline change is committed as a reviewable revision.
              </p>
              <div>
                <p className="text-[13px] font-medium text-fg">Agent roles</p>
                <p className="text-xs text-fg-3 mt-0.5">
                  Used by the one-click Plan → Execute → Review prompts in the editor's agent panel.
                </p>
                {AGENT_ROLES.map((role) => {
                  const rm = settings.agentRoles[role];
                  const set = (patch: Partial<typeof rm>) =>
                    update({ agentRoles: { ...settings.agentRoles, [role]: { ...rm, ...patch } } });
                  return (
                    <Row key={role} title={`${ROLE_META[role].step}. ${ROLE_META[role].title}`} description={ROLE_META[role].summary}>
                      <div className="flex gap-2">
                        <Select aria-label={`${ROLE_META[role].title} model`} value={rm.model} onChange={(e) => set({ model: e.target.value })}>
                          {ROLE_MODELS.map((m) => (
                            <option key={m.id} value={m.id}>{m.label}</option>
                          ))}
                        </Select>
                        <Select aria-label={`${ROLE_META[role].title} effort`} value={rm.effort} onChange={(e) => set({ effort: e.target.value as AgentEffort })}>
                          {EFFORTS.map((ef) => (
                            <option key={ef} value={ef}>{ef}</option>
                          ))}
                        </Select>
                      </div>
                    </Row>
                  );
                })}
              </div>
              {(['claude-code', 'codex'] as const).map((c) => (
                <div key={c}>
                  <p className="text-xs font-medium text-fg-2 mb-1">{c === 'codex' ? 'Codex' : 'Claude Code'} — sample configuration</p>
                  <pre className="p-3 rounded-md bg-app border border-line font-mono text-[11px] text-fg-2 whitespace-pre-wrap">{MCP_CONFIG_SNIPPET[c]}</pre>
                </div>
              ))}
              <p className="text-xs text-fg-3">Sample snippets only — the server binary is not shipped yet.</p>
            </div>
          )}

          {section === 'privacy' && (
            <>
              <div className="text-[13px] text-fg-2 leading-relaxed flex flex-col gap-2 mb-2">
                <p>
                  <strong className="text-fg">Local-first.</strong> Footage, transcripts and project files stay on this machine. The UI prototype makes no network requests
                  apart from loading fonts.
                </p>
                <p>
                  <strong className="text-fg">External models.</strong> When you hand a brief to Codex or Claude Code, that agent's provider receives the text you send and any
                  tool results the agent requests. AgenEditor will show exactly what is shared before it leaves your machine.
                </p>
              </div>
              <Row title="Ask before sharing data with external models" description="Mock toggle for design review — no data is shared in the prototype.">
                <Switch
                  label="Ask before sharing data with external models"
                  checked={!settings.allowExternalSharing}
                  onChange={(v) => update({ allowExternalSharing: !v })}
                />
              </Row>
            </>
          )}

          {section === 'about' && (
            <div className="flex flex-col gap-4">
              <dl className="grid grid-cols-[max-content_1fr] gap-x-6 gap-y-1.5 text-[13px]">
                <dt className="text-fg-3">Version</dt>
                <dd className="text-fg">UI Prototype 0.1.0</dd>
                <dt className="text-fg-3">Data</dt>
                <dd className="text-fg">{persistsLocally ? 'Demo fixtures, saved in this browser' : 'Demo fixtures, this session only'}</dd>
                <dt className="text-fg-3">Licenses</dt>
                <dd className="text-fg-2">React, React Router, Radix UI, Tailwind CSS, Lucide (MIT/ISC). Fonts: Inter, JetBrains Mono (OFL). All artwork is CSS-generated.</dd>
              </dl>
              <div>
                <p className="text-[13px] font-medium text-fg mb-2">Keyboard shortcuts</p>
                <ShortcutsTable />
              </div>
              <div className="flex flex-wrap gap-2 pt-2 border-t border-line">
                <Button variant="danger" onClick={() => setConfirmReset(true)}>
                  Reset Demo Data
                </Button>
                <Button
                  onClick={() => {
                    reset();
                    showToast({ type: 'success', title: 'Preferences reset' });
                  }}
                >
                  Reset preferences
                </Button>
              </div>
            </div>
          )}
        </Card>
      </div>
      <ConfirmDialog
        open={confirmReset}
        onOpenChange={setConfirmReset}
        destructive
        title="Reset demo data?"
        message="All demo projects, timelines, notes and jobs return to their original fixtures."
        confirmLabel="Reset demo data"
        onConfirm={async () => {
          await resetDemoData();
          showToast({ type: 'success', title: 'Demo data restored' });
        }}
      />
    </PageLayout>
  );
};
