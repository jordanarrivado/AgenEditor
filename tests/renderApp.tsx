import React from 'react';
import { render } from '@testing-library/react';
import { RouterProvider, createMemoryRouter } from 'react-router-dom';
import { AppProviders } from '../apps/desktop/src/app/providers';
import { routes } from '../apps/desktop/src/app/router';
import { MockProjectRepository } from '../apps/desktop/src/mocks/mockProjectRepository';
import type { UserSettings } from '../apps/desktop/src/app/SettingsContext';

/** Render the real route tree at `path` with an isolated, in-memory demo repository. */
export function renderApp(path: string, opts: { settings?: Partial<UserSettings> } = {}) {
  const repo = new MockProjectRepository({ storage: null });
  const router = createMemoryRouter(routes, { initialEntries: [path] });
  const utils = render(
    <AppProviders repo={repo} settings={{ hasSeenWelcome: true, ...opts.settings }}>
      <RouterProvider router={router} />
    </AppProviders>
  );
  return { ...utils, repo, router };
}
