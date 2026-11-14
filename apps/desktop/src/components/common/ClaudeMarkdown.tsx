import React from 'react';

/**
 * Renders the small Markdown subset Claude uses in replies: paragraphs, headings,
 * bullet and numbered lists, **bold**, *italic*, `code` and fenced code blocks.
 * Builds React elements only, so reply text can never inject HTML.
 */
export const ClaudeMarkdown: React.FC<{ text: string }> = ({ text }) => (
  <div className="space-y-2 leading-relaxed">{parseBlocks(text).map(renderBlock)}</div>
);

type Block =
  | { kind: 'p'; lines: string[] }
  | { kind: 'h'; text: string }
  | { kind: 'ul' | 'ol'; items: string[] }
  | { kind: 'code'; text: string };

const BULLET = /^\s*[-*•]\s+(.*)$/;
const NUMBERED = /^\s*\d+[.)]\s+(.*)$/;
const HEADING = /^\s*#{1,6}\s+(.*)$/;

export function parseBlocks(text: string): Block[] {
  const blocks: Block[] = [];
  const lines = text.replace(/\r\n/g, '\n').split('\n');
  for (let i = 0; i < lines.length; i++) {
    const line = lines[i];
    if (/^\s*```/.test(line)) {
      const code: string[] = [];
      for (i++; i < lines.length && !/^\s*```/.test(lines[i]); i++) code.push(lines[i]);
      blocks.push({ kind: 'code', text: code.join('\n') });
      continue;
    }
    if (!line.trim()) continue;
    const heading = HEADING.exec(line);
    if (heading) { blocks.push({ kind: 'h', text: heading[1] }); continue; }
    const listKind = BULLET.test(line) ? 'ul' : NUMBERED.test(line) ? 'ol' : null;
    const last = blocks[blocks.length - 1];
    if (listKind) {
      const item = (listKind === 'ul' ? BULLET : NUMBERED).exec(line)![1];
      // Blank lines between items still make one list.
      if (last?.kind === listKind) last.items.push(item);
      else blocks.push({ kind: listKind, items: [item] });
      continue;
    }
    // An indented line right after a list item continues that item.
    if ((last?.kind === 'ul' || last?.kind === 'ol') && /^\s{2,}\S/.test(line) && lines[i - 1]?.trim()) {
      last.items[last.items.length - 1] += ` ${line.trim()}`;
      continue;
    }
    if (last?.kind === 'p' && lines[i - 1]?.trim()) last.lines.push(line.trim());
    else blocks.push({ kind: 'p', lines: [line.trim()] });
  }
  return blocks;
}

const renderBlock = (block: Block, index: number) => {
  switch (block.kind) {
    case 'h':
      return <p key={index} className="font-semibold">{inline(block.text)}</p>;
    case 'ul':
      return <ul key={index} className="list-disc space-y-1 pl-5">{block.items.map((item, n) => <li key={n}>{inline(item)}</li>)}</ul>;
    case 'ol':
      return <ol key={index} className="list-decimal space-y-1 pl-5">{block.items.map((item, n) => <li key={n}>{inline(item)}</li>)}</ol>;
    case 'code':
      return <pre key={index} className="overflow-x-auto rounded bg-sidebar p-2 text-xs"><code>{block.text}</code></pre>;
    case 'p':
      return <p key={index}>{block.lines.map((line, n) => <React.Fragment key={n}>{n > 0 && <br />}{inline(line)}</React.Fragment>)}</p>;
  }
};

const INLINE = /(`[^`]+`|\*\*[^*]+\*\*|__[^_]+__|\*[^*\s][^*]*\*)/g;

export function inline(text: string): React.ReactNode[] {
  return text.split(INLINE).filter(Boolean).map((part, index) => {
    if (part.startsWith('`') && part.endsWith('`') && part.length > 2) {
      return <code key={index} className="rounded bg-sidebar px-1 py-0.5 text-[0.85em]">{part.slice(1, -1)}</code>;
    }
    if ((part.startsWith('**') && part.endsWith('**') || part.startsWith('__') && part.endsWith('__')) && part.length > 4) {
      return <strong key={index}>{part.slice(2, -2)}</strong>;
    }
    if (part.startsWith('*') && part.endsWith('*') && part.length > 2) return <em key={index}>{part.slice(1, -1)}</em>;
    return part;
  });
}
