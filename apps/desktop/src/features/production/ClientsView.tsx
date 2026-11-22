/** Clients view (spec §10): clients, their versioned instruction profiles, and project bindings.
 *
 * Each save appends an immutable profile revision; older batches keep citing the revision they
 * were created with. A save made against an outdated revision is refused, never merged silently. */

import React, { useEffect, useMemo, useState } from 'react';
import { Globe, Lock, Plus, RefreshCw, Save, Users } from 'lucide-react';
import type {
  ReferenceAsset,
  Client, CreativeDefaults, CreativeField, InstructionProfile, ProfileScope,
} from '../../services/productionRepository';
import { Badge, Button, Switch } from '../../components/ui/primitives';
import { engineErrorText } from '../../utils/engineError';
import { card, field, type ViewProps } from './ProductionWorkspace';

type Option<T> = { value: T; label: string };
const CREATIVE: { key: CreativeField; label: string; options?: Option<string>[]; kind: 'choice' | 'toggle' | 'text'; max?: number }[] = [
  { key: 'caption_preset', label: 'Caption style', kind: 'choice', options: [
    { value: 'clean_white', label: 'Clean white' }, { value: 'bold_yellow', label: 'Bold yellow' }, { value: 'boxed', label: 'Boxed' },
    { value: 'top_clean', label: 'Top, clean' }, { value: 'big_center', label: 'Bold, centred' }, { value: 'lower_minimal', label: 'Small, lower third' }] },
  { key: 'color_preset', label: 'Color', kind: 'choice', options: [
    { value: 'none', label: 'Untouched' }, { value: 'natural', label: 'Natural' }, { value: 'warm', label: 'Warm' }, { value: 'cool', label: 'Cool' }] },
  { key: 'pacing', label: 'Pacing', kind: 'choice', options: [
    { value: 'calm', label: 'Calm' }, { value: 'balanced', label: 'Balanced' }, { value: 'fast', label: 'Fast' }] },
  { key: 'captions', label: 'Captions', kind: 'toggle' },
  { key: 'normalize_audio', label: 'Normalize audio', kind: 'toggle' },
  { key: 'tone', label: 'Tone', kind: 'text', max: 500 },
  { key: 'platform', label: 'Platform', kind: 'text', max: 60 },
];
const STEM = /^[A-Za-z0-9][A-Za-z0-9_-]{0,59}$/;
type Selection = { scope: 'global' } | { scope: 'client'; clientId: string };

/** Problems that would make the engine refuse the profile; checked before sending. */
export function profileProblems(profile: InstructionProfile): string[] {
  const problems: string[] = [];
  if (profile.instructions.length > 20000) problems.push('Instructions are limited to 20,000 characters.');
  if (profile.required_constraints.length > 50) problems.push('Up to 50 required constraints.');
  if (profile.required_constraints.some((c) => c.length > 300)) problems.push('Each constraint is at most 300 characters.');
  if (profile.style_observations.length > 50) problems.push('Up to 50 style notes.');
  if (profile.style_observations.some((o) => !o.label.trim() || o.label.length > 300 || o.value.length > 300)) {
    problems.push('Each style note needs a name; name and value are at most 300 characters.');
  }
  const prefix = profile.delivery.naming_prefix;
  if (prefix !== null && !STEM.test(prefix)) problems.push('File-name prefix: letters, digits, “_” and “-” only, starting with a letter or digit (max 60).');
  return problems;
}

