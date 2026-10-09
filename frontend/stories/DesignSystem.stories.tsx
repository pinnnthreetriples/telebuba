import type { Meta, StoryObj } from '@storybook/react-vite';

import { Catalog } from '../catalog/Catalog';
import { Controls as CatalogControls } from '../catalog/Controls';
import { Feedback as CatalogFeedback } from '../catalog/Feedback';
import { Surfaces as CatalogSurfaces } from '../catalog/Surfaces';
import { Typography as CatalogTypography } from '../catalog/Typography';

import { OverviewPatterns } from './OverviewPatterns';
import { ButtonSizingGuide } from './ButtonSizingGuide';

const meta = {
  title: 'Design System/Overview',
  component: Catalog,
  parameters: { layout: 'fullscreen' },
} satisfies Meta<typeof Catalog>;

export default meta;
type Story = StoryObj<typeof meta>;

const frame = 'mx-auto max-w-shell px-4 py-8';

export const All: Story = {
  render: () => <Catalog patterns={<OverviewPatterns />} buttonGuide={<ButtonSizingGuide />} />,
};
export const Controls: Story = {
  render: () => (
    <main className={frame}>
      <CatalogControls intro={<ButtonSizingGuide />} />
    </main>
  ),
};
export const Feedback: Story = {
  render: () => (
    <main className={frame}>
      <CatalogFeedback />
    </main>
  ),
};
export const Surfaces: Story = {
  render: () => (
    <main className={frame}>
      <CatalogSurfaces />
    </main>
  ),
};
export const Patterns: Story = {
  render: () => (
    <main className={frame}>
      <OverviewPatterns />
    </main>
  ),
};
export const Typography: Story = {
  render: () => (
    <main className={frame}>
      <CatalogTypography />
    </main>
  ),
};
