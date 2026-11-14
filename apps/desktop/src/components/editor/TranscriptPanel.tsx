import React, { useMemo, useState } from 'react';
import { CornerDownRight, Search } from 'lucide-react';
import type { DemoRevision, DemoTranscriptUtterance } from '../../types';
import { useEditorStore } from '../../stores/editorStore';
import { framesToTimecode } from '../../utils/timecode';
import { clipAt } from '../../utils/timelineGeometry';
import { Badge, cx } from '../ui/primitives';
import { EmptyState } from '../ui/StateViews';

interface Props {
  utterances: DemoTranscriptUtterance[];
  revision: DemoRevision;
}

function highlight(text: string, q: string): React.ReactNode {
  if (!q) return text;
  const i = text.toLowerCase().indexOf(q.toLowerCase());
  if (i < 0) return text;
  return (
    <>
      {text.slice(0, i)}
      <mark className="bg-warning/40 text-fg rounded-sm">{text.slice(i, i + q.length)}</mark>
      {highlight(text.slice(i + q.length), q)}
    </>
  );
}

export const TranscriptPanel: React.FC<Props> = ({ utterances, revision }) => {
  const [search, setSearch] = useState('');
  const [ui, store] = useEditorStore();
  const { playheadFrames, fps, highlightedUtteranceId } = ui;

  const filtered = useMemo(() => {
    const q = search.toLowerCase().trim();
    return q ? utterances.filter((u) => u.text.toLowerCase().includes(q) || u.speaker.toLowerCase().includes(q)) : utterances;
  }, [utterances, search]);

  if (utterances.length === 0) {
    return (
      <div className="p-3">
        <EmptyState compact title="No transcript" message="This demo project has no transcript fixture. Transcription requires engine integration." />
      </div>
    );
  }

  /** "Jump to source" simulation: seek and select the V1 clip that carries this line. */
  const jump = (u: DemoTranscriptUtterance) => {
    store.pause();
    store.setPlayhead(u.startFrame);
    const v1 = revision.tracks.find((t) => t.type === 'video');
    const clip = clipAt(v1, u.startFrame);
    store.setState({ highlightedUtteranceId: u.id });
    if (clip && v1) store.selectClip(clip.id, v1.id);
  };

  return (
    <div className="h-full flex flex-col">
      <div className="p-3 border-b border-line flex flex-col gap-2">
        <div className="relative">
          <Search aria-hidden className="w-3.5 h-3.5 absolute left-2.5 top-2.5 text-fg-3" />
          <input
            type="search"
            aria-label="Search transcript"
            placeholder="Search transcript"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            className="w-full h-8 pl-8 pr-2 rounded-md bg-app border border-line text-[13px] text-fg placeholder:text-fg-3 focus:outline-none focus:border-accent"
          />
        </div>
        <div className="flex items-center justify-between text-[11px] text-fg-3">
          <span aria-live="polite">
            {search ? `${filtered.length} of ${utterances.length} lines` : `${utterances.length} lines`}
          </span>
          <Badge>Sample transcript</Badge>
        </div>
      </div>
      <ol className="flex-1 overflow-y-auto p-2 flex flex-col gap-1.5">
        {filtered.length === 0 && <li className="p-4 text-center text-xs text-fg-3">No lines match “{search}”.</li>}
        {filtered.map((u) => {
          const playing = playheadFrames >= u.startFrame && playheadFrames < u.endFrame;
          const picked = highlightedUtteranceId === u.id;
          return (
            <li key={u.id}>
              <button
                type="button"
                data-utterance-id={u.id}
                aria-current={playing ? 'true' : undefined}
                onClick={() => jump(u)}
                className={cx(
                  'w-full text-left p-2 rounded-lg border transition-colors',
                  picked ? 'border-accent bg-accent/12' : playing ? 'border-accent/40 bg-accent/5' : 'border-line bg-surface hover:border-fg-3'
                )}
              >
                <span className="flex items-center justify-between text-[11px] mb-0.5">
                  <span className="font-semibold text-fg-2">{u.speaker}</span>
                  <span className="font-mono text-fg-3">{framesToTimecode(u.startFrame, fps)}</span>
                </span>
                <span className={cx('block text-[13px] leading-relaxed', playing || picked ? 'text-fg' : 'text-fg-2')}>
                  {highlight(u.text, search.trim())}
                </span>
                {picked && (
                  <span className="mt-1 flex items-center gap-1 text-[11px] text-accent">
                    <CornerDownRight className="w-3 h-3" /> Jumped to source clip (simulated)
                  </span>
                )}
              </button>
            </li>
          );
        })}
      </ol>
    </div>
  );
};
