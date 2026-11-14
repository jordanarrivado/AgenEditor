/**
 * Export dialog (spec §4.9). Configures a FUTURE export. It never renders,
 * writes or offers files for download. "Preview Export Settings" shows a
 * confirmation sheet; queuing adds a clearly labelled simulation job.
 */
import React, { useState } from 'react';
import { ArrowLeft, FileJson, FileText, FileVideo, FolderOpen, PackageCheck, Settings2 } from 'lucide-react';
import type { DemoProject, DemoRevision } from '../../types';
import { Badge, Button, Field, Input, Select, Switch, Unavailable } from '../ui/primitives';
import { Modal } from '../ui/Modal';
import { useAppData } from '../../app/AppDataContext';
import { showToast } from '../common/Toast';

export type ExportPreset = 'instagram' | 'youtube' | 'custom';

export interface ExportSettings {
  filename: string;
  preset: ExportPreset;
  fps: number;
  quality: 'standard' | 'high';
  captions: 'burned' | 'sidecar' | 'both';
  qaFirst: boolean;
}

export const PRESET_LABEL: Record<ExportPreset, string> = {
  instagram: 'Instagram Reel 1080×1920',
  youtube: 'YouTube Shorts 1080×1920',
  custom: 'Custom (project canvas)',
};

export function plannedArtifacts(s: ExportSettings): { name: string; icon: React.ReactNode; note: string }[] {
  const base = s.filename.replace(/\.mp4$/i, '') || 'final';
  const out = [{ name: `${base}.mp4`, icon: <FileVideo className="w-4 h-4" />, note: `H.264 · ${s.fps} fps · ${s.quality} quality` }];
  if (s.captions !== 'burned') out.push({ name: 'captions.srt', icon: <FileText className="w-4 h-4" />, note: 'Sidecar captions' });
  out.push({ name: 'timeline.json', icon: <FileJson className="w-4 h-4" />, note: 'Committed timeline revision' });
  if (s.qaFirst) out.push({ name: 'qa_report.json', icon: <FileJson className="w-4 h-4" />, note: 'QA results for the render' });
  return out;
}

interface Props {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  project: DemoProject;
  revision: DemoRevision | null;
  onViewActivity?: () => void;
}

