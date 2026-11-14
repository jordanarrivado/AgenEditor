import React from 'react';
import { CheckCircle2, Eye, PencilLine } from 'lucide-react';
import type { DemoProject, ProjectStatus } from '../../types';
import { Badge, cx } from '../ui/primitives';

export const STATUS_META: Record<ProjectStatus, { label: string; tone: 'neutral' | 'accent' | 'success'; icon: React.ReactNode }> = {
  draft: { label: 'Draft', tone: 'neutral', icon: <PencilLine aria-hidden className="w-3 h-3" /> },
  review: { label: 'In review', tone: 'accent', icon: <Eye aria-hidden className="w-3 h-3" /> },
  approved: { label: 'Approved (demo)', tone: 'success', icon: <CheckCircle2 aria-hidden className="w-3 h-3" /> },
};

/** Status is conveyed by icon + text, not colour alone. */
export const ProjectStatusBadge: React.FC<{ status: ProjectStatus }> = ({ status }) => (
  <Badge tone={STATUS_META[status].tone} icon={STATUS_META[status].icon}>
    {STATUS_META[status].label}
  </Badge>
);

export const PLATFORM_LABEL: Record<DemoProject['platform'], string> = {
  instagram: 'Instagram Reel',
  tiktok: 'TikTok',
  youtube: 'YouTube Short',
  custom: 'Custom',
};

export function aspectLabel(p: Pick<DemoProject, 'width' | 'height'>): string {
  return p.height > p.width ? '9:16' : p.width === p.height ? '1:1' : '16:9';
}

export function durationLabel(p: Pick<DemoProject, 'targetDurationFrames' | 'fpsNum' | 'fpsDen'>): string {
  return `${Math.round(p.targetDurationFrames / (p.fpsNum / p.fpsDen))} s`;
}

const dateFmt = new Intl.DateTimeFormat('en', { month: 'short', day: 'numeric', year: 'numeric', hour: '2-digit', minute: '2-digit', timeZone: 'UTC' });
export function formatDate(iso: string): string {
  try {
    return `${dateFmt.format(new Date(iso))} UTC`;
  } catch {
    return iso;
  }
}

const PALETTES: Record<string, [string, string]> = {
  instagram: ['#5E88D7', '#C5A56C'],
  tiktok: ['#5CABC8', '#F07C82'],
  youtube: ['#F07C82', '#5E88D7'],
  custom: ['#6BAE9B', '#84A9FF'],
};

/** CSS-generated placeholder thumbnail (no stock imagery). */
export const ProjectThumb: React.FC<{ project: DemoProject; className?: string }> = ({ project, className }) => {
  const [a, b] = PALETTES[project.platform] ?? PALETTES.custom;
  const portrait = project.height > project.width;
  return (
    <div
      aria-hidden
      className={cx('relative overflow-hidden flex items-center justify-center bg-[#0D131C]', className)}
      style={{ backgroundImage: `radial-gradient(circle at 25% 20%, ${a}55, transparent 55%), radial-gradient(circle at 80% 85%, ${b}44, transparent 50%)` }}
    >
      <div
        className="border border-white/25 rounded-md bg-black/25 flex flex-col justify-end p-1.5 gap-1"
        style={{ aspectRatio: portrait ? '9 / 16' : '16 / 9', height: portrait ? '78%' : undefined, width: portrait ? undefined : '70%' }}
      >
        <span className="h-1 rounded-full bg-white/50 w-3/4 mx-auto" />
        <span className="h-1 rounded-full bg-white/30 w-1/2 mx-auto" />
      </div>
      <span className="absolute bottom-1.5 right-1.5 text-[10px] font-mono text-white/80 bg-black/50 px-1 rounded">{aspectLabel(project)}</span>
    </div>
  );
};

export const PageLayout: React.FC<{
  title: string;
  eyebrow?: React.ReactNode;
  description?: React.ReactNode;
  actions?: React.ReactNode;
  children: React.ReactNode;
  width?: 'normal' | 'wide' | 'narrow';
}> = ({ title, eyebrow, description, actions, children, width = 'normal' }) => (
  <div className="flex-1 overflow-y-auto">
    <div className={cx('mx-auto px-6 py-6', width === 'wide' ? 'max-w-[1400px]' : width === 'narrow' ? 'max-w-3xl' : 'max-w-6xl')}>
      <header className="flex flex-wrap items-end justify-between gap-4 mb-6">
        <div className="min-w-0">
          {eyebrow && <div className="mb-1.5">{eyebrow}</div>}
          <h1 className="text-[28px] font-semibold tracking-tight text-fg leading-tight">{title}</h1>
          {description && <p className="text-sm text-fg-2 mt-1.5 max-w-2xl leading-relaxed">{description}</p>}
        </div>
        {actions && <div className="flex items-center gap-2 flex-wrap">{actions}</div>}
      </header>
      {children}
    </div>
  </div>
);
