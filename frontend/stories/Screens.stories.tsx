import type { Meta, StoryObj } from '@storybook/react-vite';
import { QueryClientProvider } from '@tanstack/react-query';
import { createMemoryHistory, createRouter, RouterProvider } from '@tanstack/react-router';
import { useEffect, useState } from 'react';

import { router } from '@/routes';
import { queryClient } from '@/shared/lib';
import { Toaster } from '@/shared/ui';

import { installPreviewTransport, type PreviewState } from './previewApi';

function PreviewScreen({ path, state }: { path: string; state: PreviewState }) {
  const [ready, setReady] = useState(false);
  const [instance] = useState(() =>
    createRouter({
      routeTree: router.routeTree,
      history: createMemoryHistory({ initialEntries: [path] }),
    }),
  );
  useEffect(() => {
    const defaults = queryClient.getDefaultOptions();
    queryClient.clear();
    queryClient.setDefaultOptions({ queries: { retry: false, refetchOnWindowFocus: false } });
    const restore = installPreviewTransport(state);
    setReady(true);
    return () => {
      void queryClient.cancelQueries();
      queryClient.clear();
      queryClient.setDefaultOptions(defaults);
      restore();
    };
  }, [state]);
  if (!ready) return null;
  return (
    <QueryClientProvider client={queryClient}>
      <div className="min-h-screen bg-surface-card">
        <RouterProvider router={instance} />
      </div>
      <Toaster />
    </QueryClientProvider>
  );
}

const meta = {
  title: 'Design System/Screens',
  component: PreviewScreen,
  parameters: {
    layout: 'fullscreen',
    docs: {
      description: {
        component:
          'Настоящие экраны приложения с вымышленными данными. State меняет ответы API; запросы и операции не отправляются на сервер. Отсутствующие фикстуры показывают явную ошибку.',
      },
    },
  },
  argTypes: {
    path: { control: false },
    state: { control: 'select', options: ['populated', 'empty', 'error', 'loading'] },
  },
  args: { path: '/', state: 'populated' },
  render: (args) => <PreviewScreen key={`${args.path}:${args.state}`} {...args} />,
} satisfies Meta<typeof PreviewScreen>;
export default meta;
type Story = StoryObj<typeof meta>;
export const Accounts: Story = {};
export const Warming: Story = { args: { path: '/warming' } };
export const Neurocomment: Story = { args: { path: '/neurocomment' } };
export const Neuroshilling: Story = { args: { path: '/neuroshilling' } };
export const Logs: Story = { args: { path: '/logs' } };
export const Settings: Story = { args: { path: '/settings' } };
export const Login: Story = { args: { path: '/login' } };
