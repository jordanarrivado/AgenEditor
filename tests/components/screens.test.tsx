import { describe, expect, it } from 'vitest';
import { act, fireEvent, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { renderApp } from '../renderApp';

const clip = (label: RegExp) => screen.getAllByRole('button', { pressed: false }).find((b) => b.hasAttribute('data-clip-id') && label.test(b.getAttribute('aria-label') ?? ''))!;

describe('routing', () => {
  it('opens every top-level route directly', async () => {
    for (const [path, heading] of [
      ['/home', 'Home'],
      ['/projects', 'Projects'],
      ['/projects/new', 'New Project'],
      ['/activity', 'Activity'],
      ['/production', 'Production'],
      ['/templates', 'Templates'],
      ['/settings', 'Settings'],
      ['/help', 'Help & About'],
    ] as const) {
      const { unmount } = renderApp(path);
      expect(await screen.findByRole('heading', { level: 1, name: heading })).toBeInTheDocument();
      unmount();
    }
  });

  it('shows a 404 with Return Home for unknown routes and projects', async () => {
    const a = renderApp('/nope');
    expect(await screen.findByRole('heading', { name: 'Page not found' })).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'Return Home' })).toHaveAttribute('href', '/home');
    a.unmount();
    renderApp('/projects/missing/editor');
    expect(await screen.findByRole('heading', { name: 'Project not found' })).toBeInTheDocument();
  });

  it('redirects / to Welcome on first run', async () => {
    renderApp('/', { settings: { hasSeenWelcome: false } });
    expect(await screen.findByRole('heading', { name: 'Turn raw footage into a finished edit.' })).toBeInTheDocument();
    expect(screen.getByText('UI Prototype · Demo Mode')).toBeInTheDocument();
  });
});

describe('project library', () => {
  it('search, status filter and grid/list toggle change visible results', async () => {
    const user = userEvent.setup();
    renderApp('/projects');
    const grid = await screen.findByRole('list', { name: 'Projects' });
    expect(within(grid).getAllByRole('listitem')).toHaveLength(3);

    await user.type(screen.getByRole('searchbox', { name: 'Search projects' }), 'podcast');
    expect(within(screen.getByRole('list', { name: 'Projects' })).getAllByRole('listitem')).toHaveLength(1);

    await user.clear(screen.getByRole('searchbox', { name: 'Search projects' }));
    await user.selectOptions(screen.getByLabelText('Status'), 'draft');
    expect(within(screen.getByRole('list', { name: 'Projects' })).getAllByRole('listitem')).toHaveLength(1);

    await user.selectOptions(screen.getByLabelText('Status'), 'approved');
    await user.type(screen.getByRole('searchbox', { name: 'Search projects' }), 'zzz');
    expect(screen.getByText('No matching projects')).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Clear filters' }));

    await user.click(screen.getByRole('radio', { name: /List/ }));
    expect(screen.getByRole('table', { name: 'Projects' })).toBeInTheDocument();
  });
});

describe('new project wizard', () => {
  it('validates input, preserves values across steps and creates a project', async () => {
    const user = userEvent.setup();
    const { router, repo } = renderApp('/projects/new');
    await screen.findByRole('heading', { name: 'Project details' });

    await user.click(screen.getByRole('button', { name: /Next/ }));
    expect(screen.getByText('Enter a project name.')).toBeInTheDocument();

    await user.type(screen.getByLabelText('Project title *'), 'Wizard Reel');
    const dur = screen.getByLabelText('Target duration (seconds)');
    await user.clear(dur);
    await user.type(dur, '5');
    await user.click(screen.getByRole('button', { name: /Next/ }));
    expect(screen.getByText(/between 15 and 180 seconds/)).toBeInTheDocument();
    await user.clear(dur);
    await user.type(dur, '30');
    await user.click(screen.getByRole('button', { name: /Next/ }));

    await screen.findByRole('heading', { name: 'Media assets' });
    await user.click(screen.getByRole('button', { name: /Next/ }));
    expect(screen.getByText('Select at least one primary video asset.')).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Choose sample assets' }));

    // Back keeps step-1 values
    await user.click(screen.getByRole('button', { name: /Back/ }));
    expect(screen.getByLabelText('Project title *')).toHaveValue('Wizard Reel');
    await user.click(screen.getByRole('button', { name: /Next/ }));
    await user.click(screen.getByRole('button', { name: /Next/ }));
    await screen.findByRole('heading', { name: 'Editing brief' });
    await user.click(screen.getByRole('button', { name: /Next/ }));

    const summary = await screen.findByTestId('wizard-summary');
    expect(within(summary).getByText('Wizard Reel')).toBeInTheDocument();
    expect(within(summary).getByText(/30 s · 30 fps/)).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Create Demo Project' }));

    await waitFor(() => expect(router.state.location.pathname).toMatch(/^\/projects\/proj_/));
    const created = (await repo.listProjects()).find((p) => p.name === 'Wizard Reel')!;
    expect(created.targetDurationFrames).toBe(900);
    expect(await screen.findByRole('heading', { level: 1, name: 'Wizard Reel' })).toBeInTheDocument();
  });
});

