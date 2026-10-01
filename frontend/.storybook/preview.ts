import type { Preview } from '@storybook/react-vite';
import { createElement } from 'react';

import '../src/app/styles/index.css';
import { i18n } from '../src/shared/i18n';

const preview: Preview = {
  globalTypes: {
    locale: {
      description: 'Язык интерфейса',
      toolbar: {
        icon: 'globe',
        items: [
          { value: 'ru', title: 'Русский' },
          { value: 'en', title: 'English' },
        ],
        dynamicTitle: true,
      },
    },
  },
  initialGlobals: { locale: 'ru' },
  loaders: [
    async ({ globals }) => {
      await i18n.changeLanguage(globals.locale === 'en' ? 'en' : 'ru');
      return {};
    },
  ],
  decorators: [(Story, context) => createElement(Story, { key: String(context.globals.locale) })],
  parameters: {
    options: {
      storySort: {
        order: [
          'Design System',
          ['Foundations', 'Coverage', 'Overview', 'Components', 'Patterns', 'Screens'],
        ],
      },
    },
    viewport: {
      options: {
        mobile: { name: 'Телефон', styles: { width: '375px', height: '812px' } },
        tablet: { name: 'Планшет', styles: { width: '768px', height: '1024px' } },
        desktop: { name: 'Компьютер', styles: { width: '1280px', height: '900px' } },
      },
    },
  },
};

export default preview;
