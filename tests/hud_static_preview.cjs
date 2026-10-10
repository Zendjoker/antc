// Offline art-direction preview. No runtime, APIs, integrations or external resources.
const http=require('node:http'),fs=require('node:fs'),path=require('node:path');
const root=path.resolve(__dirname,'../UI');
http.createServer((req,res)=>{
 const name=new URL(req.url,'http://127.0.0.1').pathname.slice(1)||'hud-static.html';
 if(!['hud-static.html','hud-static.css','hud-static.js'].includes(name)){res.writeHead(404);return res.end();}
 res.writeHead(200,{'Content-Type':name.endsWith('.js')?'application/javascript':name.endsWith('.css')?'text/css':'text/html','Cache-Control':'no-store'});res.end(fs.readFileSync(path.join(root,name)));
}).listen(8768,'127.0.0.1',()=>console.log('Static composition only: http://127.0.0.1:8768'));