describe('editor workstation', () => {
  it('labels the preview as simulated and selects clips into the inspector', async () => {
    const user = userEvent.setup();
    renderApp('/projects/reel_001/editor');
    expect(await screen.findByTestId('simulated-label')).toHaveTextContent('Simulated timeline preview');
    expect(screen.getByText('Demo timeline · No actual rendering connected')).toBeInTheDocument();

    await user.click(clip(/Cue Friction Argument/));
    const inspector = screen.getByRole('complementary', { name: 'Inspector' });
    expect(within(inspector).getByRole('heading', { name: 'Cue Friction Argument' })).toBeInTheDocument();
    expect(within(inspector).getByLabelText('Duration')).toBeInTheDocument();
  });

  it('removes a clip with Delete and restores it with undo', async () => {
    const user = userEvent.setup();
    renderApp('/projects/reel_001/editor');
    await screen.findByTestId('simulated-label');
    await user.click(clip(/Coffee Prep/));
    const before = document.querySelectorAll('[data-clip-id]').length;

    fireEvent.keyDown(window, { key: 'Delete' });
    await waitFor(() => expect(document.querySelectorAll('[data-clip-id]').length).toBe(before - 1));

    await user.click(screen.getByRole('button', { name: /^Undo:/ }));
    await waitFor(() => expect(document.querySelectorAll('[data-clip-id]').length).toBe(before));
    expect(screen.getByRole('button', { name: /^Redo:/ })).toBeEnabled();
  });

  it('moves the playhead with keyboard and transport, and zooms', async () => {
    renderApp('/projects/reel_001/editor');
    const tc = await screen.findByTestId('preview-timecode');
    act(() => {
      fireEvent.keyDown(window, { key: 'Home' });
    });
    expect(tc).toHaveTextContent('00:00:00');
    act(() => {
      fireEvent.keyDown(window, { key: 'ArrowRight' });
    });
    expect(tc).toHaveTextContent('00:00:01');
    act(() => {
      fireEvent.keyDown(window, { key: 'ArrowRight', shiftKey: true });
    });
    expect(tc).toHaveTextContent('00:01:01');
    fireEvent.click(screen.getByRole('button', { name: 'Go to end (End)' }));
    expect(tc).toHaveTextContent('00:40:00');

    const zoom = screen.getByRole('slider', { name: 'Timeline zoom' }) as HTMLInputElement;
    const z0 = Number(zoom.value);
    act(() => {
      fireEvent.keyDown(window, { key: '+' });
    });
    expect(Number(zoom.value)).toBeGreaterThan(z0);
  });

  it('transcript search highlights and jump-to-source seeks + selects the V1 clip', async () => {
    const user = userEvent.setup();
    renderApp('/projects/reel_001/editor');
    await screen.findByTestId('simulated-label');
    await user.click(screen.getByRole('tab', { name: /Transcript/ }));
    await user.type(screen.getByRole('searchbox', { name: 'Search transcript' }), 'friction');
    const marks = document.querySelectorAll('mark');
    expect(marks.length).toBeGreaterThan(0);
    const line = marks[0].closest('button')!;
    await user.click(line);
    expect(line).toHaveTextContent('Jumped to source clip (simulated)');
    const inspector = screen.getByRole('complementary', { name: 'Inspector' });
    expect(within(inspector).getByText('V1 Talking Head')).toBeInTheDocument();
  });

  it('toggling track visibility and panel collapse updates state', async () => {
    const user = userEvent.setup();
    renderApp('/projects/reel_001/editor');
    await screen.findByTestId('simulated-label');
    await user.click(screen.getByRole('button', { name: /^Hide V2 B-Roll/i }));
    expect(screen.getByRole('button', { name: /^Show V2 B-Roll/i })).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Hide inspector' }));
    expect(screen.queryByRole('complementary', { name: 'Inspector' })).not.toBeInTheDocument();
  });

  it('agent tab shows Not connected and a Demo Mode brief', async () => {
    const user = userEvent.setup();
    renderApp('/projects/reel_001/editor');
    await screen.findByTestId('simulated-label');
    await user.click(screen.getByRole('tab', { name: /Agent/ }));
    expect(screen.getByText('Not connected in UI prototype')).toBeInTheDocument();
    expect((screen.getByTestId('handoff-text') as HTMLTextAreaElement).value).toMatch(/^\[DEMO MODE\]/);
    expect(screen.queryByRole('button', { name: /^Connect$/ })).not.toBeInTheDocument();
  });

  it('locks Execute until generated assets are ready or dropped', async () => {
    const user = userEvent.setup();
    renderApp('/projects/reel_001/editor');
    await screen.findByTestId('simulated-label');
    await user.click(screen.getByRole('tab', { name: /Agent/ }));
    const exec = () => within(screen.getByTestId('role-executor')).getByRole('button', { name: /Copy execute prompt/ });
    expect(exec()).toBeDisabled();

    fireEvent.change(screen.getByLabelText("Paste the planner's reply"), {
      target: {
        value:
          '{"asset_requests": [{"id": "gen_a", "kind": "image", "prompt": "desk"}, {"id": "gen_b", "kind": "video", "prompt": "city"}]}',
      },
    });
    await user.click(screen.getByRole('button', { name: 'Load requests' }));
    expect(screen.getByRole('button', { name: 'Generate storyboard' })).toBeInTheDocument();
    expect(within(screen.getByTestId('asset-request-gen_b')).getByRole('button', { name: 'Google Flow' })).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Mark gen_a ready' }));
    expect(exec()).toBeDisabled();
    await user.click(screen.getByRole('button', { name: 'Drop gen_b' }));
    expect(exec()).toBeEnabled();
  });

  it('offers a sample edit plan for projects without a revision', async () => {
    const user = userEvent.setup();
    renderApp('/projects/reel_002/editor');
    await user.click(await screen.findByRole('button', { name: 'Create sample edit plan' }));
    expect(await screen.findByTestId('simulated-label')).toBeInTheDocument();
  });
});

