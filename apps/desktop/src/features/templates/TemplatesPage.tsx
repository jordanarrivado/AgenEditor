import React, { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { Check, Wand2 } from 'lucide-react';
import { useAppData } from '../../app/AppDataContext';
import { CAPTION_PRESETS, EDITING_STYLE_PRESETS } from '../../mocks/fixtures';
import type { EditingStylePreset } from '../../types';
import { PageLayout } from '../../components/projects/ProjectBits';
import { Badge, Button, Card, Field, Select } from '../../components/ui/primitives';
import { Modal } from '../../components/ui/Modal';
import { showToast } from '../../components/common/Toast';

/** Abstract layout sketch per style (CSS only). */
const LayoutSketch: React.FC<{ id: string }> = ({ id }) => {
  const broll = id === 'style_educational_explainer' || id === 'style_ugc_product';
  const big = id === 'style_podcast_highlight';
  return (
    <div aria-hidden className="h-36 bg-[#0D131C] flex items-center justify-center gap-3 border-b border-line">
      <div className="h-28 aspect-[9/16] rounded-md border border-white/20 bg-gradient-to-b from-[#1C2634] to-[#0B0F15] relative overflow-hidden">
        {broll && <div className="absolute inset-x-1 top-2 h-9 rounded bg-track-broll/50" />}
        <div className="absolute left-1/2 -translate-x-1/2 top-1/2 -translate-y-1/2 w-7 h-7 rounded-full bg-track-video/60" />
        <div className={`absolute inset-x-2 bottom-5 rounded-sm ${big ? 'h-3 bg-[#FFE600]/90' : 'h-1.5 bg-white/80'}`} />
      </div>
      <div className="flex flex-col gap-1 w-24">
        {[0.9, 0.6, 0.75, 0.4].map((w, i) => (
          <div key={i} className="h-1.5 rounded-full bg-white/15" style={{ width: `${w * 100}%` }} />
        ))}
      </div>
    </div>
  );
};

export const TemplatesPage: React.FC = () => {
  const navigate = useNavigate();
  const { projects, updateProject } = useAppData();
  const [applying, setApplying] = useState<EditingStylePreset | null>(null);
  const [target, setTarget] = useState('');

  return (
    <PageLayout
      title="Templates"
      width="wide"
      description="Editing styles control how your existing footage is cut — pacing, captions and B-roll usage. They never generate footage, images, voices or music."
    >
      <ul className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-4 gap-4">
        {EDITING_STYLE_PRESETS.map((t) => (
          <li key={t.id}>
            <Card className="overflow-hidden h-full flex flex-col">
              <LayoutSketch id={t.id} />
              <div className="p-4 flex-1 flex flex-col gap-2">
                <h2 className="text-[15px] font-semibold text-fg">{t.name}</h2>
                <p className="text-[13px] text-fg-2 leading-relaxed">{t.headline}</p>
                <dl className="text-xs flex flex-col gap-1.5 mt-1">
                  <div>
                    <dt className="text-fg-3">Pacing</dt>
                    <dd className="text-fg-2">{t.pacing}</dd>
                  </div>
                  <div>
                    <dt className="text-fg-3">Captions</dt>
                    <dd className="text-fg-2">{t.captionStyle}</dd>
                  </div>
                  <div>
                    <dt className="text-fg-3">B-roll</dt>
                    <dd className="text-fg-2">{t.bRollUsage}</dd>
                  </div>
                </dl>
                <div className="flex flex-wrap gap-1 mt-1">
                  {t.recommendedFormats.map((f) => (
                    <Badge key={f}>{f}</Badge>
                  ))}
                </div>
                <div className="mt-auto pt-3 flex gap-2">
                  <Button
                    size="sm"
                    variant="primary"
                    icon={<Wand2 className="w-3.5 h-3.5" />}
                    disabled={projects.length === 0}
                    onClick={() => {
                      setApplying(t);
                      setTarget(projects[0]?.id ?? '');
                    }}
                  >
                    Apply to demo brief
                  </Button>
                  <Button size="sm" onClick={() => navigate(`/projects/new?style=${t.id}`)}>
                    New project
                  </Button>
                </div>
              </div>
            </Card>
          </li>
        ))}
      </ul>

      <section className="mt-8" aria-labelledby="cap-h">
        <h2 id="cap-h" className="text-[15px] font-semibold text-fg mb-3">
          Caption presets
        </h2>
        <ul className="grid grid-cols-1 md:grid-cols-3 gap-4">
          {CAPTION_PRESETS.map((c) => (
            <li key={c.id}>
              <Card className="p-4">
                <div className="h-16 rounded-md bg-[#0D131C] flex items-center justify-center mb-3">
                  <span
                    className="px-2 py-0.5 rounded font-bold"
                    style={{
                      color: c.color,
                      fontSize: 16,
                      textTransform: c.textTransform,
                      background: c.hasBackground ? 'rgba(0,0,0,.7)' : undefined,
                      textShadow: c.hasBackground ? undefined : '0 2px 6px #000',
                    }}
                  >
                    Your footage
                  </span>
                </div>
                <p className="text-[13px] font-semibold text-fg">{c.name}</p>
                <p className="text-xs text-fg-2 mt-0.5">{c.description}</p>
              </Card>
            </li>
          ))}
        </ul>
      </section>

      <Modal
        open={!!applying}
        onOpenChange={(o) => !o && setApplying(null)}
        size="sm"
        title={`Apply “${applying?.name}”`}
        description="Sets the project's editing style and appends the style notes to its brief (demo data)."
        footer={
          <>
            <Button onClick={() => setApplying(null)}>Cancel</Button>
            <Button
              variant="primary"
              icon={<Check className="w-3.5 h-3.5" />}
              disabled={!target}
              onClick={async () => {
                const p = projects.find((x) => x.id === target);
                if (!p || !applying) return;
                const line = `Style: ${applying.name} — ${applying.pacing}.`;
                await updateProject(p.id, {
                  stylePresetId: applying.id,
                  brief: p.brief.includes(line) ? p.brief : `${p.brief.trim()}\n\n${line}`,
                });
                showToast({ type: 'success', title: 'Style applied', message: `${applying.name} → ${p.name}` });
                setApplying(null);
              }}
            >
              Apply
            </Button>
          </>
        }
      >
        <Field label="Project" htmlFor="tpl-target">
          <Select id="tpl-target" value={target} onChange={(e) => setTarget(e.target.value)}>
            {projects.map((p) => (
              <option key={p.id} value={p.id}>
                {p.name}
              </option>
            ))}
          </Select>
        </Field>
      </Modal>
    </PageLayout>
  );
};
