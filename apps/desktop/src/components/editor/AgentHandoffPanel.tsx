/**
 * Agent handoff (spec §4.7). Text-only brief for Codex / Claude Code; the MCP
 * connection is shown as NOT connected. No fake connect button, no fake chat.
 */
import React, { useMemo, useState } from 'react';
import { Bot, ClipboardCopy, FileSearch, Film, PlugZap, Save, ShieldCheck, Scissors, Terminal } from 'lucide-react';
import { useSettings } from '../../app/SettingsContext';
import {
  ROLE_META,
  buildRolePrompt,
  claudeCodeCommand,
  modelLabel,
  type AgentRole,
} from '../../utils/agentRoles';
import type { DemoAsset, DemoProject, DemoRevision } from '../../types';
import { AGENT_CLIENT_LABEL, buildAgentBrief, copyText, type AgentClient } from '../../utils/agentBrief';
import { Badge, Button, Segmented, cx } from '../ui/primitives';
import { AssetPrepCard, EMPTY_ASSET_PREP, type AssetPrepState } from './AssetPrepCard';
import { assetsReady } from '../../utils/assetRequests';
import { Modal } from '../ui/Modal';
import { showToast } from '../common/Toast';

/** In-memory drafts (cleared on reload) — "save as in-memory draft". */
const drafts = new Map<string, string>();

export const MCP_CONFIG_SNIPPET: Record<AgentClient, string> = {
  codex: `# ~/.codex/config.toml  (sample — server not shipped yet)
[mcp_servers.ageneditor]
command = "ageneditor-mcp"
args = ["--project-root", "<your projects folder>"]`,
  'claude-code': `# sample — server not shipped yet
claude mcp add ageneditor -- ageneditor-mcp --project-root "<your projects folder>"`,
};

const CAPABILITIES = [
  { icon: <FileSearch className="w-4 h-4" />, title: 'Asset inspection', body: 'Read metadata and transcripts of supplied media.' },
  { icon: <Scissors className="w-4 h-4" />, title: 'Timeline edits', body: 'Propose and commit revisions with rationale.' },
  { icon: <Film className="w-4 h-4" />, title: 'Preview render', body: 'Request low-res preview renders.' },
  { icon: <ShieldCheck className="w-4 h-4" />, title: 'QA', body: 'Dimension, duration, loudness and caption checks.' },
];

interface Props {
  project: DemoProject;
  assets: DemoAsset[];
  revision: DemoRevision | null;
  /** 'dock' = compact editor tab; 'full' = dialog body. */
  variant?: 'dock' | 'full';
}

/** Asset checklist per project, kept for the session (cleared on reload). */
const assetPrepByProject = new Map<string, AssetPrepState>();

/**
 * One-click Plan → Prepare assets → Execute → Review workflow for Claude Code.
 * Execute is locked until every requested asset is ready or dropped.
 */
