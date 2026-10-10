// Execute the static drawing with no network, animation or application globals.
const {test}=require('node:test'),assert=require('node:assert/strict'),vm=require('node:vm'),fs=require('node:fs'),path=require('node:path');
test('static composition builds asymmetric assemblies without runtime or animation dependencies',()=>{
 class Node{constructor(tag){this.tag=tag;this.attrs={};this.children=[];}setAttribute(k,v){this.attrs[k]=v;}append(n){this.children.push(n);}}
 const host=new Node('div'),source=fs.readFileSync(path.join(__dirname,'../UI/hud-static.js'),'utf8');
 vm.runInNewContext(source,{window:{},document:{querySelector:()=>host,createElementNS:(_,tag)=>new Node(tag)}});
 const all=n=>[n,...n.children.flatMap(all)],nodes=all(host),structures=nodes.map(n=>n.attrs['data-structure']).filter(Boolean);
 for(const key of ['outer-backplane','left-cutaway','lower-console','reactor-aperture','orbital-satellite','recessed-instrumentation'])assert.ok(structures.includes(key),key);
 assert.ok(nodes.filter(n=>n.tag==='line').length>300);
 assert.equal(host.children[0].attrs.role,'img');
 assert.doesNotMatch(source,/requestAnimationFrame|fetch\s*\(|setInterval\s*\(/);
});

test('preview revision is visible and versions both visual assets',()=>{
 const html=fs.readFileSync(path.join(__dirname,'../UI/hud-static.html'),'utf8');
 assert.match(html,/COMPOSITION 03/);
 assert.match(html,/hud-static\.css\?v=3/);
 assert.match(html,/hud-static\.js\?v=3/);
 const css=fs.readFileSync(path.join(__dirname,'../UI/hud-static.css'),'utf8');
 assert.match(css,/@media\(max-width:700px\) and \(min-height:1000px\)/);
});
