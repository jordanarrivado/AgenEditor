/**
 * Step 2 — Prepare assets (spec §3.1). Turns the planner's asset_requests into a
 * one-click checklist for EXTERNAL generators. AgenEditor generates nothing.
 */
import React, { useState } from 'react';
import { Check, ClipboardCopy, ImagePlus, Undo2, X } from 'lucide-react';
import {
  GENERATOR_LABEL,
  buildGeneratorPrompt,
  buildStoryboardPrompt,
  generatorsFor,
  parseAssetRequests,
  type AssetRequest,
  type AssetRequestStatus,
  type Generator,
} from '../../utils/assetRequests';
import { copyText } from '../../utils/agentBrief';
import { Badge, Button } from '../ui/primitives';
import { showToast } from '../common/Toast';

export interface AssetPrepState {
  /** null = planner reply not loaded yet; [] = no assets needed. */
  requests: AssetRequest[] | null;
  status: Record<string, AssetRequestStatus>;
}

export const EMPTY_ASSET_PREP: AssetPrepState = { requests: null, status: {} };

async function copyFor(text: string, title: string, target: string) {
  const ok = await copyText(text);
  showToast(
    ok
      ? { type: 'success', title, message: `Paste it into ${target}, then import the result into the asset bin.` }
      : { type: 'warning', title: 'Clipboard unavailable', message: 'Select the request text and copy it manually.' }
  );
}

export const AssetPrepCard: React.FC<{
  state: AssetPrepState;
  onChange: (s: AssetPrepState) => void;
}> = ({ state, onChange }) => {
  const [pasted, setPasted] = useState('');
  const [error, setError] = useState<string | null>(null);
  const { requests, status } = state;
  const images = requests?.filter((r) => r.kind === 'image') ?? [];

  const load = () => {
    const res = parseAssetRequests(pasted);
    if (!res.ok) {
      setError(res.error);
      return;
    }
    setError(null);
    setPasted('');
    onChange({ requests: res.requests, status: Object.fromEntries(res.requests.map((r) => [r.id, 'pending'])) });
  };
  const setStatus = (id: string, s: AssetRequestStatus) => onChange({ requests, status: { ...status, [id]: s } });

  return (
    <li data-testid="role-assets" className="p-2.5 rounded-lg border border-line bg-surface flex flex-col gap-2">
      <span className="flex items-center gap-1.5 flex-wrap">
        <span className="text-[13px] font-medium text-fg">2. Prepare assets</span>
        <Badge tone="accent">ChatGPT · Gemini · Google Flow</Badge>
      </span>
      <span className="text-xs text-fg-2">
        Storyboard frames and B-roll the footage lacks are generated outside AgenEditor, then imported.
      </span>

      {requests === null ? (
        <>
          <label htmlFor="asset-requests-paste" className="text-xs font-medium text-fg-2">
            Paste the planner's reply
          </label>
          <textarea
            id="asset-requests-paste"
            value={pasted}
            onChange={(e) => setPasted(e.target.value)}
            rows={3}
            placeholder='… {"asset_requests": [ … ]}'
            className="w-full p-2 rounded-md bg-app border border-line font-mono text-[11px] text-fg-2 focus:outline-none focus:border-accent resize-y"
          />
          {error && (
            <p role="alert" className="text-xs text-danger">
              {error}
            </p>
          )}
          <span className="flex flex-wrap gap-1.5">
            <Button size="sm" variant="primary" onClick={load} disabled={!pasted.trim()}>
              Load requests
            </Button>
            <Button size="sm" variant="ghost" onClick={() => onChange({ requests: [], status: {} })}>
              No assets needed
            </Button>
          </span>
        </>
      ) : (
        <>
          {images.length > 0 && (
            <span className="flex flex-wrap gap-1.5">
              <Button
                size="sm"
                variant="primary"
                icon={<ImagePlus className="w-3.5 h-3.5" />}
                onClick={() => copyFor(buildStoryboardPrompt(requests, 'chatgpt'), 'Storyboard prompt copied', 'ChatGPT')}
              >
                Generate storyboard
              </Button>
              <Button size="sm" variant="ghost" onClick={() => copyFor(buildStoryboardPrompt(requests, 'gemini'), 'Storyboard prompt copied', 'Gemini')}>
                Gemini instead
              </Button>
            </span>
          )}
          {requests.length === 0 ? (
            <p className="text-xs text-fg-3">No generated assets needed for this plan.</p>
          ) : (
            <ul className="flex flex-col gap-1.5" aria-label="Asset requests">
              {requests.map((r) => {
                const s = status[r.id] ?? 'pending';
                return (
                  <li key={r.id} data-testid={`asset-request-${r.id}`} className="p-2 rounded-md border border-line-subtle bg-app flex flex-col gap-1.5">
                    <span className="flex items-center gap-1.5 flex-wrap">
                      <span className="font-mono text-[11px] text-fg">{r.id}</span>
                      <Badge>{r.kind}</Badge>
                      <Badge tone={s === 'ready' ? 'success' : s === 'dropped' ? 'neutral' : 'warning'}>{s}</Badge>
                    </span>
                    {r.purpose && <span className="text-xs text-fg-2">{r.purpose}</span>}
                    <span className="flex flex-wrap gap-1.5">
                      {generatorsFor(r.kind).map((g: Generator) => (
                        <Button
                          key={g}
                          size="sm"
                          variant="ghost"
                          icon={<ClipboardCopy className="w-3.5 h-3.5" />}
                          onClick={() => copyFor(buildGeneratorPrompt(r, g), `Prompt for ${r.id} copied`, GENERATOR_LABEL[g])}
                        >
                          {GENERATOR_LABEL[g]}
                        </Button>
                      ))}
                      {s === 'pending' ? (
                        <>
                          <Button size="sm" icon={<Check className="w-3.5 h-3.5" />} onClick={() => setStatus(r.id, 'ready')} aria-label={`Mark ${r.id} ready`}>
                            Ready
                          </Button>
                          <Button size="sm" variant="ghost" icon={<X className="w-3.5 h-3.5" />} onClick={() => setStatus(r.id, 'dropped')} aria-label={`Drop ${r.id}`}>
                            Drop
                          </Button>
                        </>
                      ) : (
                        <Button size="sm" variant="ghost" icon={<Undo2 className="w-3.5 h-3.5" />} onClick={() => setStatus(r.id, 'pending')} aria-label={`Reset ${r.id}`}>
                          Undo
                        </Button>
                      )}
                    </span>
                  </li>
                );
              })}
            </ul>
          )}
          <p className="text-xs text-fg-3">
            Generated files are labelled AI-generated (external). Demo Mode: "Ready" is your confirmation; it does not check that the file was imported.
          </p>
          <Button size="sm" variant="ghost" onClick={() => onChange(EMPTY_ASSET_PREP)}>
            Load a different plan
          </Button>
        </>
      )}
    </li>
  );
};
