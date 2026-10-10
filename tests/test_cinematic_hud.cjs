// Offline renderer/controller integration. No browser, network, voice or integrations.
const {test}=require('node:test'),assert=require('node:assert/strict'),vm=require('node:vm'),fs=require('node:fs'),path=require('node:path');
class Node {
  constructor(tag='div'){this.tag=tag;this.children=[];this.attrs={};this.dataset={};this.style={};this.events={};this.hidden=false;this.textContent='';this.classes=new Set();this.classList={add:k=>this.classes.add(k),remove:k=>this.classes.delete(k)};}
  setAttribute(k,v){this.attrs[k]=v;}
  append(...nodes){for(const n of nodes){n.remove();n.parentNode=this;this.children.push(n);}}
  prepend(n){this.append(n);this.children.unshift(this.children.pop());}
  remove(){if(this.parentNode){this.parentNode.children=this.parentNode.children.filter(n=>n!==this);this.parentNode=null;}}
  insertBefore(n,before){n.remove();n.parentNode=this;const i=this.children.indexOf(before);i<0?this.children.push(n):this.children.splice(i,0,n);}
  get nextSibling(){const i=this.parentNode?.children.indexOf(this);return this.parentNode?.children[i+1]||null;}
  querySelector(tag){const m=tag.match(/^\[([^=]+)="([^"]+)"\]$/);const match=n=>m?n.attrs[m[1]]===m[2]:n.tag===tag;return this.children.find(match)||this.children.map(n=>n.querySelector(tag)).find(Boolean);}
  addEventListener(k,fn){this.events[k]=fn;}focus(){this.focused=true;}
}
function fixture(){
 const nodes=new Map(),events={},frames=new Map();let next=0;
 const $=key=>{if(!nodes.has(key))nodes.set(key,new Node());return nodes.get(key);};
 const root=$('root'),host=$('.hero-art'),home=$('#page-home');home.append(host);$('#jarvis-expand').append(new Node('span'));
 for(const key of ['#chat-scroll','#tasks','#devices','#status-cards','#page-settings'])home.append($(key));
 const document={documentElement:root,hidden:false,querySelector:$,createElement:t=>new Node(t),createElementNS:(_,t)=>new Node(t),addEventListener:(k,fn)=>events[k]=fn,removeEventListener:k=>delete events[k]};
 const motion={matches:false,addEventListener:(k,fn)=>motion.change=fn,removeEventListener:()=>{}};
 const context=vm.createContext({document,window:{addEventListener:()=>{}},matchMedia:()=>motion,performance:{now:()=>0},requestAnimationFrame:fn=>{frames.set(++next,fn);return next;},cancelAnimationFrame:id=>frames.delete(id),loadStatus:async()=>{},loadSettings:async()=>{},showBanner:()=>{}});
 const drawing=fs.readFileSync(path.join(__dirname,'../UI/hud-static.js'),'utf8');
 vm.runInContext(drawing.replace("if(document.querySelector('#composition')) window.JarvisComposition(document.querySelector('#composition'));",''),context);
 vm.runInContext(fs.readFileSync(path.join(__dirname,'../UI/cinematic.js'),'utf8'),context);
 return {context,api:context.window.JarvisHUD,$,root,home,host,document,motion,frames,events};
}
test('asymmetric geometry shares the reviewed drawing; only three mechanical assemblies move',()=>{
 const f=fixture();assert.equal(f.api.stats().layers,3);assert.equal(f.frames.size,0);
 const svg=f.host.children[0].children[0];assert.ok(svg.querySelector('[data-structure="left-cutaway"]'));assert.ok(svg.querySelector('[data-motion="aperture"]'));
 const paths=n=>Number(n.tag==='path')+n.children.reduce((s,c)=>s+paths(c),0);assert.ok(paths(svg)>100);
});
test('one animation clock pauses on hidden, reduced-motion, offline and exit; thinking changes come from runtime',()=>{
 const f=fixture();f.api.update({online:true,state:{name:'processing'},tasks:[]},'processing');f.api.enable(true);assert.equal(f.frames.size,1);
 f.api.update({online:true,state:{name:'processing'},tasks:[]},'processing');assert.equal(f.frames.size,1);
 f.document.hidden=true;f.events.visibilitychange();assert.equal(f.frames.size,0);
 f.document.hidden=false;f.events.visibilitychange();assert.equal(f.frames.size,1);
 f.motion.matches=true;f.motion.change();assert.equal(f.frames.size,0);
 f.motion.matches=false;f.motion.change();assert.equal(f.frames.size,1);
 f.api.update({online:false},'offline');assert.equal(f.frames.size,0);f.api.enable(false);assert.equal(f.frames.size,0);
});
test('radial panels move existing controls and restore them on close or exit; Escape closes menu first',()=>{
 const f=fixture();f.api.enable(true);const stage=f.host.children[0],menu=stage.children.find(n=>n.tag==='nav');f.api.menu();assert.equal(menu.hidden,false);
 menu.children[1].events.click();assert.notEqual(f.$('#tasks').parentNode,f.home);
 let stopped=false;f.events.keydown({key:'Escape',preventDefault(){},stopImmediatePropagation(){stopped=true;}});assert.equal(stopped,true);assert.equal(f.$('#tasks').parentNode,f.home);
 menu.children[4].events.click();assert.notEqual(f.$('#page-settings').parentNode,f.home);f.api.enable(false);assert.equal(f.$('#page-settings').parentNode,f.home);
});
test('progress is derived only from reported task steps; unavailable state clears task labels',()=>{
 const f=fixture(),stage=f.host.children[0],telemetry=stage.children.find(n=>n.className==='holo-telemetry');
 f.api.update({online:true,state:{name:'wake_word_only'},tasks:[{state:'RUNNING',goal:'Real goal',steps:[{state:'COMPLETED'},{state:'RUNNING'}]}]},'executing');assert.equal(telemetry.children[2].textContent,'1/2 REPORTED STEPS · 50%');
 f.api.update({online:false},'disconnected');assert.equal(telemetry.children[1].textContent,'RUNTIME UNAVAILABLE');assert.equal(telemetry.children[2].textContent,'');
 f.api.destroy();assert.equal(f.frames.size,0);assert.equal(f.events.keydown,undefined);
});
test('frame measurement counts real long intervals rather than hiding dropped frames behind animation clamps',()=>{
 const f=fixture();f.api.update({online:true,state:{name:'processing'}},'processing');f.api.enable(true);
 for(let i=1;i<303;i++){const [id,fn]=f.frames.entries().next().value;f.frames.delete(id);fn(i*100);}
 const sample=f.api.stats();assert.ok(Math.abs(sample.fps-10)<.01);assert.equal(sample.slowFrames,300);f.api.destroy();
});

test('backend idle_check maps to speaking; disconnected stops motion and clears progress',()=>{
 const f=fixture();f.api.enable(true);f.api.update({online:true,state:{name:'idle_check'}},'idle_check');assert.equal(f.api.stats().phase,'speaking');assert.equal(f.frames.size,1);
 f.api.update({online:false},'disconnected');assert.equal(f.frames.size,0);assert.equal(f.api.stats().phase,'disconnected');
});
test('motion actually advances reviewed mechanical assemblies without moving foreground wedge',()=>{
 const f=fixture();f.api.update({online:true,state:{name:'processing'}},'processing');f.api.enable(true);
 for(const time of [100,200]){const [id,fn]=f.frames.entries().next().value;f.frames.delete(id);fn(time);}
 const svg=f.host.children[0].children[0];assert.match(svg.querySelector('[data-motion="aperture"]').attrs.transform,/rotate\(-/);assert.equal(svg.querySelector('[data-structure="left-cutaway"]').attrs.transform,undefined);f.api.destroy();
});

test('unavailable snapshot cannot animate or report speaking even when given a stale voice phase',()=>{
 const f=fixture();f.api.enable(true);f.api.update({online:false},'speaking');assert.equal(f.api.stats().phase,'offline');assert.equal(f.frames.size,0);
});