const RoleWorkflow: React.FC<Props> = ({ project, assets, revision, variant = 'dock' }) => {
  const { settings } = useSettings();
  const profile = settings.agentRoles;
  const [prep, setPrepState] = useState<AssetPrepState>(() => assetPrepByProject.get(project.id) ?? EMPTY_ASSET_PREP);
  const setPrep = (s: AssetPrepState) => {
    assetPrepByProject.set(project.id, s);
    setPrepState(s);
  };
  const executeUnlocked = prep.requests !== null && assetsReady(prep.requests, prep.status);
  const readyAssets = (prep.requests ?? []).filter((r) => prep.status[r.id] === 'ready');

  const copyRole = async (role: AgentRole) => {
    const ok = await copyText(buildRolePrompt({ role, profile, project, assets, revision, readyAssets }));
    const meta = ROLE_META[role];
    showToast(
      ok
        ? { type: 'success', title: `${meta.title} prompt copied`, message: `Paste it into Claude Code running ${modelLabel(profile[role].model)}.` }
        : { type: 'warning', title: 'Clipboard unavailable', message: 'Use the handoff text below instead.' }
    );
  };
  const copyCommand = async (role: AgentRole) => {
    const ok = await copyText(claudeCodeCommand(profile[role]));
    showToast(
      ok
        ? { type: 'success', title: 'Command copied', message: 'Run it in a terminal to open Claude Code on this model.' }
        : { type: 'warning', title: 'Clipboard unavailable', message: claudeCodeCommand(profile[role]) }
    );
  };

  const roleCard = (role: AgentRole, locked?: string) => {
    const meta = ROLE_META[role];
    const rm = profile[role];
    return (
      <li key={role} data-testid={`role-${role}`} className="p-2.5 rounded-lg border border-line bg-surface flex flex-col gap-2">
        <span className="flex items-center gap-1.5 flex-wrap">
          <span className="text-[13px] font-medium text-fg">
            {meta.step}. {meta.title}
          </span>
          <Badge tone="accent">
            {modelLabel(rm.model)} · {rm.effort}
          </Badge>
          {locked && <Badge tone="warning">Locked</Badge>}
        </span>
        <span className="text-xs text-fg-2">{meta.summary}</span>
        {locked && <span className="text-xs text-warning">{locked}</span>}
        <span className="flex flex-wrap gap-1.5">
          <Button
            size="sm"
            variant="primary"
            icon={<ClipboardCopy className="w-3.5 h-3.5" />}
            onClick={() => copyRole(role)}
            disabled={Boolean(locked)}
          >
            Copy {meta.title.toLowerCase()} prompt
          </Button>
          <Button size="sm" variant="ghost" icon={<Terminal className="w-3.5 h-3.5" />} onClick={() => copyCommand(role)} aria-label={`Copy launch command for ${meta.title}`}>
            Command
          </Button>
        </span>
      </li>
    );
  };

  return (
    <section aria-labelledby={`role-workflow-${variant}`} className="flex flex-col gap-2">
      <h3 id={`role-workflow-${variant}`} className="text-[11px] font-semibold uppercase tracking-wide text-fg-3">
        One-click agent workflow
      </h3>
      <ol className={cx('grid gap-2', variant === 'full' ? 'grid-cols-2' : 'grid-cols-1')}>
        {roleCard('planner')}
        <AssetPrepCard state={prep} onChange={setPrep} />
        {roleCard(
          'executor',
          executeUnlocked
            ? undefined
            : prep.requests === null
              ? "Load the planner's asset requests first (or choose No assets needed)."
              : 'Waiting for every asset to be marked Ready or Drop.'
        )}
        {roleCard('reviewer')}
      </ol>
      <p className="text-xs text-fg-3">Models and effort are set in Settings → Agent / MCP. AgenEditor edits media; it never generates it.</p>
    </section>
  );
};

