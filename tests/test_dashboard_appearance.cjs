// Offline: execute production appearance and core handlers, without a browser or backend.
const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const source = fs.readFileSync(path.join(__dirname,'../UI/appearance.js'),'utf8');
const jarvis = fs.readFileSync(path.join(__dirname,'../UI/jarvis.js'),'utf8');
function fixture(value, denied=false) {
  const nodes = new Map(), events = {}, windowEvents = {}, store = new Map(value ? [['jarvis.appearance.v2',value]] : []);
  const $ = key => { if(!nodes.has(key)) nodes.set(key,{dataset:{},textContent:'',value:'',handlers:{},attributes:{},addEventListener(k,fn){this.handlers[k]=fn;},setAttribute(k,v){this.attributes[k]=v;}});return nodes.get(key); };
  const document={documentElement:{dataset:{}},visibilityState:'visible',querySelector:$,addEventListener:(k,fn)=>events[k]=fn};
  const context=vm.createContext({document,window:{addEventListener:(k,fn)=>windowEvents[k]=fn},localStorage:{getItem(k){if(denied)throw Error('Denied');return store.get(k);},setItem(k,v){if(denied)throw Error('Denied');store.set(k,v);}},$,live:{online:true,state:{name:'wake_word_only'},tasks:[]},setTimeout,clearTimeout});
  vm.runInContext(source,context);events.DOMContentLoaded();
  return {nodes,$,events,windowEvents,store,document,context};
}
test('Classic (light) default is persisted before paint; HUD restores and switches immediately without network',()=>{
  const f=fixture();assert.equal(f.document.documentElement.dataset.appearance,'classic');assert.equal(f.store.get('jarvis.appearance.v2'),'classic');
  f.$('#appearance-shortcut').handlers.click();assert.equal(f.document.documentElement.dataset.appearance,'hud');assert.equal(f.$('#appearance-mode').value,'hud');
  assert.match(f.$('#appearance-shortcut').attributes['aria-label'],/Switch to Classic/);
  const restored=fixture(f.store.get('jarvis.appearance.v2'));assert.equal(restored.document.documentElement.dataset.appearance,'hud');
  restored.$('#appearance-mode').handlers.change({target:{value:'classic'}});assert.equal(restored.store.get('jarvis.appearance.v2'),'classic');
  restored.$('#appearance-mode').handlers.change({target:{value:'invalid'}});assert.equal(restored.document.documentElement.dataset.appearance,'classic');
});
test('storage denied still applies appearance and reports unsaved preference accurately',()=>{
  const f=fixture(undefined,true);f.$('#appearance-shortcut').handlers.click();assert.equal(f.document.documentElement.dataset.appearance,'hud');assert.match(f.$('#appearance-status').textContent,/could not be saved/);
});
test('cross-window changes sync controls; hidden documents pause animation and recover',()=>{
  const f=fixture();f.windowEvents.storage({key:'jarvis.appearance.v2',newValue:'hud'});assert.equal(f.$('#appearance-mode').value,'hud');
  f.windowEvents.storage({key:'unrelated',newValue:'classic'});assert.equal(f.$('#appearance-mode').value,'hud');
  f.document.visibilityState='hidden';f.events.visibilitychange();assert.equal(f.document.documentElement.dataset.motion,'paused');
  f.document.visibilityState='visible';f.events.visibilitychange();assert.equal(f.document.documentElement.dataset.motion,'active');
});
function core(f) {
  const start=jarvis.indexOf('  function renderCore('), end=jarvis.indexOf('  // ---------------------------------------------------------------- home',start);
  vm.runInContext(jarvis.slice(start,end),f.context);
}
test('core uses real running tasks, prioritizes actual voice state, clears stale feedback on disconnect',()=>{
  const f=fixture();core(f);const run=s=>vm.runInContext(s,f.context);
  run('renderCore("wake_word_only")');assert.equal(f.$('.hero-art').dataset.phase,'wake_word_only');
  f.context.live.tasks=[{state:'RUNNING'}];run('renderCore("wake_word_only")');assert.equal(f.$('.hero-art').dataset.phase,'executing');
  for(const phase of ['listening','speaking','processing','error','interrupted','quiet']) {run(`renderCore("${phase}")`);assert.equal(f.$('.hero-art').dataset.phase,phase);}
  run('coreFeedback("completed");renderCore("disconnected")');assert.equal(f.$('.hero-art').dataset.phase,'disconnected');assert.equal(f.$('.hero-art').dataset.feedback,undefined);
  run('clearTimeout(coreFeedbackTimer)');
});
test('accepted and unknown requests never trigger success; confirmed feedback expires and respects offline',()=>{
  const f=fixture();core(f);let expire;f.context.setTimeout=fn=>{expire=fn;return 1;};f.context.clearTimeout=()=>{};
  for(const outcome of ['accepted','unknown']) vm.runInContext(`coreFeedback("${outcome}")`,f.context);
  assert.equal(f.$('.hero-art').dataset.feedback,undefined);
  vm.runInContext('coreFeedback("completed")',f.context);assert.equal(f.$('.hero-art').dataset.feedback,'completed');expire();assert.equal(f.$('.hero-art').dataset.feedback,undefined);
  vm.runInContext('coreFeedback("failed")',f.context);assert.equal(f.$('.hero-art').dataset.feedback,'failed');expire();
  f.context.live.online=false;vm.runInContext('coreFeedback("completed")',f.context);assert.equal(f.$('.hero-art').dataset.feedback,undefined);
});
test('a pending frontend request does not fabricate the backend thinking state; CSS reduces motion',()=>{
  const f=fixture();f.context.linkState='connected';f.context.pending='draft';
  const start=jarvis.indexOf('  function stateKey()'),end=jarvis.indexOf('  function renderPresence()',start);
  vm.runInContext(jarvis.slice(start,end),f.context);assert.equal(vm.runInContext('stateKey()',f.context),'wake_word_only');
  const css=fs.readFileSync(path.join(__dirname,'../UI/hud.css'),'utf8');assert.match(css,/prefers-reduced-motion:reduce/);assert.match(css,/data-motion="paused"/);
});
test('full Jarvis mode toggles by core, exits by Escape and navigation, and restores focus',()=>{
  const f=fixture(), classes=new Set();
  f.document.documentElement.classList={contains:k=>classes.has(k),toggle:(k,on)=>on?classes.add(k):classes.delete(k)};
  const coreButton=f.$('#jarvis-expand'), text={textContent:''}; let focused=0;
  coreButton.querySelector=()=>text;coreButton.focus=()=>focused++;
  vm.runInContext(fs.readFileSync(path.join(__dirname,'../UI/presence.js'),'utf8'),f.context);
  coreButton.handlers.click();assert.ok(classes.has('jarvis-full'));assert.equal(coreButton.attributes['aria-pressed'],'true');assert.equal(f.$('#jarvis-exit').hidden,false);
  coreButton.handlers.click();assert.ok(!classes.has('jarvis-full'));assert.equal(f.$('#jarvis-exit').hidden,true);
  coreButton.handlers.click();f.events.keydown({key:'Escape'});assert.ok(!classes.has('jarvis-full'));assert.equal(focused,1);
  coreButton.handlers.click();f.events['jarvis:leave']();assert.ok(!classes.has('jarvis-full'));
  coreButton.handlers.click();f.$('#jarvis-exit').handlers.click();assert.equal(focused,2);
});
test('direct cinematic link opens full mode after renderer readiness in a fresh browser',()=>{
  const f=fixture(),classes=new Set();f.document.documentElement.classList={contains:k=>classes.has(k),toggle:(k,on)=>on?classes.add(k):classes.delete(k)};
  f.$('#jarvis-expand').querySelector=()=>({textContent:''});f.context.URLSearchParams=URLSearchParams;f.context.location={search:'?view=jarvis'};
  let enabled=false;f.context.window.JarvisHUD={enable:value=>enabled=value};
  vm.runInContext(fs.readFileSync(path.join(__dirname,'../UI/presence.js'),'utf8'),f.context);
  assert.equal(enabled,false);f.windowEvents['jarvis:hud-ready']();assert.equal(enabled,true);assert.ok(classes.has('jarvis-full'));
  f.context.location.search='';classes.clear();enabled=false;f.windowEvents['jarvis:hud-ready']();assert.equal(enabled,false);
});
test('Full Jarvis layout does not depend on the selected appearance; brand assets are wired',()=>{
  const brand=fs.readFileSync(path.join(__dirname,'../UI/brand.css'),'utf8'), html=fs.readFileSync(path.join(__dirname,'../UI/index.html'),'utf8');
  assert.match(brand,/html\.jarvis-full \.assistant-console \{[^}]*display:flex;[^}]*flex-direction:column/);
  assert.doesNotMatch(brand,/html\[data-appearance="classic"\] \.assistant-console/,'light console styles must exclude full mode');
  assert.ok(html.indexOf('workspace.css')<html.indexOf('brand.css')&&html.indexOf('brand.css')<html.indexOf('cinematic.css'));
  assert.ok(fs.existsSync(path.join(__dirname,'../UI/brand/zend-logo.png')));
  assert.doesNotMatch(source,/"jarvis\.appearance"/,'the auto-saved legacy key must not override the light default');
});
