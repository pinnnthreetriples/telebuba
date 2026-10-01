export const cardSpacing = 'px-xl py-lg';
export const cardProps = { className: cardSpacing, title: 'Preview' };
export const styles = { normal: 'flex', compact: 'p-lg' };
export const layoutProps = { className: 'flex', title: 'p-lg' };
export const conditionalProps = { className: Math.random() > 0.5 ? 'p-lg' : 'flex' };
export const reverseConditionalProps = { className: Math.random() > 0.5 ? 'flex' : 'p-lg' };