export const ExportModal: React.FC<Props> = ({ open, onOpenChange, project, revision, onViewActivity }) => {
  const { startSimulationJob } = useAppData();
  const [step, setStep] = useState<'settings' | 'confirm' | 'queued'>('settings');
  const [s, setS] = useState<ExportSettings>({
    filename: 'final.mp4',
    preset: project.platform === 'youtube' ? 'youtube' : project.platform === 'custom' ? 'custom' : 'instagram',
    fps: project.fpsNum,
    quality: 'high',
    captions: 'burned',
    qaFirst: true,
  });
  const filenameError = /^[\w\-. ]+\.mp4$/i.test(s.filename.trim()) ? undefined : 'Use a file name ending in .mp4 (letters, numbers, - _ . and spaces).';

  const close = (o: boolean) => {
    onOpenChange(o);
    if (!o) setStep('settings');
  };

  const queue = async () => {
    await startSimulationJob(
      project.id,
      'export',
      `Simulation: export of v${revision?.revisionNumber ?? '?'} as ${s.filename} (${PRESET_LABEL[s.preset]}). No file will be produced.`
    );
    setStep('queued');
    showToast({ type: 'info', title: 'Simulation job queued', message: 'Progress is simulated in Activity. No file will be written.' });
  };

  const footer =
    step === 'settings' ? (
      <>
        <Button onClick={() => close(false)}>Cancel</Button>
        <Button variant="primary" disabled={!!filenameError} onClick={() => setStep('confirm')}>
          Preview Export Settings
        </Button>
      </>
    ) : step === 'confirm' ? (
      <>
        <Button icon={<ArrowLeft className="w-3.5 h-3.5" />} onClick={() => setStep('settings')}>
          Back
        </Button>
        <Button onClick={() => close(false)}>Done</Button>
        <Button variant="primary" onClick={queue}>
          Queue simulation job
        </Button>
      </>
    ) : (
      <>
        {onViewActivity && (
          <Button
            onClick={() => {
              close(false);
              onViewActivity();
            }}
          >
            View in Activity
          </Button>
        )}
        <Button variant="primary" onClick={() => close(false)}>
          Done
        </Button>
      </>
    );

  return (
    <Modal
      open={open}
      onOpenChange={close}
      icon={<Settings2 className="w-5 h-5" />}
      title={step === 'settings' ? 'Export settings' : 'Planned export'}
      badge={<Badge tone="warning">Configuration only — no render</Badge>}
      description={
        step === 'settings'
          ? `Configure how v${revision?.revisionNumber ?? '–'} of “${project.name}” will be exported once the media engine is connected.`
          : 'This is a preview of the proposed parameters. The UI prototype does not render, encode or write files.'
      }
      footer={footer}
    >
      {step === 'settings' ? (
        <form className="grid grid-cols-1 sm:grid-cols-2 gap-4" onSubmit={(e) => e.preventDefault()}>
          <Field label="File name" htmlFor="exp-name" error={filenameError} className="sm:col-span-2">
            <Input
              id="exp-name"
              value={s.filename}
              aria-invalid={!!filenameError}
              aria-describedby={filenameError ? 'exp-name-error' : undefined}
              onChange={(e) => setS({ ...s, filename: e.target.value })}
            />
          </Field>
          <Field label="Destination" htmlFor="exp-dest" hint="Folder selection requires the desktop app." className="sm:col-span-2">
            <div className="flex gap-2">
              <Input id="exp-dest" readOnly value="(not selected)" className="text-fg-3" />
              <Unavailable reason="Choosing a folder requires desktop (Tauri) integration">
                <Button icon={<FolderOpen className="w-3.5 h-3.5" />}>Browse…</Button>
              </Unavailable>
            </div>
          </Field>
          <Field label="Output preset" htmlFor="exp-preset">
            <Select id="exp-preset" value={s.preset} onChange={(e) => setS({ ...s, preset: e.target.value as ExportPreset })}>
              {(Object.keys(PRESET_LABEL) as ExportPreset[]).map((p) => (
                <option key={p} value={p}>
                  {PRESET_LABEL[p]}
                </option>
              ))}
            </Select>
          </Field>
          <Field label="Codec" htmlFor="exp-codec" hint="Fixed for the first release.">
            <Input id="exp-codec" readOnly value="H.264 (AVC) + AAC" />
          </Field>
          <Field label="Frame rate" htmlFor="exp-fps">
            <Select id="exp-fps" value={s.fps} onChange={(e) => setS({ ...s, fps: Number(e.target.value) })}>
              {[24, 30, 60].map((f) => (
                <option key={f} value={f}>
                  {f} fps
                </option>
              ))}
            </Select>
          </Field>
          <Field label="Quality" htmlFor="exp-quality">
            <Select id="exp-quality" value={s.quality} onChange={(e) => setS({ ...s, quality: e.target.value as ExportSettings['quality'] })}>
              <option value="standard">Standard</option>
              <option value="high">High</option>
            </Select>
          </Field>
          <Field label="Captions" htmlFor="exp-captions" className="sm:col-span-2">
            <Select id="exp-captions" value={s.captions} onChange={(e) => setS({ ...s, captions: e.target.value as ExportSettings['captions'] })}>
              <option value="burned">Burned-in</option>
              <option value="sidecar">Sidecar (.srt)</option>
              <option value="both">Both</option>
            </Select>
          </Field>
          <div className="sm:col-span-2">
            <Switch
              label="Run QA before export"
              description="Adds a QA report to the planned output."
              checked={s.qaFirst}
              onChange={(v) => setS({ ...s, qaFirst: v })}
            />
          </div>
        </form>
      ) : (
        <div className="flex flex-col gap-4" data-testid="export-summary">
          {step === 'queued' && (
            <div role="status" className="p-3 rounded-lg border border-accent/30 bg-accent/10 text-[13px] text-fg">
              A <strong>simulation</strong> job was added to Activity. Its progress is scripted; no video will be produced.
            </div>
          )}
          <dl className="grid grid-cols-[max-content_1fr] gap-x-6 gap-y-1.5 text-[13px]">
            <dt className="text-fg-3">File name</dt>
            <dd className="font-mono text-fg">{s.filename}</dd>
            <dt className="text-fg-3">Preset</dt>
            <dd className="text-fg">{PRESET_LABEL[s.preset]}</dd>
            <dt className="text-fg-3">Video</dt>
            <dd className="text-fg">
              H.264 · {project.width}×{project.height} · {s.fps} fps · {s.quality}
            </dd>
            <dt className="text-fg-3">Captions</dt>
            <dd className="text-fg">{s.captions === 'burned' ? 'Burned-in' : s.captions === 'sidecar' ? 'Sidecar .srt' : 'Burned-in + sidecar'}</dd>
            <dt className="text-fg-3">QA first</dt>
            <dd className="text-fg">{s.qaFirst ? 'Yes' : 'No'}</dd>
            <dt className="text-fg-3">Revision</dt>
            <dd className="text-fg">v{revision?.revisionNumber ?? '–'}</dd>
          </dl>
          <div>
            <h3 className="text-[11px] font-semibold uppercase tracking-wide text-fg-3 mb-2 flex items-center gap-1.5">
              <PackageCheck className="w-3.5 h-3.5" /> Planned output artifacts
            </h3>
            <ul className="flex flex-col gap-1.5">
              {plannedArtifacts(s).map((a) => (
                <li key={a.name} className="flex items-center gap-2.5 p-2 rounded-md border border-dashed border-line text-[13px]">
                  <span aria-hidden className="text-fg-3">{a.icon}</span>
                  <span className="font-mono text-fg">{a.name}</span>
                  <span className="text-xs text-fg-3 flex-1">{a.note}</span>
                  <Badge>Planned · not created</Badge>
                </li>
              ))}
            </ul>
          </div>
        </div>
      )}
    </Modal>
  );
};
