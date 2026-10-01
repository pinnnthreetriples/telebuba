import { existsSync, readFileSync, statSync } from 'node:fs';
import { dirname, resolve } from 'node:path';

import ts from 'typescript';

// Values being on the scale does not make a local override part of the system.
// Resolve JSX attributes and lexical constants instead of scanning quoted text.
const SURFACE = /^(?:p[trblxy]?|rounded|bg|border)(?:-|$)/;
const CONTROL =
  /^(?:p[trblxy]?|h|min-h|size|rounded|gap|bg|border|font|leading|tracking|type)(?:-|$)|^text-(?!left$|right$|center$|justify$|wrap$|nowrap$|balance$|pretty$)|^\[(?:font|padding|height|border|color)/;
const GEOMETRY = /^(?:p[trblxy]?|gap|space-[xy])(?:-|$)/;
const CONTRACTS = {
  Card: SURFACE,
  CollapsibleCard: SURFACE,
  Button: CONTROL,
  IconButton: CONTROL,
  Input: CONTROL,
  Textarea: CONTROL,
  SearchInput: CONTROL,
  ModalHeader: GEOMETRY,
  ModalBody: GEOMETRY,
  ModalFooter: GEOMETRY,
  SettingRow: GEOMETRY,
  StatTile: SURFACE,
  PageFrame: GEOMETRY,
  SectionStack: GEOMETRY,
  Badge: CONTROL,
  Notice: CONTROL,
  TabList: CONTROL,
  SegmentedControl: CONTROL,
  Select: CONTROL,
  SelectableCard: CONTROL,
  Switch: CONTROL,
  HelpHint: CONTROL,
  Spinner: CONTROL,
  Modal: GEOMETRY,
};

function modulePath(from, specifier) {
  const base = specifier.startsWith('@/')
    ? resolve(process.cwd(), 'src', specifier.slice(2))
    : resolve(dirname(from), specifier);
  return [base, `${base}.ts`, `${base}.tsx`, `${base}/index.ts`].find(
    (path) => existsSync(path) && statSync(path).isFile(),
  );
}

function importedStrings(from, specifier, name, seen, mode = 'strings', members = []) {
  if (!specifier.startsWith('.') && !specifier.startsWith('@/')) return [];
  const path = modulePath(from, specifier);
  const key = `${path}:${name}:${mode}:${members.join('.')}`;
  if (!path || seen.has(key)) return [];
  seen.add(key);
  const source = ts.createSourceFile(
    path,
    readFileSync(path, 'utf8'),
    ts.ScriptTarget.Latest,
    true,
  );
  const definitions = new Map();
  const bindings = new Map();
  const reexports = [];
  source.forEachChild((statement) => {
    if (ts.isVariableStatement(statement)) {
      for (const declaration of statement.declarationList.declarations) {
        if (ts.isIdentifier(declaration.name))
          definitions.set(declaration.name.text, declaration.initializer);
      }
    }
    if (ts.isImportDeclaration(statement)) {
      const named = statement.importClause?.namedBindings;
      if (named && ts.isNamedImports(named)) {
        for (const binding of named.elements) {
          bindings.set(binding.name.text, {
            specifier: statement.moduleSpecifier.text,
            name: (binding.propertyName ?? binding.name).text,
          });
        }
      }
    }
    if (ts.isExportDeclaration(statement) && statement.moduleSpecifier) {
      if (!statement.exportClause) {
        reexports.push({ specifier: statement.moduleSpecifier.text, name });
      } else if (ts.isNamedExports(statement.exportClause)) {
        for (const binding of statement.exportClause.elements) {
          if (binding.name.text === name) {
            reexports.push({
              specifier: statement.moduleSpecifier.text,
              name: (binding.propertyName ?? binding.name).text,
            });
          }
        }
      }
    }
  });
  const imported = (binding, kind, selected = []) =>
    importedStrings(path, binding.specifier, binding.name, seen, kind, selected);
  const collect = (node, visited = new Set()) => {
    if (!node || visited.has(node)) return [];
    visited.add(node);
    if (ts.isStringLiteralLike(node)) return [node.text];
    if (ts.isIdentifier(node)) {
      const binding = bindings.get(node.text);
      return binding ? imported(binding, 'strings') : collect(definitions.get(node.text), visited);
    }
    if (ts.isPropertyAccessExpression(node))
      return select(node.expression, [node.name.text], 'strings');
    if (ts.isTemplateExpression(node)) {
      return [
        node.head.text,
        ...node.templateSpans.flatMap((span) => [
          ...collect(span.expression, visited),
          span.literal.text,
        ]),
      ];
    }
    const values = [];
    node.forEachChild((child) => {
      values.push(...collect(child, visited));
    });
    return values;
  };
  const collectAttributes = (node, visited = new Set()) => {
    if (!node || visited.has(node)) return [];
    visited.add(node);
    if (ts.isIdentifier(node)) {
      const binding = bindings.get(node.text);
      return binding
        ? imported(binding, 'attributes')
        : collectAttributes(definitions.get(node.text), visited);
    }
    if (
      ts.isAsExpression(node) ||
      ts.isSatisfiesExpression(node) ||
      ts.isParenthesizedExpression(node)
    )
      return collectAttributes(node.expression, visited);
    if (!ts.isObjectLiteralExpression(node)) return [];
    return node.properties.flatMap((property) => {
      if (ts.isSpreadAssignment(property)) return collectAttributes(property.expression, visited);
      if (!ts.isPropertyAssignment(property) && !ts.isShorthandPropertyAssignment(property))
        return [];
      const alternatives = collect(
        ts.isPropertyAssignment(property) ? property.initializer : property.name,
      );
      return [
        {
          name: property.name.text,
          value: {
            type: 'ArrayExpression',
            elements: alternatives.map((value) => ({ type: 'Literal', value })),
          },
        },
      ];
    });
  };
  const select = (node, selected, kind, visited = new Set()) => {
    if (!node || visited.has(node)) return [];
    if (!selected.length) return kind === 'attributes' ? collectAttributes(node) : collect(node);
    visited.add(node);
    if (
      ts.isAsExpression(node) ||
      ts.isSatisfiesExpression(node) ||
      ts.isParenthesizedExpression(node)
    )
      return select(node.expression, selected, kind, visited);
    if (ts.isIdentifier(node)) {
      const binding = bindings.get(node.text);
      return binding
        ? imported(binding, kind, selected)
        : select(definitions.get(node.text), selected, kind, visited);
    }
    if (!ts.isObjectLiteralExpression(node)) return [];
    const property = node.properties.find((entry) => entry.name?.text === selected[0]);
    return property && ts.isPropertyAssignment(property)
      ? select(property.initializer, selected.slice(1), kind, visited)
      : [];
  };
  const definition = definitions.get(name);
  const binding = bindings.get(name);
  if (definition) return select(definition, members, mode);
  if (binding) return imported(binding, mode, members);
  return reexports.flatMap((entry) => imported(entry, mode, members));
}

/** @type {import('eslint').Rule.RuleModule} */
export const componentContracts = {
  meta: {
    type: 'problem',
    schema: [],
    messages: {
      override:
        '{{component}} owns {{classes}}. Select a typed variant or edit its central settings; className is for surrounding layout.',
      primitive: 'Use the shared {{component}} so a central change reaches this control.',
    },
  },
  create(context) {
    const source = context.sourceCode;
    const filename = context.filename.replaceAll('\\', '/');
    if (/\/shared\/(?:ui|design-system)\//.test(filename) || /\.test\./.test(filename)) return {};
    const imports = new Map();
    const resolveVariable = (node) => {
      for (let scope = source.getScope(node); scope; scope = scope.upper) {
        const variable = scope.set.get(node.name);
        if (variable) return variable.defs[0];
      }
      return undefined;
    };
    const strings = (node, seen = new Set()) => {
      if (!node || seen.has(node)) return [];
      seen.add(node);
      if (node.type === 'Literal') return typeof node.value === 'string' ? [node.value] : [];
      if (node.type === 'JSXExpressionContainer') return strings(node.expression, seen);
      if (node.type === 'TemplateLiteral')
        return [
          ...node.quasis.map((part) => part.value.raw),
          ...node.expressions.flatMap((part) => strings(part, seen)),
        ];
      if (node.type === 'Identifier') {
        const definition = resolveVariable(node);
        if (definition?.type === 'Variable') return strings(definition.node.init, seen);
        if (definition?.type === 'ImportBinding' && definition.node.type === 'ImportSpecifier') {
          return importedStrings(
            resolve(context.cwd, filename),
            definition.parent.source.value,
            definition.node.imported.name,
            new Set(),
          );
        }
        return [];
      }
      if (node.type === 'MemberExpression') {
        if (node.object.type === 'Identifier') {
          const definition = resolveVariable(node.object);
          const key = node.computed ? node.property.value : node.property.name;
          if (
            definition?.type === 'ImportBinding' &&
            definition.node.type === 'ImportSpecifier' &&
            typeof key === 'string'
          )
            return importedStrings(
              resolve(context.cwd, filename),
              definition.parent.source.value,
              definition.node.imported.name,
              new Set(),
              'strings',
              [key],
            );
          if (
            definition?.type === 'ImportBinding' &&
            definition.node.type === 'ImportNamespaceSpecifier' &&
            typeof key === 'string'
          )
            return importedStrings(
              resolve(context.cwd, filename),
              definition.parent.source.value,
              key,
              new Set(),
            );
        }
        const object =
          node.object.type === 'Identifier' ? resolveVariable(node.object)?.node.init : node.object;
        const key = node.computed ? node.property.value : node.property.name;
        if (object?.type === 'ObjectExpression' && typeof key === 'string') {
          const property = object.properties.find(
            (entry) => entry.type === 'Property' && (entry.key.name ?? entry.key.value) === key,
          );
          return strings(property?.value, seen);
        }
        return strings(node.object, seen);
      }
      if (node.type === 'ConditionalExpression')
        return [...strings(node.consequent, seen), ...strings(node.alternate, seen)];
      if (node.type === 'LogicalExpression' || node.type === 'BinaryExpression')
        return [...strings(node.left, seen), ...strings(node.right, seen)];
      if (node.type === 'CallExpression')
        return node.arguments.flatMap((argument) => strings(argument, seen));
      if (node.type === 'ArrayExpression')
        return node.elements.flatMap((element) => strings(element, seen));
      if (node.type === 'ObjectExpression')
        return node.properties.flatMap((property) =>
          property.type === 'Property'
            ? [...strings(property.key, seen), ...strings(property.value, seen)]
            : strings(property.argument, seen),
        );
      if (node.type === 'TSAsExpression' || node.type === 'TSSatisfiesExpression')
        return strings(node.expression, seen);
      return [];
    };
    const attributes = (node, seen = new Set()) => {
      if (!node || seen.has(node)) return [];
      seen.add(node);
      if (node.type === 'Identifier') {
        const definition = resolveVariable(node);
        if (definition?.type === 'ImportBinding' && definition.node.type === 'ImportSpecifier')
          return importedStrings(
            resolve(context.cwd, filename),
            definition.parent.source.value,
            definition.node.imported.name,
            new Set(),
            'attributes',
          );
        return definition?.type === 'Variable' ? attributes(definition.node.init, seen) : [];
      }
      if (node.type === 'ObjectExpression') {
        return node.properties.flatMap((property) => {
          if (property.type === 'SpreadElement') return attributes(property.argument, seen);
          return [{ name: property.key.name ?? property.key.value, value: property.value }];
        });
      }
      if (node.type === 'TSAsExpression' || node.type === 'TSSatisfiesExpression')
        return attributes(node.expression, seen);
      return [];
    };
    return {
      ImportDeclaration(node) {
        if (!/(?:shared\/ui|^\.\/)/.test(node.source.value)) return;
        for (const specifier of node.specifiers) {
          if (specifier.type === 'ImportNamespaceSpecifier') imports.set(specifier.local.name, '*');
          if (specifier.type === 'ImportSpecifier' && CONTRACTS[specifier.imported.name])
            imports.set(specifier.local.name, specifier.imported.name);
        }
      },
      JSXOpeningElement(node) {
        if (node.name.type !== 'JSXIdentifier' && node.name.type !== 'JSXMemberExpression') return;
        const exception = source
          .getAllComments()
          .some(
            (comment) =>
              comment.range[1] <= node.range[0] &&
              comment.loc.end.line >= node.loc.start.line - 2 &&
              /design-system-exception:\s+\S.{9,}/.test(comment.value),
          );
        if (exception) return;
        const resolvedAttrs = node.attributes.flatMap((attribute) =>
          attribute.type === 'JSXSpreadAttribute'
            ? attributes(attribute.argument)
            : [{ name: attribute.name.name, value: attribute.value }],
        );
        // JSX spreads obey last-write precedence just like native object spreads.
        const attrs = [
          ...new Map(resolvedAttrs.map((attribute) => [attribute.name, attribute])).values(),
        ];
        const component =
          node.name.type === 'JSXMemberExpression'
            ? imports.get(node.name.object.name) === '*' && CONTRACTS[node.name.property.name]
              ? node.name.property.name
              : undefined
            : imports.get(node.name.name);
        const classes = attrs
          .filter((attribute) =>
            /^(?:className|wrapperClassName|headerClassName|bodyClassName)$/.test(attribute.name),
          )
          .flatMap((attribute) => strings(attribute.value))
          .flatMap((text) => text.split(/\s+/))
          .map((name) => name.split(':').at(-1))
          .filter(Boolean);
        if (component) {
          const forbidden = [...new Set(classes.filter((name) => CONTRACTS[component].test(name)))];
          if (forbidden.length)
            context.report({
              node,
              messageId: 'override',
              data: { component, classes: forbidden.join(', ') },
            });
        }
        if (
          node.name.name === 'button' &&
          classes.includes('rounded-full') &&
          classes.some((name) => /^px-/.test(name))
        )
          context.report({ node, messageId: 'primitive', data: { component: 'Button' } });
        if (node.name.name === 'input') {
          const type = attrs.find((attribute) => attribute.name === 'type');
          const value = strings(type?.value)[0] ?? 'text';
          if (!['file', 'checkbox', 'radio', 'range', 'hidden'].includes(value))
            context.report({ node, messageId: 'primitive', data: { component: 'Input' } });
        }
        if (node.name.name === 'textarea')
          context.report({ node, messageId: 'primitive', data: { component: 'Textarea' } });
      },
    };
  },
};