export const ClientsView: React.FC<ViewProps> = ({ repo, run, busy, projects, overview }) => {
  const [clients, setClients] = useState<Client[]>([]);
  const [bindings, setBindings] = useState<Record<string, string>>({});
  const [selection, setSelection] = useState<Selection>({ scope: 'global' });
  const [newClient, setNewClient] = useState('');
  const [version, setVersion] = useState(0);
  const [loadError, setLoadError] = useState<string | null>(null);
  const reload = () => setVersion((v) => v + 1);

  useEffect(() => {
    let live = true;
    Promise.all([repo.clients(), repo.projectClients()])
      .then(([list, bound]) => { if (live) { setClients(list); setBindings(bound); setLoadError(null); } })
      .catch((err) => { if (live) setLoadError(engineErrorText(err)); });
    return () => { live = false; };
  }, [repo, version]);

  const withBatches = new Set(overview.batches.map((b) => b.batch.project_id));
  const selectedClient = selection.scope === 'client' ? clients.find((c) => c.id === selection.clientId) : undefined;

  return (
    <div className="grid gap-5 lg:grid-cols-[16rem_minmax(0,1fr)]">
      <aside aria-label="Instruction layers" className="space-y-3">
        {loadError && <p role="alert" className="text-sm text-danger">{loadError}</p>}
        <ul className="space-y-1.5">
          <li>
            <LayerButton active={selection.scope === 'global'} onClick={() => setSelection({ scope: 'global' })} icon={<Globe className="h-4 w-4" />}>
              Global defaults
            </LayerButton>
          </li>
          {clients.map((client) => (
            <li key={client.id}>
              <LayerButton
                active={selection.scope === 'client' && selection.clientId === client.id}
                onClick={() => setSelection({ scope: 'client', clientId: client.id })}
                icon={<Users className="h-4 w-4" />}
              >
                {client.name}
              </LayerButton>
            </li>
          ))}
        </ul>
        <form
          className="flex gap-2"
          onSubmit={(e) => {
            e.preventDefault();
            const name = newClient.trim();
            if (!name) return;
            let created = null as Client | null;
            void run('Add client', async () => { created = await repo.createClient(name); }).then((ok) => {
              if (ok && created) {
                setNewClient('');
                setSelection({ scope: 'client', clientId: created.id });
                reload();
              }
            });
          }}
        >
          <input aria-label="New client name" className={`${field} h-8 py-0`} placeholder="New client" value={newClient} maxLength={80} onChange={(e) => setNewClient(e.target.value)} />
          <Button type="submit" size="md" icon={<Plus className="h-4 w-4" />} disabled={busy || !newClient.trim()}>Add</Button>
        </form>
      </aside>

      <div className="min-w-0 space-y-6">
        <ProfileEditor
          key={selection.scope === 'global' ? 'global' : selection.clientId}
          scope={selection.scope}
          clientId={selection.scope === 'client' ? selection.clientId : null}
          title={selectedClient ? `${selectedClient.name} profile` : 'Global defaults'}
          // Client isolation: a client's notes come only from its own projects; global ones only from unbound projects.
          referenceProjects={projects.filter((p) => (selection.scope === 'client' ? bindings[p.id] === selection.clientId : !bindings[p.id]))}
          repo={repo}
          busy={busy}
          run={run}
        />

        <section aria-labelledby="bindings-title" className={`${card} space-y-2 p-4`}>
          <h2 id="bindings-title" className="text-sm font-semibold">Projects and clients</h2>
          <p className="text-xs text-fg-3">A project's batches use its client's profile. Once a project has batches it stays with that client, so no client's instructions or footage leak into another's work.</p>
          {projects.length === 0 ? <p className="text-sm text-fg-3">No projects yet.</p> : (
            <ul className="divide-y divide-line">
              {projects.map((project) => {
                const bound = bindings[project.id] ?? '';
                const locked = Boolean(bound) && withBatches.has(project.id);
                return (
                  <li key={project.id} className="flex flex-wrap items-center gap-3 py-2 text-sm">
                    <span className="min-w-0 flex-1 truncate font-medium">{project.name}</span>
                    {locked && <Badge icon={<Lock className="h-3 w-3" />}>Fixed by batches</Badge>}
                    <select
                      aria-label={`Client for ${project.name}`}
                      className={`${field} h-8 w-56 py-0`}
                      value={bound}
                      disabled={busy || locked || clients.length === 0}
                      onChange={(e) => {
                        const clientId = e.target.value;
                        if (clientId) void run('Assign client', () => repo.bindProject(project.id, clientId)).then(reload);
                      }}
                    >
                      <option value="" disabled>{clients.length ? 'No client' : 'Add a client first'}</option>
                      {clients.map((client) => <option key={client.id} value={client.id}>{client.name}</option>)}
                    </select>
                  </li>
                );
              })}
            </ul>
          )}
        </section>
      </div>
    </div>
  );
};

const LayerButton: React.FC<{ active: boolean; onClick: () => void; icon: React.ReactNode; children: React.ReactNode }> = ({ active, onClick, icon, children }) => (
  <button
    type="button"
    aria-current={active ? 'true' : undefined}
    onClick={onClick}
    className={`${card} flex w-full items-center gap-2 p-2.5 text-left text-sm hover:border-accent ${active ? 'border-accent' : ''}`}
  >
    <span aria-hidden className="text-fg-3">{icon}</span>
    <span className="min-w-0 flex-1 truncate font-medium">{children}</span>
  </button>
);

