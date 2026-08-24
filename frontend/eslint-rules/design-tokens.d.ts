import type { Rule } from 'eslint';

// The rule itself is plain JS so eslint.config.js can import it without a build
// step; this is the shape its test needs.
declare const plugin: { rules: Record<string, Rule.RuleModule> };
export default plugin;
