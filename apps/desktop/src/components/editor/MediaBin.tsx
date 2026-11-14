import React, { useMemo, useState } from 'react';
import { FileText, Film, Music, Plus, Search, Upload } from 'lucide-react';
import type { DemoAsset, MediaKind } from '../../types';
import { formatBytes, framesToTimecode } from '../../utils/timecode';
import { Badge, Button, Unavailable, cx } from '../ui/primitives';
import { EmptyState } from '../ui/StateViews';

interface Props {
  assets: DemoAsset[];
  /** Library assets not yet in this project — offered via "Add sample asset". */
  libraryAssets: DemoAsset[];
  onAddAssets: (ids: string[]) => void;
}

export const KIND_ICON: Record<MediaKind, React.ReactNode> = {
  video: <Film className="w-4 h-4" />,
  audio: <Music className="w-4 h-4" />,
  image: <Film className="w-4 h-4" />,
  subtitle: <FileText className="w-4 h-4" />,
};

export const MediaBin: React.FC<Props> = ({ assets, libraryAssets, onAddAssets }) => {
  const [filter, setFilter] = useState<'all' | MediaKind>('all');
  const [search, setSearch] = useState('');
  const [selectedId, setSelectedId] = useState<string | null>(null);

  const filtered = useMemo(() => {
    const q = search.toLowerCase();
    return assets.filter(
      (a) => (filter === 'all' || a.kind === filter) && (a.name.toLowerCase().includes(q) || a.filename.toLowerCase().includes(q))
    );
  }, [assets, filter, search]);
  const available = libraryAssets.filter((a) => !assets.some((x) => x.id === a.id));
  const selected = assets.find((a) => a.id === selectedId) ?? null;

  return (
    <div className="h-full flex flex-col">
      <div className="p-3 border-b border-line flex flex-col gap-2">
        <div className="relative">
          <Search aria-hidden className="w-3.5 h-3.5 absolute left-2.5 top-2.5 text-fg-3" />
          <input
            type="search"
            aria-label="Search media"
            placeholder="Search media"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            className="w-full h-8 pl-8 pr-2 rounded-md bg-app border border-line text-[13px] text-fg placeholder:text-fg-3 focus:outline-none focus:border-accent"
          />
        </div>
        <div role="radiogroup" aria-label="Filter by type" className="flex gap-1">
          {(['all', 'video', 'audio', 'subtitle'] as const).map((k) => (
            <button
              key={k}
              role="radio"
              aria-checked={filter === k}
              onClick={() => setFilter(k)}
              className={cx(
                'h-6 px-2 rounded text-xs capitalize',
                filter === k ? 'bg-accent/15 text-accent' : 'text-fg-3 hover:text-fg'
              )}
            >
              {k}
            </button>
          ))}
        </div>
      </div>

      <div className="flex-1 overflow-y-auto p-2 flex flex-col gap-1.5">
        {assets.length === 0 ? (
          <EmptyState
            compact
            title="No media in this project"
            message="Add demo assets to start planning an edit."
            action={
              <Button variant="primary" size="sm" disabled={available.length === 0} onClick={() => onAddAssets(available.slice(0, 3).map((a) => a.id))}>
                Choose sample assets
              </Button>
            }
          />
        ) : filtered.length === 0 ? (
          <EmptyState compact title="No matching media" action={<Button size="sm" onClick={() => { setSearch(''); setFilter('all'); }}>Clear filters</Button>} />
        ) : (
          filtered.map((a) => (
            <button
              key={a.id}
              type="button"
              aria-pressed={selectedId === a.id}
              onClick={() => setSelectedId(selectedId === a.id ? null : a.id)}
              className={cx(
                'w-full text-left p-2 rounded-lg border flex gap-2.5 items-start transition-colors',
                selectedId === a.id ? 'border-accent bg-accent/10' : 'border-line bg-surface hover:border-fg-3'
              )}
            >
              <span
                aria-hidden
                className="w-12 h-9 rounded-md shrink-0 flex items-center justify-center text-[#08121A]"
                style={{ background: `linear-gradient(135deg, ${a.thumbnailColor ?? '#5E88D7'}, #1C2634)` }}
              >
                {KIND_ICON[a.kind]}
              </span>
              <span className="min-w-0 flex-1">
                <span className="block text-[13px] font-medium text-fg truncate">{a.name}</span>
                <span className="block text-[11px] font-mono text-fg-3 truncate">{a.filename}</span>
                <span className="flex gap-2 mt-0.5 text-[11px] font-mono text-fg-2">
                  {a.durationFrames ? <span>{framesToTimecode(a.durationFrames, a.frameRateNum ?? 30)}</span> : null}
                  <span>{formatBytes(a.fileSizeBytes)}</span>
                </span>
              </span>
            </button>
          ))
        )}
      </div>

      {selected && (
        <div className="border-t border-line p-3 text-xs flex flex-col gap-1" aria-label="Selected asset metadata">
          <div className="flex items-center gap-1.5">
            <span className="font-semibold text-fg truncate">{selected.name}</span>
            <Badge>Demo</Badge>
          </div>
          {selected.description && <p className="text-fg-2 leading-relaxed">{selected.description}</p>}
          <dl className="grid grid-cols-2 gap-x-2 gap-y-0.5 font-mono text-[11px] text-fg-2 mt-1">
            <dt className="text-fg-3">ID</dt>
            <dd className="truncate">{selected.id}</dd>
            <dt className="text-fg-3">Kind</dt>
            <dd>{selected.kind}</dd>
            {selected.width && (
              <>
                <dt className="text-fg-3">Size</dt>
                <dd>
                  {selected.width}×{selected.height}
                </dd>
              </>
            )}
            {selected.frameRateNum && (
              <>
                <dt className="text-fg-3">FPS</dt>
                <dd>{selected.frameRateNum / (selected.frameRateDen ?? 1)}</dd>
              </>
            )}
          </dl>
        </div>
      )}

      <div className="p-2 border-t border-line flex gap-2">
        <Button
          size="sm"
          className="flex-1"
          icon={<Plus className="w-3.5 h-3.5" />}
          disabled={available.length === 0}
          title={available.length === 0 ? 'All demo assets are already in this project' : undefined}
          onClick={() => onAddAssets([available[0].id])}
        >
          Add sample asset
        </Button>
        <Unavailable reason="Importing files from disk requires media engine integration">
          <Button size="sm" icon={<Upload className="w-3.5 h-3.5" />}>
            Import
          </Button>
        </Unavailable>
      </div>
    </div>
  );
};