export const AgentHandoffContent: React.FC<Props> = ({ project, assets, revision, variant = 'dock' }) => {
  const [client, setClient] = useState<AgentClient>('claude-code');
  const [showSetup, setShowSetup] = useState(false);
  const generated = useMemo(() => buildAgentBrief({ project, assets, revision, client }), [project, assets, revision, client]);
  const draftKey = `${project.id}:${client}`;
  const [text, setText] = useState<string | null>(null);
  const value = text ?? drafts.get(draftKey) ?? generated;

  const copy = async () => {
    const ok = await copyText(value);
    showToast(
      ok
        ? { type: 'success', title: 'Handoff brief copied', message: `Paste it into ${AGENT_CLIENT_LABEL[client]}. It is labelled Demo Mode.` }
        : { type: 'warning', title: 'Clipboard unavailable', message: 'Select the text and copy it manually.' }
    );
  };

  return (
    <div className={cx('flex flex-col gap-3', variant === 'dock' ? 'p-3' : '')}>
      <div className="flex items-center justify-between gap-2 p-2.5 rounded-lg border border-warning/30 bg-warning/8">
        <span className="flex items-center gap-2 text-[13px] text-fg">
          <PlugZap aria-hidden className="w-4 h-4 text-warning" />
          MCP connection
        </span>
        <Badge tone="warning">Not connected in UI prototype</Badge>
      </div>

      {variant === 'full' && (
        <p className="text-[13px] text-fg-2 leading-relaxed">
          AgenEditor is designed to be driven by an external coding agent through an MCP server that exposes safe,
          project-scoped editing tools. That server is not part of this UI prototype — you can prepare and copy the
          handoff brief now; nothing will be executed.
        </p>
      )}

      {client === 'claude-code' && <RoleWorkflow project={project} assets={assets} revision={revision} variant={variant} />}

      <div className="flex items-center justify-between gap-2">
        <span className="text-xs text-fg-2">Client</span>
        <Segmented
          size="sm"
          label="Agent client"
          value={client}
          onChange={(c) => {
            setClient(c);
            setText(null);
          }}
          options={[
            { value: 'claude-code', label: 'Claude Code' },
            { value: 'codex', label: 'Codex' },
          ]}
        />
      </div>

      <div className="flex flex-col gap-1">
        <label htmlFor={`handoff-${variant}`} className="text-xs font-medium text-fg-2 flex items-center gap-1.5">
          <Bot aria-hidden className="w-3.5 h-3.5" /> Handoff brief (text only)
        </label>
        <textarea
          id={`handoff-${variant}`}
          data-testid="handoff-text"
          value={value}
          onChange={(e) => setText(e.target.value)}
          rows={variant === 'full' ? 14 : 12}
          className="w-full p-2.5 rounded-md bg-app border border-line font-mono text-[11px] text-fg-2 leading-relaxed focus:outline-none focus:border-accent resize-y"
        />
      </div>

      <div className="flex flex-wrap gap-2">
        <Button variant="primary" size="sm" icon={<ClipboardCopy className="w-3.5 h-3.5" />} onClick={copy}>
          Copy instructions
        </Button>
        <Button
          size="sm"
          icon={<Save className="w-3.5 h-3.5" />}
          onClick={() => {
            drafts.set(draftKey, value);
            showToast({ type: 'success', title: 'Draft kept for this session', message: 'In-memory only — cleared when the app reloads.' });
          }}
        >
          Save draft
        </Button>
        <Button size="sm" variant="ghost" onClick={() => setShowSetup((s) => !s)} aria-expanded={showSetup}>
          {showSetup ? 'Hide' : 'View'} connection instructions
        </Button>
        {text !== null && (
          <Button size="sm" variant="ghost" onClick={() => { setText(null); drafts.delete(draftKey); }}>
            Regenerate
          </Button>
        )}
      </div>

      {showSetup && (
        <div className="rounded-lg border border-line bg-surface p-3 text-xs flex flex-col gap-2">
          <p className="text-fg-2">
            Once the AgenEditor MCP server ships, register it with {AGENT_CLIENT_LABEL[client]}:
          </p>
          <pre className="p-2 rounded-md bg-app border border-line font-mono text-[11px] text-fg-2 whitespace-pre-wrap">
            {MCP_CONFIG_SNIPPET[client]}
          </pre>
          <p className="text-fg-3">Sample configuration only — the command does not exist yet.</p>
        </div>
      )}

      <div>
        <h3 className="text-[11px] font-semibold uppercase tracking-wide text-fg-3 mb-2">Planned tools</h3>
        <ul className={cx('grid gap-2', variant === 'full' ? 'grid-cols-2' : 'grid-cols-1')}>
          {CAPABILITIES.map((c) => (
            <li key={c.title} className="p-2.5 rounded-lg border border-line bg-surface flex gap-2.5">
              <span aria-hidden className="text-fg-3 mt-0.5">{c.icon}</span>
              <span className="min-w-0">
                <span className="flex items-center gap-1.5 flex-wrap">
                  <span className="text-[13px] font-medium text-fg">{c.title}</span>
                  <Badge>Planned · unavailable</Badge>
                </span>
                <span className="block text-xs text-fg-2 mt-0.5">{c.body}</span>
              </span>
            </li>
          ))}
        </ul>
      </div>
    </div>
  );
};

export const AgentHandoffDialog: React.FC<Props & { open: boolean; onOpenChange: (o: boolean) => void }> = ({
  open,
  onOpenChange,
  ...props
}) => (
  <Modal
    open={open}
    onOpenChange={onOpenChange}
    size="lg"
    icon={<Bot className="w-5 h-5" />}
    title="Prepare agent handoff"
    badge={<Badge tone="accent">Demo Mode</Badge>}
    description={`${props.project.name} — ${props.project.description}`}
  >
    <AgentHandoffContent {...props} variant="full" />
  </Modal>
);
