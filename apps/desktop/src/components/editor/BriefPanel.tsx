import React, { useEffect, useState } from 'react';
import { Save } from 'lucide-react';
import { DEFAULT_BRIEF_OPTIONS, type BriefOptions, type DemoProject } from '../../types';
import { EDITING_STYLE_PRESETS } from '../../mocks/fixtures';
import { Button, Select, Switch } from '../ui/primitives';
import { showToast } from '../common/Toast';

interface Props {
  project: DemoProject;
  onSave: (patch: Pick<DemoProject, 'brief' | 'briefOptions' | 'stylePresetId'>) => Promise<void> | void;
}

export const BRIEF_OPTION_LABELS: { key: keyof BriefOptions; label: string; description: string }[] = [
  { key: 'strongHook', label: 'Strong hook', description: 'Lead with the most compelling line in the first 3 s.' },
  { key: 'cleanCaptions', label: 'Clean captions', description: 'Accurate, readable captions inside safe margins.' },
  { key: 'useBroll', label: 'Use B-roll', description: 'Cut to supplied B-roll at mentioned cues.' },
  { key: 'balanceMusic', label: 'Balance music', description: 'Keep the supplied music bed under speech.' },
];

export const BriefPanel: React.FC<Props> = ({ project, onSave }) => {
  const [brief, setBrief] = useState(project.brief);
  const [opts, setOpts] = useState<BriefOptions>(project.briefOptions ?? DEFAULT_BRIEF_OPTIONS);
  const [style, setStyle] = useState(project.stylePresetId ?? EDITING_STYLE_PRESETS[0].id);

  useEffect(() => {
    setBrief(project.brief);
    setOpts(project.briefOptions ?? DEFAULT_BRIEF_OPTIONS);
    setStyle(project.stylePresetId ?? EDITING_STYLE_PRESETS[0].id);
  }, [project.id, project.brief, project.briefOptions, project.stylePresetId]);

  const dirty =
    brief !== project.brief ||
    style !== (project.stylePresetId ?? EDITING_STYLE_PRESETS[0].id) ||
    JSON.stringify(opts) !== JSON.stringify(project.briefOptions ?? DEFAULT_BRIEF_OPTIONS);

  const save = async () => {
    await onSave({ brief, briefOptions: opts, stylePresetId: style });
    showToast({ type: 'success', title: 'Brief saved', message: 'Saved to the demo project.' });
  };

  return (
    <div className="h-full flex flex-col">
      <div className="flex-1 overflow-y-auto p-3 flex flex-col gap-4">
        <div className="flex flex-col gap-1">
          <label htmlFor="brief-text" className="text-xs font-medium text-fg-2">
            Editing goal
          </label>
          <textarea
            id="brief-text"
            rows={7}
            value={brief}
            onChange={(e) => setBrief(e.target.value)}
            className="w-full p-2.5 rounded-md bg-app border border-line text-[13px] text-fg leading-relaxed focus:outline-none focus:border-accent resize-y"
          />
        </div>
        <div className="flex flex-col gap-1">
          <label htmlFor="brief-style" className="text-xs font-medium text-fg-2">
            Editing style
          </label>
          <Select id="brief-style" value={style} onChange={(e) => setStyle(e.target.value)}>
            {EDITING_STYLE_PRESETS.map((p) => (
              <option key={p.id} value={p.id}>
                {p.name}
              </option>
            ))}
          </Select>
        </div>
        <div className="flex flex-col gap-1">
          <span className="text-xs font-medium text-fg-2">Priorities</span>
          {BRIEF_OPTION_LABELS.map((o) => (
            <Switch
              key={o.key}
              label={o.label}
              description={o.description}
              checked={opts[o.key]}
              onChange={(v) => setOpts((p) => ({ ...p, [o.key]: v }))}
            />
          ))}
        </div>
      </div>
      <div className="p-2 border-t border-line flex items-center justify-between gap-2">
        <span className="text-[11px] text-fg-3">{dirty ? 'Unsaved changes' : 'Saved to demo project'}</span>
        <Button variant="primary" size="sm" icon={<Save className="w-3.5 h-3.5" />} disabled={!dirty} onClick={save}>
          Save brief
        </Button>
      </div>
    </div>
  );
};