describe('export dialog', () => {
  it('previews settings and never offers a file download', async () => {
    const user = userEvent.setup();
    renderApp('/projects/reel_001/editor');
    await screen.findByTestId('simulated-label');
    await user.click(screen.getByRole('button', { name: 'Export' }));
    const dialog = await screen.findByRole('dialog');
    expect(within(dialog).getByText('Configuration only — no render')).toBeInTheDocument();
    await user.click(within(dialog).getByRole('button', { name: 'Preview Export Settings' }));
    const summary = await within(dialog).findByTestId('export-summary');
    expect(within(summary).getAllByText('Planned · not created').length).toBeGreaterThanOrEqual(3);
    expect(dialog.querySelector('a[download], a[href$=".mp4"]')).toBeNull();
    await user.click(within(dialog).getByRole('button', { name: 'Queue simulation job' }));
    expect(await within(dialog).findByText(/scripted; no video will be produced/)).toBeInTheDocument();
  });
});

describe('review', () => {
  it('adds/removes notes and approves in demo', async () => {
    const user = userEvent.setup();
    const { repo } = renderApp('/projects/reel_001/review');
    await screen.findByTestId('qa-list');
    expect(screen.getAllByText('Not tested').length).toBeGreaterThan(0);

    await user.type(screen.getByLabelText('New review note'), 'Tighten the hook');
    await user.click(screen.getByRole('button', { name: 'Add note' }));
    const notes = await screen.findByTestId('review-notes');
    expect(within(notes).getByText('Tighten the hook')).toBeInTheDocument();
    await user.click(within(notes).getAllByRole('button', { name: /Remove note by You/ })[0]);
    await waitFor(() => expect(within(screen.getByTestId('review-notes')).queryByText('Tighten the hook')).not.toBeInTheDocument());

    await user.click(screen.getByRole('button', { name: 'Approve in demo' }));
    await user.click(within(await screen.findByRole('dialog')).getByRole('button', { name: 'Approve in demo' }));
    await waitFor(async () => expect((await repo.getProject('reel_001'))!.status).toBe('approved'));
    expect(await screen.findByRole('button', { name: 'Withdraw approval' })).toBeInTheDocument();
  });
});

describe('settings', () => {
  it('theme switch applies immediately', async () => {
    const user = userEvent.setup();
    renderApp('/settings');
    await screen.findByRole('heading', { level: 1, name: 'Settings' });
    await user.click(screen.getByRole('radio', { name: 'Light' }));
    expect(document.documentElement.dataset.theme).toBe('light');
    await user.click(screen.getByRole('radio', { name: 'Dark' }));
    expect(document.documentElement.dataset.theme).toBe('dark');
  });
});
