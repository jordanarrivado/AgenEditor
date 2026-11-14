import React from 'react';
import { describe, expect, it } from 'vitest';
import { render } from '@testing-library/react';
import { ClaudeMarkdown } from '../../apps/desktop/src/components/common/ClaudeMarkdown';

const reply = `I can reach the project. Here's what's in it right now:

- **Brief:** empty. There are no instructions yet.
- **Assets:** one clip, \`clip-02-p2.mp4\`. It's registered as **B-roll**.

To start an edit, do one of these:
1. Add your main clip to the **Main footage** box.
2. If \`clip-02-p2.mp4\` is meant to be the main footage, add it again there.`;

describe('ClaudeMarkdown', () => {
  it('renders bold, code, bullet and numbered lists instead of raw markers', () => {
    const { container } = render(<ClaudeMarkdown text={reply} />);
    expect(container.textContent).not.toMatch(/\*\*|`/);
    expect(container.querySelectorAll('ul > li')).toHaveLength(2);
    expect(container.querySelectorAll('ol > li')).toHaveLength(2);
    expect([...container.querySelectorAll('strong')].map((el) => el.textContent)).toEqual(['Brief:', 'Assets:', 'B-roll', 'Main footage']);
    expect(container.querySelectorAll('code')).toHaveLength(2);
  });

  it('shows HTML in replies as text, never as markup', () => {
    const { container } = render(<ClaudeMarkdown text={'<img src=x onerror=alert(1)> **hi**'} />);
    expect(container.querySelector('img')).toBeNull();
    expect(container.textContent).toContain('<img src=x onerror=alert(1)>');
  });
});
