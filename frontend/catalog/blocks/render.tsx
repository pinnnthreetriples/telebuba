// Точка входа для `scripts/blocks-doc.mjs`: Vite загружает её в Node, и каждый образец
// из `Blocks.tsx` становится статической разметкой настоящего компонента.
import { renderToStaticMarkup } from 'react-dom/server';

import '@/shared/i18n';

import { BLOCKS } from './Blocks';

export function renderBlocks() {
  return BLOCKS.map((block) => ({
    id: block.id,
    name: block.name,
    library: block.library === true,
    variants: block.variants.map((variant) => ({
      label: variant.label,
      html: renderToStaticMarkup(variant.node),
    })),
  }));
}
