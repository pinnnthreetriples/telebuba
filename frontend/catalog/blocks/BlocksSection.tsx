// Раздел каталога «Блоки»: те же образцы, что на `docs/blocks.html`, вживую и в браузере.
import { Cell, Row, Section } from '../Frame';

import { BLOCKS } from './Blocks';

export function BlocksSection() {
  return (
    <Section id="blocks" title="Блоки">
      {BLOCKS.map((block) => (
        <Row key={block.id} label={block.name}>
          {block.variants.map((variant) => (
            <Cell key={variant.label} caption={variant.label}>
              <div className="w-panel max-w-full">{variant.node}</div>
            </Cell>
          ))}
        </Row>
      ))}
    </Section>
  );
}
