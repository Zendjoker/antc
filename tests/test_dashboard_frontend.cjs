// Offline regression tests: execute the production handlers in an isolated DOM/transport fixture.
// Run: node --test tests/test_dashboard_frontend.cjs (no packages, services or credentials).
const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const app = fs.readFileSync(path.join(__dirname, '../UI/app.js'), 'utf8');
const jarvis = fs.readFileSync(path.join(__dirname, '../UI/jarvis.js'), 'utf8');
const slice = (source, start, end) => source.slice(source.indexOf(start), source.indexOf(end, source.indexOf(start)));
const deferred = () => { let resolve, reject; const promise = new Promise((a, b) => { resolve = a; reject = b; }); return { promise, resolve, reject }; };
const response = (data, status = 200) => ({ ok: status < 400, status, json: async () => data });

class Element {
  constructor(tag = 'div') {
    this.tagName = tag.toUpperCase(); this.children = []; this.dataset = {}; this.handlers = {}; this.attributes = {};
    this.disabled = false; this.checked = false; this.style = {}; this._value = ''; this.textContent = '';
    this.classes = new Set();
    this.classList = { add: x => this.classes.add(x), remove: x => this.classes.delete(x), contains: x => this.classes.has(x), toggle: (x, on) => { on ??= !this.classes.has(x); on ? this.classes.add(x) : this.classes.delete(x); } };
  }
  append(...nodes) { this.children.push(...nodes); }
  appendChild(node) { this.append(node); }
  prepend(node) { this.children.unshift(node); }
  set innerHTML(value) { this.children = []; }
  get innerHTML() { return ''; }
  get options() { return this.children.flatMap(x => x.tagName === 'OPTGROUP' ? x.children : x.tagName === 'OPTION' ? [x] : []); }
  get value() { return this.tagName === 'SELECT' ? (this.options.find(x => x.selected) || this.options[0])?.value || '' : this._value; }
  set value(v) { this._value = v; if (this.tagName === 'SELECT') this.options.forEach(x => { x.selected = x.value === v; }); }
  get selectedOptions() { return this.options.filter(x => x.value === this.value); }
  querySelectorAll(selector) { return this.children.flatMap(x => x instanceof Element ? [...(selector.split(',').map(s => s.trim().toUpperCase()).includes(x.tagName) ? [x] : []), ...x.querySelectorAll(selector)] : []); }
  querySelector(selector) { return this.querySelectorAll(selector)[0] || null; }
  addEventListener(type, fn) { this.handlers[type] = fn; }
  setAttribute(k, v) { this.attributes[k] = v; }
  dispatchEvent(e) { return this.handlers[e.type]?.(e); }
  remove() {} focus() {} scrollTo() {}
}
function fixture() {
  const nodes = new Map();
  const $ = id => { if (!nodes.has(id)) nodes.set(id, new Element(id.includes('select') ? 'select' : 'div')); return nodes.get(id); };
  const toasts = [];
  const c = vm.createContext({ $, document: { querySelector: $, createElement: tag => new Element(tag), querySelectorAll: () => [], createTextNode: text => text },
    window: {}, fetch: async () => response({}), AbortController, setTimeout, clearTimeout, setInterval: () => {}, Event: class { constructor(type) { this.type = type; } },
    Date, console, confirm: () => true, encodeURIComponent,
    toast: (...args) => toasts.push(args), el: (tag, cls, text) => { const n = new Element(tag); n.className = cls; n.textContent = text; return n; },
    render: () => {}, coreFeedback: () => {}, scrollFeed: () => {}, show: () => {}, grow: () => {}, ico: () => new Element(), input: $('#chat-input'), live: { online: true }, pending: null, feedSig: '', chatError: '', uncertainCommand: '', latestCommandReply: '', latestReplyBaseline:'null' });
  return { c, $, nodes, toasts, run: code => vm.runInContext(code, c) };
}
function transport(f) { f.run(slice(app, 'async function requestJSON', 'function $(sel')); }
function actions(f) { transport(f); f.run(slice(jarvis, '  const STATES', '  function render()')); }
function settings(f) { f.run(app.slice(0, app.lastIndexOf('\nloadStatus().catch'))); }
function voice(f) { f.run(slice(jarvis, '  let voiceCatalogue', '  function renderMedia')); }
function missions(f) { f.run('let missionId="A", missionAt=0, missionRequest=0, missionDrawn="", missionSig="";'); f.run(slice(jarvis, '  async function loadMission()', '  function list(')); }

