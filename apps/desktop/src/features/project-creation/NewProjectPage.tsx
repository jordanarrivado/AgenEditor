import React, { useMemo, useRef, useState } from 'react';
import { useNavigate, useSearchParams } from 'react-router-dom';
import { ArrowLeft, ArrowRight, Check, FolderInput, Info, Sparkles } from 'lucide-react';
import { useAppData } from '../../app/AppDataContext';
import { useSettings } from '../../app/SettingsContext';
import { DEFAULT_BRIEF_OPTIONS, type AspectRatio, type BriefOptions, type PlatformTarget } from '../../types';
import { EDITING_STYLE_PRESETS } from '../../mocks/fixtures';
import { BRIEF_OPTION_LABELS } from '../../components/editor/BriefPanel';
import { KIND_ICON } from '../../components/editor/MediaBin';
import { PageLayout, PLATFORM_LABEL } from '../../components/projects/ProjectBits';
import { Badge, Button, Card, Field, Input, Segmented, Select, Switch, Textarea, cx } from '../../components/ui/primitives';
import { ConfirmDialog } from '../../components/ui/Modal';
import { showToast } from '../../components/common/Toast';
import { formatBytes, framesToTimecode } from '../../utils/timecode';
import { ASPECT_RATIOS, DURATION_MAX, DURATION_MIN, validateAssets, validateDetails, type DetailErrors } from '../../utils/validation';

const STEPS = ['Project details', 'Media assets', 'Editing brief', 'Review'] as const;
const SAMPLE_BRIEF =
  'Cut a 40-second vertical reel from the interview. Open with the strongest line about habits, remove long pauses, cut to the supplied B-roll when objects are mentioned, keep the music bed under speech, and add clean captions inside the safe area.';

/** Deterministic demo asset set offered by "Choose sample assets". */
const SAMPLE_SET = [
  'asset_001_speaker_main',
  'asset_002_broll_morning_coffee',
  'asset_003_broll_phone_distraction',
  'asset_004_broll_desk_focus',
  'asset_005_music_ambient_pulse',
];

