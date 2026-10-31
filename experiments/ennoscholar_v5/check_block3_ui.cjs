// Offline SSR of the V5 budget control; no app/server, authentication or real data.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const Module = require('node:module');
const root = path.resolve(__dirname, '../..');
const deps = name => require(path.join(root, 'frontend/node_modules', name));
const React = deps('react');
const server = deps('react-dom/server');
const ts = deps('typescript');
const file = path.join(root, 'frontend/components/ennosmart/v5/session-budget.tsx');
const output = ts.transpileModule(fs.readFileSync(file, 'utf8'), {
  compilerOptions: {jsx: ts.JsxEmit.ReactJSX, module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020},
  fileName: file, reportDiagnostics: true
});
assert.equal(output.diagnostics.length, 0);
const mod = new Module(file, module);
mod.filename = file;
mod.require = name => {
  if (name === 'react') return React;
  if (name === 'react/jsx-runtime') return deps('react/jsx-runtime');
  if (name === '@/components/ui/button') return {Button: ({variant, ...props}) => React.createElement('button', props)};
  if (name === '@/lib/scholar-v5') return {
    getResourceBudget: () => {throw Error('No network from offline SSR');},
    setResourceBudget: () => {throw Error('No mutation from offline SSR');}
  };
  throw Error('Unexpected import: ' + name);
};
mod._compile(output.outputText, file);
const value = {limit_usd:null, revision:0, committed_usd:.15, confirmed_usd:.1,
  reserved_usd:.03, uncertain_usd:.02, remaining_usd:null, tokens:300};
function render(budget) {
  return server.renderToStaticMarkup(React.createElement(mod.exports.default, {
    sid:'offline-session', value:budget, onChanged:async()=>{}
  }));
}
let html = render(value);
assert.match(html, /sans plafond de conversation/);
assert.match(html, /confirmés.*réservés.*incertains/);
assert.match(html, /for="v5-session-budget"/);
assert.match(html, /aria-describedby="v5-budget-help v5-budget-error"/);
assert.match(html, /Enregistrer le plafond/);
html = render({...value, limit_usd:.5});
assert.doesNotMatch(html, /sans plafond de conversation/);
assert.match(html, /value="0.5"/);
assert.match(html, /0,5000/);
console.log('V5 session budget: offline SSR passed (uncapped + explicit cap, labels, financial categories).');
