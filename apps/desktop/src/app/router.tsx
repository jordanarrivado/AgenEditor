import React from 'react';
import { Navigate, type RouteObject } from 'react-router-dom';
import { AppShell } from '../components/common/AppShell';
import { useSettings } from './SettingsContext';
import { NotFoundPage, RouteErrorPage } from '../features/system/NotFoundPage';
import { LoadingState } from '../components/ui/StateViews';

/** `/` → Welcome on first run, Home afterwards. */
const RootRedirect: React.FC = () => {
  const { settings } = useSettings();
  return <Navigate to={settings.hasSeenWelcome ? '/home' : '/welcome'} replace />;
};

/** Route-level code splitting: each screen is its own chunk. */
const page =
  <K extends string>(load: () => Promise<Record<K, React.ComponentType>>, name: K) =>
  async () => ({ Component: (await load())[name] });

const Fallback: React.FC = () => (
  <div className="p-6">
    <LoadingState label="Loading screen…" />
  </div>
);

export const routes: RouteObject[] = [
  {
    errorElement: <RouteErrorPage />,
    hydrateFallbackElement: <Fallback />,
    children: [
      { path: '/', element: <RootRedirect /> },
      { path: '/welcome', lazy: page(() => import('../features/welcome/WelcomePage'), 'WelcomePage') },
      {
        element: <AppShell />,
        children: [
          { path: '/home', lazy: page(() => import('../features/dashboard/HomePage'), 'HomePage') },
          { path: '/projects', lazy: page(() => import('../features/project-library/ProjectsPage'), 'ProjectsPage') },
          { path: '/projects/new', lazy: page(() => import('../features/project-creation/NewProjectPage'), 'NewProjectPage') },
          { path: '/projects/:projectId', lazy: page(() => import('../features/project-overview/ProjectOverviewPage'), 'ProjectOverviewPage') },
          { path: '/projects/:projectId/editor', lazy: page(() => import('../features/editor-workspace/EditorPage'), 'EditorPage') },
          { path: '/projects/:projectId/review', lazy: page(() => import('../features/review/ReviewPage'), 'ReviewPage') },
          { path: '/activity', lazy: page(() => import('../features/activity/ActivityPage'), 'ActivityPage') },
          { path: '/templates', lazy: page(() => import('../features/templates/TemplatesPage'), 'TemplatesPage') },
          { path: '/settings', lazy: page(() => import('../features/settings/SettingsPage'), 'SettingsPage') },
          { path: '/help', lazy: page(() => import('../features/help/HelpPage'), 'HelpPage') },
          { path: '*', element: <NotFoundPage /> },
        ],
      },
    ],
  },
];
