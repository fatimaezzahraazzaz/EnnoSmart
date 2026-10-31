// Render the ACTUAL chat Response + Markdown components from exported ASGI data.
// No frontend source is edited, no server/browser is started and all network is blocked.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {pathToFileURL} = require('node:url');
const Module = require('node:module');
const root = path.resolve(__dirname, '../..');
const deps = name => require(path.join(root, 'frontend/node_modules', name));
const React = deps('react');
const server = deps('react-dom/server');
const ts = deps('typescript');

(async () => {
  const markdown = await import(pathToFileURL(path.join(root,'frontend/node_modules/react-markdown/index.js')).href);
  const gfm = await import(pathToFileURL(path.join(root,'frontend/node_modules/remark-gfm/index.js')).href);
  const file = path.join(root,'frontend/components/ennosmart/v5/ennoscholar-v4-chat.tsx');
  const compiled = ts.transpileModule(fs.readFileSync(file,'utf8'), {
    compilerOptions:{jsx:ts.JsxEmit.ReactJSX,module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2020,esModuleInterop:true},
    fileName:file,reportDiagnostics:true
  });
  assert.equal(compiled.diagnostics.length,0);
  const mod = new Module(file,module);mod.filename=file;
  mod.require = name => {
    if (name==='react') return React;
    if (name==='react/jsx-runtime') return deps('react/jsx-runtime');
    if (name==='react-markdown') return {__esModule:true,default:markdown.default};
    if (name==='remark-gfm') return {__esModule:true,default:gfm.default};
    if (name==='next/dynamic') return {__esModule:true,default:()=>()=>null};
    if (name==='lucide-react') return new Proxy({}, {get:()=>()=>null});
    if (name==='@/components/ui/button') return {Button:({variant,size,...props})=>React.createElement('button',props)};
    if (name==='@/lib/scholar-v5') return {safeUrl:value=>/^https?:\/\//.test(value)?value:null};
    if (name.startsWith('./')) return {__esModule:true,default:()=>null};
    throw Error('Unexpected dependency: '+name);
  };
  // Expose a local component to the test VM only. No production export is added.
  mod._compile(compiled.outputText+'\nmodule.exports.TestResponse = Response;',file);
  const cases=JSON.parse(fs.readFileSync(path.join(__dirname,'.artifacts/s2-ui-cases.json'),'utf8'));
  assert.ok(Object.keys(cases).length>=20,'Run the S2 ASGI tests before the renderer');
  for (const [name,item] of Object.entries(cases)) {
    assert.ok(item.message, name+': no message in the API conversation');
    const html=server.renderToStaticMarkup(React.createElement(mod.exports.TestResponse,{
      message:item.message,sessionId:'offline',onExplore:()=>{},onChanged:async()=>{}
    }));
    const text=html.replace(/<[^>]*>/g,' ').replace(/&amp;/g,'&').replace(/&#x27;|&#39;/g,"'").replace(/&quot;/g,'"')
      .replace(/\s+/g,' ').toLocaleLowerCase('fr');
    for (const fragment of item.expected) assert.ok(text.includes(fragment.toLocaleLowerCase('fr')),name+': missing '+fragment);
    assert.match(html,/aria-label="Réponse EnnoScholar"/);
  }
  console.log('S2: '+Object.keys(cases).length+' API messages visible in actual chat Response + ReactMarkdown (offline SSR).');
})().catch(error=>{console.error(error);process.exitCode=1;});