// ------------------------------------------------------------------ profile editor

const ProfileEditor: React.FC<{
  scope: ProfileScope;
  clientId: string | null;
  title: string;
  referenceProjects: { id: string; name: string }[];
  repo: ViewProps['repo'];
  busy: boolean;
  run: ViewProps['run'];
}> = ({ scope, clientId, title, referenceProjects, repo, busy, run }) => {
  const [loaded, setLoaded] = useState<InstructionProfile | null>(null);
  const [draft, setDraft] = useState<InstructionProfile | null>(null);
  const [constraints, setConstraints] = useState('');
  const [loadError, setLoadError] = useState<string | null>(null);
  const [version, setVersion] = useState(0);

  useEffect(() => {
    let live = true;
    repo.getProfile(scope, clientId)
      .then((profile) => {
        if (!live) return;
        setLoaded(profile);
        setDraft(profile);
        setConstraints(profile.required_constraints.join('\n'));
        setLoadError(null);
      })
      .catch((err) => { if (live) setLoadError(engineErrorText(err)); });
    return () => { live = false; };
  }, [repo, scope, clientId, version]);

  const candidate = useMemo(() => draft && {
    ...draft,
    required_constraints: constraints.split('\n').map((line) => line.trim()).filter(Boolean),
  }, [draft, constraints]);
  const dirty = Boolean(loaded && candidate && JSON.stringify(loaded) !== JSON.stringify(candidate));
  const problems = candidate ? profileProblems(candidate) : [];

  if (loadError) return <p role="alert" className="text-sm text-danger">{loadError}</p>;
  if (!draft || !loaded || !candidate) return <p className="text-sm text-fg-3">Loading profile…</p>;

  const setCreative = (key: CreativeField, value: CreativeDefaults[CreativeField]) =>
    setDraft({ ...draft, creative: { ...draft.creative, [key]: value } });
  const toggleLock = (key: CreativeField) => setDraft({
    ...draft,
    locked_creative: draft.locked_creative.includes(key) ? draft.locked_creative.filter((k) => k !== key) : [...draft.locked_creative, key],
  });

  return (
    <form
      aria-label={title}
      className={`${card} space-y-4 p-4`}
      onSubmit={(e) => {
        e.preventDefault();
        if (problems.length || !dirty) return;
        void run('Save profile', () => repo.saveProfile(candidate, loaded.revision)).then((ok) => ok && setVersion((v) => v + 1));
      }}
    >
      <div className="flex flex-wrap items-center gap-2">
        <h2 className="text-lg font-semibold">{title}</h2>
        <Badge>{loaded.revision === 0 ? 'not saved yet' : `revision ${loaded.revision}`}</Badge>
        {dirty && <Badge tone="warning">Unsaved changes</Badge>}
        <span className="ml-auto" />
        <Button variant="ghost" icon={<RefreshCw className="h-4 w-4" />} onClick={() => setVersion((v) => v + 1)}>Reload</Button>
      </div>
      <p className="text-xs text-fg-3">
        {scope === 'global'
          ? 'Defaults for every client. Client profiles, projects, batches and clips can refine them.'
          : 'Applies to every project bound to this client. Locked settings cannot be changed by a project, batch or clip; a different value there goes to a person as a conflict.'}
      </p>

      <label className="block space-y-1 text-sm">Instructions
        <textarea className={`${field} min-h-28`} value={draft.instructions} maxLength={20000} onChange={(e) => setDraft({ ...draft, instructions: e.target.value })} placeholder="Brand voice, audience, what to avoid…" />
      </label>

      <fieldset className="space-y-2">
        <legend className="text-sm font-semibold">Creative defaults</legend>
        <p className="text-xs text-fg-3">“Inherit” keeps the value from the layer above.</p>
        <div className="grid gap-3 md:grid-cols-2">
          {CREATIVE.map((item) => {
            const value = draft.creative[item.key];
            return (
              <div key={item.key} className="space-y-1 text-sm">
                <div className="flex items-center justify-between gap-2">
                  <label htmlFor={`creative-${item.key}`}>{item.label}</label>
                  {scope === 'client' && (
                    <label className="flex items-center gap-1 text-xs text-fg-3">
                      <input type="checkbox" checked={draft.locked_creative.includes(item.key)} onChange={() => toggleLock(item.key)} aria-label={`Lock ${item.label}`} />
                      Lock
                    </label>
                  )}
                </div>
                {item.kind === 'text' ? (
                  <input
                    id={`creative-${item.key}`}
                    className={field}
                    value={(value as string | null) ?? ''}
                    maxLength={item.max}
                    placeholder="Inherit"
                    onChange={(e) => setCreative(item.key, e.target.value === '' ? null : e.target.value)}
                  />
                ) : (
                  <select
                    id={`creative-${item.key}`}
                    className={field}
                    value={value === null ? '' : String(value)}
                    onChange={(e) => {
                      const raw = e.target.value;
                      setCreative(item.key, raw === '' ? null : item.kind === 'toggle' ? raw === 'true' : raw as CreativeDefaults[CreativeField]);
                    }}
                  >
                    <option value="">Inherit</option>
                    {(item.kind === 'toggle' ? [{ value: 'true', label: 'On' }, { value: 'false', label: 'Off' }] : item.options!).map((o) => (
                      <option key={o.value} value={o.value}>{o.label}</option>
                    ))}
                  </select>
                )}
              </div>
            );
          })}
        </div>
      </fieldset>

      {scope === 'client' && (
        <label className="block space-y-1 text-sm">Required constraints <span className="text-xs text-fg-3">(one per line; lower layers can never override these)</span>
          <textarea className={`${field} min-h-20`} value={constraints} onChange={(e) => setConstraints(e.target.value)} placeholder={'Never show competitor logos\nKeep the disclaimer on screen'} />
        </label>
      )}

      <fieldset className="space-y-1">
        <legend className="text-sm font-semibold">Delivery</legend>
        <Switch checked={draft.delivery.allow_partial_delivery} onChange={(v) => setDraft({ ...draft, delivery: { ...draft.delivery, allow_partial_delivery: v } })} label="Allow partial delivery" description="Package some approved clips before the whole batch is done." />
        <Switch checked={draft.delivery.include_captions} onChange={(v) => setDraft({ ...draft, delivery: { ...draft.delivery, include_captions: v } })} label="Include caption files" />
        <Switch checked={draft.delivery.include_project_snapshot} onChange={(v) => setDraft({ ...draft, delivery: { ...draft.delivery, include_project_snapshot: v } })} label="Include project snapshot" />
        <label className="block space-y-1 pt-1 text-sm">File-name prefix
          <input className={`${field} md:w-72`} value={draft.delivery.naming_prefix ?? ''} maxLength={60} placeholder="None" onChange={(e) => setDraft({ ...draft, delivery: { ...draft.delivery, naming_prefix: e.target.value === '' ? null : e.target.value } })} />
        </label>
      </fieldset>

      <StyleNotes
        notes={draft.style_observations}
        onChange={(style_observations) => setDraft({ ...draft, style_observations })}
        projects={referenceProjects}
        repo={repo}
      />

      {problems.map((p) => <p key={p} className="text-xs text-warning">{p}</p>)}
      <Button type="submit" variant="primary" icon={<Save className="h-4 w-4" />} disabled={busy || !dirty || problems.length > 0}>
        Save as revision {loaded.revision + 1}
      </Button>
    </form>
  );
};

