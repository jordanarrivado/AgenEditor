import React, { useMemo, useState } from 'react';
import { GENERATOR_LABEL, buildGeneratorPrompt, generatorsFor, parseAssetRequests } from '../../utils/assetRequests';
import { DROPPED, checklistReady, requestStatus, type AssetChecklist } from '../../utils/assetChecklist';
import { copyText } from '../../utils/agentBrief';
import type { EngineAsset } from '../../services/liveEngineRepository';

const button = 'rounded-lg border border-line px-2.5 py-1.5 text-xs font-medium text-fg hover:bg-elevated disabled:opacity-40 disabled:cursor-not-allowed';

/**
 * Prepare-assets checklist for the live workspace. Prompts are copied to external
 * generators; a request becomes ready only when its imported file is registered.
 */
export const NativeAssetChecklist: React.FC<{
  list: AssetChecklist | null;
  assets: EngineAsset[];
  busy: boolean;
  onChange: (list: AssetChecklist | null) => void;
  onImport: (requestId: string, toolName: string) => void;
  onLink: (requestId: string, assetId: string) => void;
  onAccept: (requestId: string, note: string | null) => void;
}> = ({ list, assets, busy, onChange, onImport, onLink, onAccept }) => {
  const [pasted, setPasted] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [copied, setCopied] = useState<string | null>(null);
  const [selectedLink, setSelectedLink] = useState<Record<string, string>>({});
  const [issueNote, setIssueNote] = useState<Record<string, string>>({});
  const registered = useMemo(() => new Set(assets.map((a) => a.id)), [assets]);

  const load = () => {
    const res = parseAssetRequests(pasted);
    if (!res.ok) { setError(res.error); return; }
    setError(null);
    setPasted('');
    onChange({ requests: res.requests, links: {} });
  };
  const setLink = (id: string, value: string | null) => {
    if (!list) return;
    const links = { ...list.links };
    if (value === null) delete links[id]; else links[id] = value;
    // The known-issues log must follow the link: any change to it clears the acceptance.
    const { [id]: _removed, ...accepted } = list.accepted ?? {};
    onChange({ ...list, links, accepted });
  };
  const copy = async (text: string, id: string) => setCopied(await copyText(text) ? id : 'failed');

  return (
    <div data-testid="native-asset-checklist" className="space-y-2 border-t border-line pt-3">
      <h3 className="text-sm font-semibold">Prepare assets</h3>
      {!list ? (
        <>
          <label htmlFor="native-asset-requests" className="text-xs text-fg-2">Paste the planner's reply containing asset_requests</label>
          <textarea id="native-asset-requests" rows={3} value={pasted} onChange={(e) => setPasted(e.target.value)}
            className="w-full rounded-lg border border-line bg-app px-2 py-1.5 font-mono text-[11px] text-fg-2" />
          {error && <p role="alert" className="text-xs text-danger">{error}</p>}
          <div className="flex gap-2">
            <button className={button} disabled={busy || !pasted.trim()} onClick={load}>Load requests</button>
            <button className={button} disabled={busy} onClick={() => onChange({ requests: [], links: {} })}>No assets needed</button>
          </div>
        </>
      ) : (
        <>
          {list.requests.length === 0 && <p className="text-xs text-fg-3">No generated assets needed for this plan.</p>}
          <ul className="space-y-1.5" aria-label="Asset requests">
            {list.requests.map((r) => {
              const status = requestStatus(list, r.id, registered);
              const asset = assets.find((a) => a.id === list.links[r.id]);
              const available = assets.filter((candidate) =>
                candidate.kind === r.kind
                && (r.kind !== 'video' || candidate.role === 'broll')
                && candidate.provenance?.origin === 'generated-external'
                && !Object.values(list.links).includes(candidate.id));
              return (
                <li key={r.id} data-testid={`native-request-${r.id}`} data-request-id={r.id} data-request-status={status} className="rounded-lg border border-line p-2 space-y-1.5">
                  {status === 'pending' && <p className="text-xs text-fg-3">Drop the file here to import it for this request.</p>}
                  <div className="text-xs"><span className="font-mono">{r.id}</span> · {r.kind} · <span data-testid={`native-status-${r.id}`}>{status}</span>{r.purpose && <span className="text-fg-2"> · {r.purpose}</span>}</div>
                  {asset && <div className="text-xs text-warning">Imported: {asset.original_filename} · AI-generated (external){asset.provenance?.tool_name ? ` · ${asset.provenance.tool_name}` : ''}</div>}
                  {list.accepted?.[r.id] && <div data-testid={`native-issue-${r.id}`} className="text-xs text-warning">Accepted with a known issue: {list.accepted[r.id].note}</div>}
                  <div className="flex flex-wrap gap-1.5">
                    {generatorsFor(r.kind).map((g) => (
                      <button key={g} className={button} onClick={() => void copy(buildGeneratorPrompt(r, g), r.id)}>Copy prompt for {GENERATOR_LABEL[g]}</button>
                    ))}
                    {status === 'pending' && generatorsFor(r.kind).map((generator) => (
                      <button key={`import-${generator}`} className={button} disabled={busy}
                        onClick={() => onImport(r.id, GENERATOR_LABEL[generator])}>
                        Import {GENERATOR_LABEL[generator]} file for {r.id}…
                      </button>
                    ))}
                    {status === 'pending' && <button className={button} disabled={busy} onClick={() => setLink(r.id, DROPPED)}>Drop</button>}
                    {status !== 'pending' && <button className={button} disabled={busy} onClick={() => setLink(r.id, null)}>Reset</button>}
                  </div>
                  {status === 'pending' && available.length > 0 && (
                    <div className="flex gap-1.5 items-end">
                      <label className="text-xs text-fg-2 flex-1">Link an existing imported asset
                        <select className="w-full mt-1 rounded border border-line bg-app p-1.5" value={selectedLink[r.id] ?? ''}
                          onChange={(event) => setSelectedLink((current) => ({ ...current, [r.id]: event.target.value }))}>
                          <option value="">Choose an asset</option>
                          {available.map((candidate) => <option key={candidate.id} value={candidate.id}>{candidate.original_filename} · {candidate.id}</option>)}
                        </select>
                      </label>
                      <button className={button} disabled={busy || !selectedLink[r.id]} onClick={() => onLink(r.id, selectedLink[r.id])}>Link</button>
                    </div>
                  )}
                  {status === 'ready' && !list.accepted?.[r.id] && (
                    <div className="flex gap-1.5 items-end">
                      <label className="text-xs text-fg-2 flex-1">Known issue you accept for {r.id}
                        <input className="w-full mt-1 rounded border border-line bg-app p-1.5" maxLength={500} value={issueNote[r.id] ?? ''}
                          onChange={(event) => setIssueNote((current) => ({ ...current, [r.id]: event.target.value }))} />
                      </label>
                      <button className={button} disabled={busy || !(issueNote[r.id] ?? '').trim()}
                        onClick={() => { onAccept(r.id, issueNote[r.id].trim()); setIssueNote((current) => ({ ...current, [r.id]: '' })); }}>Accept with issue</button>
                    </div>
                  )}
                  {status === 'ready' && list.accepted?.[r.id] && <button className={button} disabled={busy} onClick={() => onAccept(r.id, null)}>Clear accepted issue</button>}
                  {status === 'pending' && r.kind === 'image' && <p className="text-xs text-fg-3">PNG and JPEG stills are accepted. Select the saved image from the external tool.</p>}
                  {copied === r.id && <p role="status" className="text-xs text-fg-3">Prompt copied. Paste it into the external tool.</p>}
                </li>
              );
            })}
          </ul>
          {copied === 'failed' && <p role="alert" className="text-xs text-danger">Clipboard unavailable. Select the request text and copy it manually.</p>}
          <p className="text-xs text-fg-3" data-testid="native-checklist-gate">
            {checklistReady(list, registered) ? 'All requests are imported or dropped.' : 'Execute is blocked: some requests are still pending.'}
          </p>
          <button className={button} disabled={busy} onClick={() => onChange(null)}>Load a different plan</button>
        </>
      )}
    </div>
  );
};
