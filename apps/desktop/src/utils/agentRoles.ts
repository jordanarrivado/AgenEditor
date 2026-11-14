/**
 * Plan → Execute → Review role profile for the external agent handoff.
 *
 * Each role gets a ready-to-paste prompt so the user clicks instead of typing.
 * The prompts target an external Claude Code session driving the AgenEditor MCP
 * tools; nothing here calls a model. Media is edited only — never generated.
 */
import type { DemoAsset, DemoProject, DemoRevision } from '../types';
import { buildAgentBrief } from './agentBrief';
import { ASSET_REQUEST_FORMAT, type AssetRequest } from './assetRequests';

export type AgentRole = 'planner' | 'executor' | 'reviewer';
export type AgentEffort = 'low' | 'medium' | 'high' | 'xhigh' | 'max';

export interface RoleModel {
  model: string;
  effort: AgentEffort;
}

export type RoleProfile = Record<AgentRole, RoleModel>;

export const AGENT_ROLES: AgentRole[] = ['planner', 'executor', 'reviewer'];

export const ROLE_MODELS: { id: string; label: string }[] = [
  { id: 'claude-opus-5-5', label: 'Claude Opus 5.5' },
  { id: 'claude-sonnet-5-5', label: 'Claude Sonnet 5.5' },
  { id: 'claude-haiku-5-5', label: 'Claude Haiku 5.5' },
];

export const EFFORTS: AgentEffort[] = ['low', 'medium', 'high', 'xhigh', 'max'];

/** Opus plans and reviews the cut; Sonnet carries out the edit list with MCP tools. */
export const DEFAULT_ROLE_PROFILE: RoleProfile = {
  planner: { model: 'claude-opus-5-5', effort: 'medium' },
  executor: { model: 'claude-sonnet-5-5', effort: 'medium' },
  reviewer: { model: 'claude-opus-5-5', effort: 'medium' },
};

export const ROLE_META: Record<AgentRole, { step: number; title: string; summary: string }> = {
  planner: { step: 1, title: 'Plan', summary: 'Reads transcript + brief, writes a source-cited edit list and the asset requests.' },
  executor: { step: 3, title: 'Execute', summary: 'Carries out the edit list through AgenEditor MCP tools.' },
  reviewer: { step: 4, title: 'Review', summary: 'Checks the cut and measured QA against the brief; sends notes back.' },
};

export function modelLabel(id: string): string {
  return ROLE_MODELS.find((m) => m.id === id)?.label ?? id;
}

/** Shell command that opens Claude Code on the role's model. */
export function claudeCodeCommand(role: RoleModel): string {
  return `claude --model ${role.model}`;
}

const ROLE_INSTRUCTIONS: Record<AgentRole, string[]> = {
  planner: [
    'ROLE: PLANNER. Do not call any tool that changes the timeline.',
    '1. Call list_assets and get_transcript for the speaker footage.',
    '2. Find one coherent idea that fits the target duration. Pick a strong opening hook, drop filler and dead air.',
    '3. Output an edit decision list (EDL): for each segment give asset ID, first and last transcript word IDs,',
    '   source frame range, order, and a one-line reason. Add caption and B-roll notes only for supplied assets.',
    '4. End with the EDL as a JSON block ready for submit_edit_plan. Never invent timestamps, quotes or assets.',
    '5. If the cut needs storyboard frames or B-roll the footage lacks, add a second JSON block in exactly this form',
    `   (use [] when nothing is needed): ${ASSET_REQUEST_FORMAT}`,
    '   kind is "image" or "video". The user generates these in ChatGPT, Gemini or Google Flow and imports them;',
    '   reference them in the EDL only by these ids. Do not generate media yourself.',
  ],
  executor: [
    'ROLE: EXECUTOR. Carry out the EDL below exactly; do not re-plan the story.',
    '1. Paste the planner EDL here: <EDL>',
    '   Use only registered assets. Generated assets (if any) are listed below and are labelled AI-generated (external).',
    '2. submit_edit_plan with that EDL, then validate_timeline. Fix only mechanical validation errors.',
    '3. commit_timeline with expected_revision, then start_preview_render and poll get_job_status.',
    '4. run_qa and report the real job and QA results. Never claim a render or QA that did not finish.',
  ],
  reviewer: [
    'ROLE: REVIEWER. Do not edit the timeline yourself.',
    '1. Read the brief, the committed revision, and the measured run_qa report.',
    '2. Check: hook in the first seconds, one clear idea, duration and aspect ratio, caption sync, audio peaks,',
    '   black frames, every clip source-linked.',
    '3. Reply APPROVE, or a numbered list of revision notes the executor can act on (cite word IDs / frames).',
    '   Label your notes as editorial advice; technical pass/fail comes only from QA measurements.',
  ],
};

export function buildRolePrompt(input: {
  role: AgentRole;
  profile: RoleProfile;
  project: DemoProject;
  assets: DemoAsset[];
  revision: DemoRevision | null;
  /** Generated assets the user marked ready (executor step). */
  readyAssets?: AssetRequest[];
}): string {
  const { role, profile, readyAssets = [], ...rest } = input;
  const meta = ROLE_META[role];
  const rm = profile[role];
  return [
    buildAgentBrief({ ...rest, client: 'claude-code' }),
    '',
    `--- Step ${meta.step} of 4: ${meta.title} · ${modelLabel(rm.model)} (${rm.model}), effort ${rm.effort} ---`,
    ...ROLE_INSTRUCTIONS[role],
    ...(role === 'executor' && readyAssets.length
      ? ['', 'Generated assets (AI-generated, external; imported by the user):', ...readyAssets.map((a) => `  - ${a.id} [${a.kind}] ${a.purpose}`)]
      : []),
  ].join('\n');
}