// ------------------------------------------------------------------ style notes

type StyleNote = InstructionProfile['style_observations'][number];
const BASIS: Record<StyleNote['basis'], { label: string; tone: 'success' | 'warning' | 'neutral' }> = {
  measured: { label: 'Measured', tone: 'success' },
  estimate: { label: 'Estimate', tone: 'warning' },
  person: { label: 'From a person', tone: 'neutral' },
};

const newNoteId = () => `style_${Math.random().toString(36).slice(2, 12)}${Date.now().toString(36)}`.slice(0, 64);

/** Reference style notes. People add their own; measured or estimated notes come from analysis and keep their label. */
const StyleNotes: React.FC<{
  notes: StyleNote[];
  onChange: (notes: StyleNote[]) => void;
  projects: { id: string; name: string }[];
  repo: ViewProps['repo'];
}> = ({ notes, onChange, projects, repo }) => {
  const [label, setLabel] = useState('');
  const [value, setValue] = useState('');
  const [projectId, setProjectId] = useState(projects[0]?.id ?? '');
  const [references, setReferences] = useState<ReferenceAsset[]>([]);
  const [assetId, setAssetId] = useState('');
  const [measuring, setMeasuring] = useState(false);
  const [message, setMessage] = useState<{ tone: 'error' | 'ok'; text: string } | null>(null);

  // Bindings load after first render; keep the choice within the eligible projects.
  useEffect(() => {
    if (!projects.some((p) => p.id === projectId)) setProjectId(projects[0]?.id ?? '');
  }, [projects, projectId]);

  useEffect(() => {
    if (!projectId) { setReferences([]); return; }
    let live = true;
    repo.referenceAssets(projectId)
      .then((list) => { if (live) { setReferences(list); setAssetId(list[0]?.id ?? ''); } })
      .catch((err) => { if (live) setMessage({ tone: 'error', text: engineErrorText(err) }); });
    return () => { live = false; };
  }, [repo, projectId]);

  const measure = async () => {
    setMeasuring(true);
    setMessage(null);
    try {
      const measured = await repo.measureReference(projectId, assetId);
      // A re-measurement replaces the earlier notes from the same file.
      const ids = new Set(measured.map((n) => n.id));
      onChange([...notes.filter((n) => !ids.has(n.id)), ...measured].slice(0, 50));
      setMessage({ tone: 'ok', text: `Added ${measured.length} measured notes. Review them, then save the profile.` });
    } catch (err) {
      setMessage({ tone: 'error', text: engineErrorText(err) });
    } finally {
      setMeasuring(false);
    }
  };
  const add = () => {
    if (!label.trim()) return;
    onChange([...notes, { id: newNoteId(), label: label.trim(), value: value.trim(), basis: 'person', source_asset_id: null }]);
    setLabel('');
    setValue('');
  };
  return (
    <fieldset className="space-y-2">
      <legend className="text-sm font-semibold">Style notes</legend>
      <p className="text-xs text-fg-3">What to keep consistent, such as caption position or cut rhythm. Measured notes came from analysing a reference file; yours are labelled as from a person.</p>
      {notes.length > 0 && (
        <ul className="space-y-1 text-sm">
          {notes.map((note) => (
            <li key={note.id} className="flex flex-wrap items-center gap-2">
              <span className="font-medium">{note.label}</span>
              {note.value && <span className="text-fg-2">{note.value}</span>}
              <Badge tone={BASIS[note.basis].tone}>{BASIS[note.basis].label}</Badge>
              <button type="button" className="text-xs text-fg-3 hover:text-danger" aria-label={`Remove style note ${note.label}`} onClick={() => onChange(notes.filter((n) => n.id !== note.id))}>
                Remove
              </button>
            </li>
          ))}
        </ul>
      )}
      <div className="flex flex-wrap gap-2">
        <input aria-label="Style note name" className={`${field} md:w-56`} placeholder="Caption position" value={label} maxLength={300} onChange={(e) => setLabel(e.target.value)} />
        <input aria-label="Style note value" className={`${field} md:w-72`} placeholder="Lower third, centred" value={value} maxLength={300} onChange={(e) => setValue(e.target.value)} />
        <Button onClick={add} disabled={!label.trim() || notes.length >= 50} icon={<Plus className="h-4 w-4" />}>Add note</Button>
      </div>
      <div className="space-y-1.5 rounded-lg border border-line bg-app p-3">
        <p className="text-sm font-medium">Measure a reference file</p>
        <p className="text-xs text-fg-3">Reads frame size, frame rate, cuts and shot lengths, and loudness from a reference in {projects.length ? 'this layer’s projects' : 'a project'} (the first 5 minutes of long files). Nothing is guessed.</p>
        {projects.length === 0 ? (
          <p className="text-xs text-fg-3">No eligible project. Client notes come only from that client's projects; global notes only from projects without a client.</p>
        ) : (
          <div className="flex flex-wrap items-center gap-2">
            <select aria-label="Reference project" className={`${field} h-8 py-0 md:w-56`} value={projectId} onChange={(e) => setProjectId(e.target.value)}>
              {projects.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
            </select>
            <select aria-label="Reference file" className={`${field} h-8 py-0 md:w-64`} value={assetId} onChange={(e) => setAssetId(e.target.value)} disabled={references.length === 0}>
              {references.length === 0 && <option value="">No reference files in this project</option>}
              {references.map((r) => <option key={r.id} value={r.id}>{r.name}</option>)}
            </select>
            <Button onClick={() => void measure()} disabled={measuring || !assetId}>{measuring ? 'Measuring…' : 'Measure'}</Button>
          </div>
        )}
        {message && <p role={message.tone === 'error' ? 'alert' : 'status'} className={`text-xs ${message.tone === 'error' ? 'text-danger' : 'text-success'}`}>{message.text}</p>}
      </div>
    </fieldset>
  );
};