export const NewProjectPage: React.FC = () => {
  const navigate = useNavigate();
  const [params] = useSearchParams();
  const { allAssets, createProject } = useAppData();
  const { settings } = useSettings();

  const [step, setStep] = useState(0);
  const [maxReached, setMaxReached] = useState(0);
  const [name, setName] = useState('');
  const [description, setDescription] = useState('');
  const [platform, setPlatform] = useState<PlatformTarget>('instagram');
  const [aspectRatio, setAspectRatio] = useState<AspectRatio>(settings.defaultAspectRatio);
  const [duration, setDuration] = useState(String(settings.defaultDurationSeconds));
  const [fps, setFps] = useState<number>(settings.defaultFps);
  const [assetIds, setAssetIds] = useState<string[]>([]);
  const [pickedFiles, setPickedFiles] = useState<string[]>([]);
  const [brief, setBrief] = useState(SAMPLE_BRIEF);
  const [opts, setOpts] = useState<BriefOptions>(DEFAULT_BRIEF_OPTIONS);
  const [style, setStyle] = useState(params.get('style') ?? EDITING_STYLE_PRESETS[0].id);
  const [errors, setErrors] = useState<DetailErrors>({});
  const [assetError, setAssetError] = useState<string>();
  const [cancelOpen, setCancelOpen] = useState(false);
  const [creating, setCreating] = useState(false);
  const fileRef = useRef<HTMLInputElement>(null);
  const headingRef = useRef<HTMLHeadingElement>(null);

  const durationNum = Number(duration);
  const details = { name, aspectRatio, targetDurationSeconds: durationNum, fps };
  const dirty = name !== '' || description !== '' || assetIds.length > 0;
  const library = useMemo(() => [...allAssets].sort((a, b) => a.kind.localeCompare(b.kind) || a.name.localeCompare(b.name)), [allAssets]);
  const styleName = EDITING_STYLE_PRESETS.find((p) => p.id === style)?.name ?? style;

  const validateStep = (s: number): boolean => {
    if (s === 0) {
      const e = validateDetails(details);
      setErrors(e);
      return Object.keys(e).length === 0;
    }
    if (s === 1) {
      const e = validateAssets(assetIds, allAssets);
      setAssetError(e);
      return !e;
    }
    return true;
  };

  const goto = (s: number) => {
    setStep(s);
    setMaxReached((m) => Math.max(m, s));
    requestAnimationFrame(() => headingRef.current?.focus());
  };
  const next = () => {
    if (validateStep(step)) goto(step + 1);
  };

  const create = async () => {
    if (!validateStep(0)) return goto(0);
    if (!validateStep(1)) return goto(1);
    setCreating(true);
    const p = await createProject({
      name: name.trim(),
      description,
      platform,
      aspectRatio,
      targetDurationSeconds: durationNum,
      fps,
      brief,
      briefOptions: opts,
      stylePresetId: style,
      selectedAssetIds: assetIds,
    });
    showToast({ type: 'success', title: 'Demo project created', message: p.name });
    navigate(`/projects/${p.id}`);
  };

  const toggleAsset = (id: string) => {
    setAssetIds((ids) => (ids.includes(id) ? ids.filter((x) => x !== id) : [...ids, id]));
    setAssetError(undefined);
  };

  return (
    <PageLayout
      title="New Project"
      width="narrow"
      eyebrow={<Badge tone="warning">Creates a demo project</Badge>}
      description="Set up an editing task for footage you already own."
    >
      {/* Stepper */}
      <nav aria-label="Wizard progress" className="mb-5">
        <ol className="flex items-center gap-2">
          {STEPS.map((label, i) => {
            const reachable = i <= maxReached;
            return (
              <li key={label} className="flex-1 flex items-center gap-2 min-w-0">
                <button
                  type="button"
                  disabled={!reachable}
                  aria-current={i === step ? 'step' : undefined}
                  onClick={() => (i < step || validateStep(step) ? goto(i) : undefined)}
                  className={cx(
                    'flex items-center gap-2 min-w-0 text-left disabled:cursor-not-allowed',
                    i === step ? 'text-fg' : reachable ? 'text-fg-2 hover:text-fg' : 'text-fg-3'
                  )}
                >
                  <span
                    className={cx(
                      'w-6 h-6 rounded-full shrink-0 flex items-center justify-center text-xs font-semibold border',
                      i === step ? 'bg-accent text-on-accent border-accent' : i < maxReached || i < step ? 'bg-success/15 text-success border-success/40' : 'border-line'
                    )}
                  >
                    {i < step ? <Check className="w-3.5 h-3.5" aria-label="completed" /> : i + 1}
                  </span>
                  <span className="text-[13px] font-medium truncate hidden sm:inline">{label}</span>
                </button>
                {i < STEPS.length - 1 && <span aria-hidden className="flex-1 h-px bg-line" />}
              </li>
            );
          })}
        </ol>
      </nav>

      <Card className="p-6">
        <h2 ref={headingRef} tabIndex={-1} className="text-lg font-semibold text-fg mb-4 focus:outline-none">
          {STEPS[step]}
        </h2>

        {step === 0 && (
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
            <Field label="Project title *" htmlFor="np-name" error={errors.name} className="sm:col-span-2">
              <Input
                id="np-name"
                autoFocus
                placeholder="e.g. Habit Loop — Episode 4 Reel"
                value={name}
                aria-invalid={!!errors.name}
                aria-describedby={errors.name ? 'np-name-error' : undefined}
                onChange={(e) => setName(e.target.value)}
              />
            </Field>
            <Field label="Description" htmlFor="np-desc" className="sm:col-span-2">
              <Textarea id="np-desc" rows={2} value={description} onChange={(e) => setDescription(e.target.value)} />
            </Field>
            <Field label="Primary platform" htmlFor="np-platform">
              <Select id="np-platform" value={platform} onChange={(e) => setPlatform(e.target.value as PlatformTarget)}>
                {(Object.keys(PLATFORM_LABEL) as PlatformTarget[]).map((p) => (
                  <option key={p} value={p}>
                    {PLATFORM_LABEL[p]}
                  </option>
                ))}
              </Select>
            </Field>
            <div className="flex flex-col gap-1">
              <span className="text-xs font-medium text-fg-2" id="np-ar-label">
                Aspect ratio
              </span>
              <Segmented
                label="Aspect ratio"
                value={aspectRatio}
                onChange={setAspectRatio}
                options={ASPECT_RATIOS.map((r) => ({ value: r, label: r }))}
              />
              {errors.aspectRatio && <p className="text-xs text-danger">{errors.aspectRatio}</p>}
            </div>
            <Field
              label="Target duration (seconds)"
              htmlFor="np-dur"
              error={errors.targetDurationSeconds}
              hint={`${DURATION_MIN}–${DURATION_MAX} s · ${Number.isFinite(durationNum) ? Math.round(durationNum * fps) : '–'} frames`}
            >
              <Input
                id="np-dur"
                type="number"
                min={DURATION_MIN}
                max={DURATION_MAX}
                value={duration}
                aria-invalid={!!errors.targetDurationSeconds}
                aria-describedby={errors.targetDurationSeconds ? 'np-dur-error' : undefined}
                onChange={(e) => setDuration(e.target.value)}
                className="font-mono"
              />
            </Field>
            <Field label="Frame rate" htmlFor="np-fps" error={errors.fps}>
              <Select id="np-fps" value={fps} onChange={(e) => setFps(Number(e.target.value))}>
                <option value={24}>24 fps</option>
                <option value={30}>30 fps</option>
                <option value={60}>60 fps</option>
              </Select>
            </Field>
          </div>
        )}

        {step === 1 && (
          <div className="flex flex-col gap-4">
            <div
              className="rounded-xl border-2 border-dashed border-line p-6 text-center flex flex-col items-center gap-2"
              onDragOver={(e) => e.preventDefault()}
              onDrop={(e) => {
                e.preventDefault();
                setPickedFiles(Array.from(e.dataTransfer.files).map((f) => f.name));
              }}
            >
              <FolderInput aria-hidden className="w-6 h-6 text-fg-3" />
              <p className="text-[13px] text-fg">Drop files here, or</p>
              <div className="flex gap-2">
                <Button
                  variant="primary"
                  size="sm"
                  onClick={() => {
                    setAssetIds(SAMPLE_SET.filter((id) => allAssets.some((a) => a.id === id)));
                    setAssetError(undefined);
                  }}
                >
                  Choose sample assets
                </Button>
                <Button size="sm" onClick={() => fileRef.current?.click()}>
                  Pick files (names only)
                </Button>
                <input
                  ref={fileRef}
                  type="file"
                  multiple
                  accept="video/*,audio/*,.srt"
                  className="sr-only"
                  aria-hidden
                  tabIndex={-1}
                  onChange={(e) => setPickedFiles(Array.from(e.target.files ?? []).map((f) => f.name))}
                />
              </div>
              <p className="text-xs text-fg-3 max-w-md flex items-start gap-1.5">
                <Info aria-hidden className="w-3.5 h-3.5 mt-px shrink-0" />
                Picked files are listed by name only — they are not read, copied, imported or processed in the UI prototype.
              </p>
            </div>

            {pickedFiles.length > 0 && (
              <div className="rounded-lg border border-line p-3">
                <p className="text-xs font-medium text-fg-2 mb-1.5">Files you picked (not imported)</p>
                <ul className="flex flex-wrap gap-1.5">
                  {pickedFiles.map((n) => (
                    <li key={n}>
                      <Badge>{n}</Badge>
                    </li>
                  ))}
                </ul>
              </div>
            )}

            <fieldset>
              <legend className="text-xs font-medium text-fg-2 mb-2">Demo asset library ({assetIds.length} selected)</legend>
              <ul className="flex flex-col gap-1.5 max-h-80 overflow-y-auto pr-1">
                {library.map((a) => {
                  const on = assetIds.includes(a.id);
                  return (
                    <li key={a.id}>
                      <label
                        className={cx(
                          'flex items-center gap-3 p-2 rounded-lg border cursor-pointer',
                          on ? 'border-accent bg-accent/10' : 'border-line bg-surface hover:border-fg-3'
                        )}
                      >
                        <input type="checkbox" checked={on} onChange={() => toggleAsset(a.id)} className="accent-accent w-4 h-4" />
                        <span aria-hidden className="w-8 h-8 rounded-md flex items-center justify-center text-[#08121A] shrink-0" style={{ background: a.thumbnailColor }}>
                          {KIND_ICON[a.kind]}
                        </span>
                        <span className="min-w-0 flex-1">
                          <span className="block text-[13px] text-fg truncate">{a.name}</span>
                          <span className="block text-[11px] font-mono text-fg-3 truncate">
                            {a.filename} · {a.durationFrames ? framesToTimecode(a.durationFrames, 30) : '—'} · {formatBytes(a.fileSizeBytes)}
                          </span>
                        </span>
                        <Badge>{a.kind}</Badge>
                      </label>
                    </li>
                  );
                })}
              </ul>
            </fieldset>
            {assetError && (
              <p role="alert" className="text-xs text-danger">
                {assetError}
              </p>
            )}
          </div>
        )}

        {step === 2 && (
          <div className="flex flex-col gap-4">
            <Field label="Editing brief" htmlFor="np-brief" hint="Describe the cut you want. This text is sent to the agent once MCP is available.">
              <Textarea id="np-brief" rows={6} value={brief} onChange={(e) => setBrief(e.target.value)} />
            </Field>
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-x-6">
              {BRIEF_OPTION_LABELS.map((o) => (
                <Switch key={o.key} label={o.label} description={o.description} checked={opts[o.key]} onChange={(v) => setOpts((p) => ({ ...p, [o.key]: v }))} />
              ))}
            </div>
            <fieldset>
              <legend className="text-xs font-medium text-fg-2 mb-2">Style preset</legend>
              <div className="grid grid-cols-1 sm:grid-cols-2 gap-2" role="radiogroup" aria-label="Style preset">
                {EDITING_STYLE_PRESETS.map((p) => (
                  <button
                    key={p.id}
                    type="button"
                    role="radio"
                    aria-checked={style === p.id}
                    onClick={() => setStyle(p.id)}
                    className={cx('text-left p-3 rounded-lg border', style === p.id ? 'border-accent bg-accent/10' : 'border-line bg-surface hover:border-fg-3')}
                  >
                    <span className="block text-[13px] font-semibold text-fg">{p.name}</span>
                    <span className="block text-xs text-fg-2 mt-0.5 line-clamp-2">{p.headline}</span>
                  </button>
                ))}
              </div>
            </fieldset>
          </div>
        )}

        {step === 3 && (
          <dl className="grid grid-cols-[max-content_1fr] gap-x-6 gap-y-2 text-[13px]" data-testid="wizard-summary">
            <dt className="text-fg-3">Title</dt>
            <dd className="text-fg font-medium">{name}</dd>
            {description && (
              <>
                <dt className="text-fg-3">Description</dt>
                <dd className="text-fg-2">{description}</dd>
              </>
            )}
            <dt className="text-fg-3">Format</dt>
            <dd className="text-fg">
              {PLATFORM_LABEL[platform]} · {aspectRatio} · {durationNum} s · {fps} fps
            </dd>
            <dt className="text-fg-3">Assets</dt>
            <dd className="text-fg">
              {assetIds.length} demo assets ({allAssets.filter((a) => assetIds.includes(a.id) && a.kind === 'video').length} video)
            </dd>
            <dt className="text-fg-3">Style</dt>
            <dd className="text-fg">{styleName}</dd>
            <dt className="text-fg-3">Priorities</dt>
            <dd className="text-fg">{BRIEF_OPTION_LABELS.filter((o) => opts[o.key]).map((o) => o.label).join(', ') || 'None'}</dd>
            <dt className="text-fg-3">Brief</dt>
            <dd className="text-fg-2 leading-relaxed">{brief}</dd>
          </dl>
        )}

        <div className="mt-6 pt-4 border-t border-line flex items-center justify-between gap-2">
          <Button onClick={() => (dirty ? setCancelOpen(true) : navigate(-1))}>Cancel</Button>
          <div className="flex gap-2">
            {step > 0 && (
              <Button icon={<ArrowLeft className="w-3.5 h-3.5" />} onClick={() => goto(step - 1)}>
                Back
              </Button>
            )}
            {step < STEPS.length - 1 ? (
              <Button variant="primary" onClick={next}>
                Next <ArrowRight className="w-3.5 h-3.5" />
              </Button>
            ) : (
              <Button variant="primary" icon={<Sparkles className="w-3.5 h-3.5" />} disabled={creating} onClick={create}>
                Create Demo Project
              </Button>
            )}
          </div>
        </div>
      </Card>

      <ConfirmDialog
        open={cancelOpen}
        onOpenChange={setCancelOpen}
        title="Discard this project?"
        message="Your entries in the wizard will be lost."
        confirmLabel="Discard"
        cancelLabel="Keep editing"
        destructive
        onConfirm={() => navigate('/projects')}
      />
    </PageLayout>
  );
};