test('action honesty: malformed/unexpected JSON, rejected body and unsuccessful HTTP never become success', async () => {
  const f = fixture(); actions(f);
  for (const r of [response(null), response({}), response([]), { ok: true, status: 200, json: async () => { throw Error('HTML'); } }, response({ ok: false, message: 'No' }), response({ ok: true, message: 'Yes' }, 403)]) {
    f.c.fetch = async () => r;
    assert.equal((await f.run('api("/api/action", {})')).ok, false);
  }
  f.c.fetch = async () => response({ ok: true }, 202);
  assert.equal((await f.run('api("/api/action", {})')).outcome, 'accepted');
  f.c.fetch = async () => response({ reply: '' });
  assert.equal((await f.run('api("/api/command", {})')).outcome, 'completed');
});
test('request deadline includes response parsing; late completion is not consumed', async () => {
  const f = fixture(); transport(f); const body = deferred(); let signal;
  f.c.fetch = async (_, options) => { signal = options.signal; return { ok: true, status: 200, json: () => body.promise }; };
  await assert.rejects(f.run('requestJSON("/test", {}, 5)'), /timed out/);
  assert.equal(signal.aborted, true); body.resolve({ ok: true });
});
test('identical repeated action clicks share one flight and do not display Done for bad data', async () => {
  const f = fixture(); actions(f); const d = deferred(); let calls = 0;
  f.c.requestJSON = async path => { if (path === '/api/live') return { data: { online: false } }; calls++; return d.promise; };
  const a = f.run('act({do:"timer"})'), b = f.run('act({do:"timer"})');
  d.resolve({ data: {}, status: 200 }); await Promise.all([a,b]);
  assert.equal(calls, 1); assert.equal(f.toasts[0][1], 'err'); assert.doesNotMatch(f.toasts[0][0], /Done/);
});
test('chat retains unavailable/failed drafts, guards duplicates, and preserves edits made while sending', async () => {
  const f = fixture(); f.run(slice(jarvis, '  async function send(', '  const input =')); f.c.render = () => {}; f.c.poll = async () => {};
  const source = f.$('#chat-input'); source.value = 'original'; f.c.source = source;
  f.c.live = { online: false }; await f.run('send(source.value, false, source)'); assert.equal(source.value, 'original');
  f.c.live.online = true; const d = deferred(); let calls=0; f.c.api = () => { calls++; return d.promise; };
  const sending = f.run('send(source.value, false, source)'); await f.run('send(source.value, false, source)');
  assert.equal(calls, 1); source.value = 'new draft'; d.resolve({ ok: true, outcome: 'completed' }); await sending;
  assert.equal(source.value, 'new draft');
  f.c.api = async () => ({ ok: false, outcome: 'unknown', message: 'Outcome unknown' });
  await f.run('send(source.value, false, source)'); assert.equal(source.value, 'new draft'); assert.match(f.run('chatError'), /retained/);
  f.c.confirm = () => false; await f.run('send(source.value, false, source)'); assert.equal(source.value, 'new draft');
});
test('confirmed chat clears only the submitted draft; both form handlers pass their source without clearing', async () => {
  const f = fixture(); f.run(slice(jarvis, '  async function send(', '  const input =')); f.c.poll=async()=>{}; f.c.api=async()=>({ok:true,outcome:'completed'});
  f.c.source=f.$('#chat-input'); f.c.source.value='draft'; await f.run('send(source.value, false, source)'); assert.equal(f.c.source.value,'');
  for (const id of ['home-ask','chat-form']) {
    const line = jarvis.split('\n').find(x => x.includes(`$("#${id}").addEventListener("submit"`));
    let sent; f.c.send=(...args)=>{sent=args;}; f.$('#home-input').value='home draft'; f.$('#chat-input').value='chat draft';
    f.run(line); f.$('#'+id).handlers.submit({preventDefault(){}});
    assert.equal(sent[2].value, sent[0]);
  }
});
test('settings save preserves newer edits, prevents duplicate saves and supports reverting during save', async () => {
  const f=fixture(); settings(f); f.run('settingsBaseline.A="old";'); const input=f.$('#setting-A'); f.c.field=input;
  f.run('markDirty(field,"A","first")'); const d=deferred(); let calls=0; f.c.requestJSON=()=>{calls++;return d.promise;};
  const handler=f.$('#save-btn').handlers.click; const saving=handler(); f.run('markDirty(field,"A","old")'); await handler(); assert.equal(calls,1);
  f.run('settingsBaseline.B="original"; markDirty(field,"B","edit"); markDirty(field,"B","original")');
  assert.equal(f.run('"B" in changes'),false);
  d.resolve({data:{ok:true,changed:['A']}}); await saving;
  assert.equal(f.run('changes.A'),'old'); assert.equal(f.$('#save-btn').disabled,false);
  f.c.requestJSON=async()=>({data:{ok:true,changed:['A']}}); await handler(); assert.equal(f.run('Object.keys(changes).length'),0);
  f.run('markDirty(field,"A","old")'); assert.equal(f.run('Object.keys(changes).length'),0);
});
test('settings failure retains drafts and stale reload cannot replace edits', async () => {
  const f=fixture(); settings(f); const d=deferred(); f.c.requestJSON=()=>d.promise;
  const loading=f.run('loadSettings()'); f.c.field=f.$('#setting-A'); f.run('markDirty(field,"A","draft")');
  d.resolve({data:{sections:[]}}); await loading; assert.equal(f.run('changes.A'),'draft');
  f.c.requestJSON=async()=>({data:{ok:false}}); await f.$('#save-btn').handlers.click();
  assert.equal(f.run('changes.A'),'draft'); assert.match(f.$('#banner').textContent,/retained/); assert.equal(f.$('#save-btn').disabled,false);
});
test('polls are serialized; post-action refresh waits then loads a fresh snapshot; malformed/disconnected states differ', async () => {
  const f=fixture(); actions(f); const d=deferred(); let calls=0;
  f.c.requestJSON=()=>{calls++;return calls===1?d.promise:Promise.resolve({data:{online:true,state:{name:'speaking'}}});};
  const old=f.run('poll()'), overlap=f.run('poll()'), fresh=f.run('poll(true)'); assert.equal(calls,1);
  d.resolve({data:{online:true,state:{name:'listening'}}}); await Promise.all([old,overlap,fresh]);
  assert.equal(calls,2); assert.equal(f.run('live.state.name'),'speaking');
  f.c.requestJSON=async()=>({data:{online:false}}); await f.run('poll()'); assert.equal(f.run('linkState'),'connected');
  f.c.requestJSON=async()=>({data:{online:'yes'}}); await f.run('poll()'); assert.equal(f.run('linkState'),'disconnected'); assert.equal(f.run('live.online'),false);
});
const catalogue = {provider:'elevenlabs',selected:'a',name:'Thomas',enabled:true,providers:[{id:'elevenlabs',name:'ElevenLabs',voices:[{id:'a',name:'Thomas',description:'Calm'},{id:'b',name:'George',description:'Warm'}]}]};
test('voice acknowledgement is not active confirmation; provider AND id must match; offline wording is honest', async () => {
  const f=fixture(); voice(f); f.c.catalogue=catalogue; f.run('voiceCatalogue=catalogue; live={online:true,voice_settings:catalogue}; renderVoice();');
  f.$('#voice-select').value='b'; f.$('#voice-select').handlers.change();
  f.c.api=async()=>({ok:true,outcome:'completed',message:'Saved'}); f.c.poll=async()=>{};
  await f.$('#voice-form').handlers.submit({preventDefault(){}});
  assert.doesNotMatch(f.$('#voice-feedback').className,/success/); assert.match(f.$('#voice-feedback').textContent,/not confirmed/);
  f.run('live.voice_settings={...catalogue,provider:"piper",selected:"b"}; renderVoice();'); assert.doesNotMatch(f.$('#voice-feedback').className,/success/);
  f.run('live.voice_settings={...catalogue,selected:"b",name:"George"}; renderVoice();'); assert.match(f.$('#voice-feedback').textContent,/Active voice confirmed/);
  f.run('live.online=false; renderVoice();'); assert.match(f.$('#voice-current').textContent,/Catalogue/); assert.equal(f.$('#voice-apply').disabled,true);
});
test('late mission A cannot replace B or attach stale actions; load failure has retry', async () => {
  const f=fixture(); missions(f); const a=deferred(),b=deferred(); f.c.requestJSON=url=>url.endsWith('A')?a.promise:b.promise; const drawn=[]; f.c.drawMission=d=>drawn.push(d.mission.id);
  const shape=id=>({data:{mission:{id},leads:[],steps:[],outreach:[],events:[]}});
  const first=f.run('loadMission()'); f.run('missionId="B"'); const second=f.run('loadMission()'); b.resolve(shape('B')); await second; a.resolve(shape('A')); await first;
  assert.deepEqual(drawn,['B']);
  f.run('missionId="A"; missionDrawn="A";'); const button=f.run('mBtn("Pause", null, {do:"pause"})'); f.run('missionId="B"'); let acted=false; f.c.act=async()=>{acted=true;}; await button.handlers.click(); assert.equal(acted,false);
  f.c.requestJSON=async()=>{throw Error('Disconnected');}; await f.run('loadMission()'); assert.equal(f.$('#mission-body').hidden,true); assert.equal(f.$('#mission-empty').children[0].textContent,'Retry loading mission');
});
test('connections reject false/invalid success; operation guard disables controls and rolls back errors', async () => {
  const f=fixture(); settings(f); const root=f.$('#providers'),control=new Element('input'); root.append(control); const d=deferred(); let calls=0,rolled=false;
  f.c.operation=()=>{calls++;return d.promise;}; f.c.rollback=()=>{rolled=true;};
  const first=f.run('connectionOperation(operation,rollback)'); assert.equal(control.disabled,true); await f.run('connectionOperation(operation)'); assert.equal(calls,1);
  d.reject(Error('Unavailable')); await first; assert.equal(control.disabled,false); assert.equal(rolled,true); assert.match(f.$('#conn-banner').textContent,/Refresh/);
  for(const data of [{ok:false},{},{error:'Rejected'}]) { f.c.requestJSON=async()=>({data}); await assert.rejects(f.run('api("/api/connections/google/active",{})')); }
});
test('connection flow timeout preserves flow id for check-again; canceled and malformed flows fail', async () => {
  const f=fixture(); settings(f); await assert.rejects(f.run('follow("same-flow",0)'),/unconfirmed/); assert.equal(f.run('pendingFlow'),'same-flow');
  f.c.setTimeout=fn=>{fn();return 0;};
  for(const state of ['canceled','wrong']) {f.c.requestJSON=async()=>({data:{state,message:'Not connected'}}); await assert.rejects(f.run('follow("flow")'));}
  f.c.requestJSON=async()=>({data:{state:'done'}}); f.c.loadConnections=async()=>{}; await f.run('follow("flow")'); assert.equal(f.run('pendingFlow'),'');
});
test('layout consolidation retains unique control IDs, one primary voice form and the approved panel sequence', () => {
  const html=fs.readFileSync(path.join(__dirname,'../UI/index.html'),'utf8');
  const ids=[...html.matchAll(/\bid="([^"]+)"/g)].map(x=>x[1]); assert.equal(new Set(ids).size,ids.length);
  for(const id of ['home-ask','voice-form','voice-select','voice-apply','home-suggest','devices','env-kv','tasks','timers','lists','research','phone-kv','overview-missions']) assert.ok(ids.includes(id),id);
  assert.ok(html.indexOf('id="voice-form"')<html.indexOf('id="dashboard-grid"'));
  const order=[...html.matchAll(/class="card ([\w-]+)"/g)].map(x=>x[1]);
  assert.deepEqual(order,['work-card','timers-card','lists-card','media-card','activity-card','room-card','research-card','phone-card']);
  assert.ok(html.includes('data-draft="Add to my list: "'));
});
test('connection timeout recovery blocks a second sign-in flow while the previous outcome is pending', async()=>{
  const f=fixture(); settings(f); f.run('pendingFlow="existing"'); let requested=false; f.c.requestJSON=async()=>{requested=true;return {data:{flow:'new'}};};
  await assert.rejects(f.run('api("/api/connections/google/connect",{})'),/previous sign-in/); assert.equal(requested,false);
});
test('memory refresh does not show success for invalid data and releases its disabled control',async()=>{
  const f=fixture(); f.c.emptyState=()=>new Element(); f.c.requestJSON=async()=>({data:{}});
  f.run(slice(jarvis,'  async function loadKnowledge()', '  // ---------------------------------------------------------------- command menu'));
  await f.$('#mem-refresh').handlers.click(); assert.equal(f.toasts[0][1],'err'); assert.equal(f.$('#mem-refresh').disabled,false);
  f.c.requestJSON=async()=>({data:{profile:[],preferences:[],facts:[],summaries:[]}}); await f.$('#mem-refresh').handlers.click(); assert.equal(f.toasts[1][1],'ok');
});

