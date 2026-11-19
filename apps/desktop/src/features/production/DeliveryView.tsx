/** Delivery view (spec §15): package an enumerated set of approved clips; show verified manifests. */

import React, { useState } from 'react';
import { PackageCheck } from 'lucide-react';
import { Badge, Button } from '../../components/ui/primitives';
import { card, type ViewProps } from './ProductionWorkspace';
import { DELIVERABLE, bytes, clock } from './format';

export const DeliveryView: React.FC<ViewProps> = ({ detail, repo, run, busy, person }) => {
  const [chosen, setChosen] = useState<string[]>([]);
  if (!detail) return <p className="text-sm text-fg-3">Choose a batch to package its approved clips.</p>;
  const approved = detail.clips.filter((clip) => DELIVERABLE.includes(clip.status));
  const picked = chosen.filter((id) => approved.some((clip) => clip.id === id));
  const who = person.trim();
  const toggle = (id: string) => setChosen((all) => (all.includes(id) ? all.filter((x) => x !== id) : [...all, id]));
  const partial = picked.length > 0 && picked.length < detail.clips.filter((c) => c.status !== 'cancelled').length;

  return (
    <div className="space-y-6">
      <section aria-labelledby="deliver-title" className={`${card} space-y-3 p-4`}>
        <h2 id="deliver-title" className="font-semibold">Package approved clips</h2>
        <p className="text-xs text-fg-3">Only clips a person approved can be packaged. Files are copied into the project's deliveries folder, hash-verified, and nothing is uploaded.</p>
        {approved.length === 0 ? <p className="text-sm text-fg-3">No approved clips yet.</p> : (
          <>
            <div className="flex gap-2 text-xs">
              <button type="button" className="text-accent hover:underline" onClick={() => setChosen(approved.map((c) => c.id))}>Select all</button>
              <button type="button" className="text-fg-3 hover:underline" onClick={() => setChosen([])}>Clear</button>
            </div>
            <ul className="space-y-1.5">
              {approved.map((clip) => (
                <li key={clip.id}>
                  <label className="flex items-center gap-2 text-sm">
                    <input type="checkbox" checked={picked.includes(clip.id)} onChange={() => toggle(clip.id)} aria-label={`Deliver ${clip.title}`} />
                    <span className="font-medium">{clip.title}</span>
                    <Badge>revision {clip.draft_revision ?? clip.current_revision ?? '—'}</Badge>
                  </label>
                </li>
              ))}
            </ul>
            {partial && detail.batch.policy.allow_partial_delivery === false && (
              <p className="text-xs text-warning">This batch's policy does not allow partial delivery; the engine will refuse a subset.</p>
            )}
            {!who && <p className="text-xs text-warning">Type your name in “Acting as” to authorize the package.</p>}
            <Button
              variant="primary"
              icon={<PackageCheck className="h-4 w-4" />}
              disabled={busy || !who || picked.length === 0}
              onClick={() => void run('Package delivery', () => repo.packageDelivery(detail.batch.id, picked, who)).then((ok) => ok && setChosen([]))}
            >
              Package {picked.length || ''} {picked.length === 1 ? 'clip' : 'clips'}{partial ? ' (partial)' : ''}
            </Button>
          </>
        )}
      </section>

      <section aria-labelledby="history-title" className="space-y-2">
        <h2 id="history-title" className="text-sm font-semibold text-fg-2">Delivery history</h2>
        {detail.deliveries.length === 0 ? <p className="text-sm text-fg-3">Nothing delivered from this batch yet.</p> : (
          <ul className="space-y-2">
            {[...detail.deliveries].reverse().map((manifest) => (
              <li key={manifest.id} className={`${card} space-y-1.5 p-3 text-sm`}>
                <p className="flex flex-wrap items-center gap-2">
                  <span className="font-medium">{manifest.package_name}</span>
                  {manifest.partial && <Badge tone="warning">Partial</Badge>}
                  <span className="text-xs text-fg-3">by {manifest.authorized_by} · {clock(manifest.created_at)}</span>
                </p>
                <p className="text-fg-2">{manifest.clips.map((c) => c.title).join(' · ')}</p>
                {manifest.folder && <p className="break-all text-xs text-fg-3">{manifest.folder}</p>}
                {manifest.files.length > 0 && (
                  <details className="text-xs text-fg-3">
                    <summary className="cursor-pointer">{manifest.files.length} verified files</summary>
                    <ul className="mt-1 space-y-0.5 font-mono">
                      {manifest.files.map((file) => <li key={file.path} className="break-all">{file.path} · {bytes(file.size_bytes)} · {file.sha256.slice(0, 16)}…</li>)}
                    </ul>
                  </details>
                )}
                {manifest.notice && <p className="text-xs text-fg-3">{manifest.notice}</p>}
              </li>
            ))}
          </ul>
        )}
      </section>
    </div>
  );
};