test('full HUD submission stays in the console and retains the confirmed response',async()=>{
 const f=fixture();f.c.document.documentElement=new Element();f.c.document.documentElement.classList.add('jarvis-full');let navigation=0;f.c.show=()=>navigation++;
 f.run(slice(jarvis,'  async function send(','  const input ='));f.c.poll=async()=>{};f.c.api=async()=>({outcome:'completed',reply:'Confirmed offline reply'});f.c.source=f.$('#home-input');f.c.source.value='Draft';
 await f.run('send(source.value,true,source)');assert.equal(navigation,0);assert.equal(f.run('latestCommandReply'),'Confirmed offline reply');assert.equal(f.c.source.value,'');
 f.c.document.documentElement.classList.remove('jarvis-full');f.c.source.value='Next';await f.run('send(source.value,true,source)');assert.equal(navigation,1);
});

test('console reply fallback yields to newer backend conversation and hides stale replies offline',()=>{
 const f=fixture();f.run(slice(jarvis,'  function consoleReply()','  function renderHome()'));f.c.latestCommandReply='Confirmed reply';assert.equal(f.run('consoleReply()'),'Confirmed reply');
 f.c.live.conversation=[{role:'assistant',text:'New runtime reply',time:'later'}];assert.equal(f.run('consoleReply()'),'New runtime reply');assert.equal(f.run('latestCommandReply'),'');
 f.c.live.online=false;assert.equal(f.run('consoleReply()'),'');
});
