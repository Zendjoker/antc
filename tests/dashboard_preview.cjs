// Local static preview ONLY. All API data below is synthetic; no Python, Jarvis, or integrations run.
// node tests/dashboard_preview.cjs   -> http://127.0.0.1:8767/?state=speaking
const http = require('node:http');
const fs = require('node:fs');
const path = require('node:path');
const root = path.resolve(__dirname, '../UI');
const voices = { provider: 'elevenlabs', enabled: true, fallback: false, selected: 'thomas', name: 'Thomas', providers: [
  { id:'elevenlabs', name:'ElevenLabs', voices:[{id:'thomas',name:'Thomas',description:'Calm'},{id:'george',name:'George',description:'Warm'}]},
  { id:'piper', name:'Piper · local', voices:[{id:'ryan',name:'Ryan',description:'American man'}]}
] };
let selected = 'thomas', setting = 'elevenlabs';
const snapshot = state => ({ online:state !== 'offline', state:{name:state}, voice_settings:{...voices, selected, name:selected === 'thomas'?'Thomas':'George'},
  user:'Alex', location:'Preview location', connections:{google:'not connected'}, tasks:[{state:'RUNNING',goal:'Compare options for the workspace',seconds:4,steps:[{tool:'research',state:'RUNNING',why:'Reading sources'}]}],
  missions:[{id:'preview',title:'Review a business website',state:'paused',steps_done:2,steps_failed:0,steps_total:6,spent_usd:0,approvals_pending:0}],
  timers:[{id:'preview',label:'Focus session',kind:'timer',in_s:1200,total_s:1500,at:'15:30'}],
  lists:{lists:{ideas:['Review the comparison','Sketch the next iteration']},moments:[{when:'after work',text:'Review the draft'}]},
  phone:{ready:false,mode:false}, home:{online:false,devices:[]}, conversation:[], activity:[{at:Date.now()/1000,text:'Preview: research started',kind:'activity'}],
  volume:{level:35,muted:false}, now_playing:null, research:null, browser:null, spend:{today:0,limit:0}, timing:{}, brain:'Preview',voice:'elevenlabs',hearing:'Preview',wake_word:'hey_jarvis'
});
http.createServer(async(req,res)=>{
  const url = new URL(req.url,'http://127.0.0.1');
  // No forced redirect into Full Jarvis here, on any port: the ordinary Home route must load the normal HUD.
  // Full Jarvis is only ever reached by an explicit ?view=jarvis link or the in-page "Enter full Jarvis" control.
  const state = new URL(req.headers.referer || 'http://127.0.0.1').searchParams.get('state') || 'offline';
  const json = (data,status=200)=>{res.writeHead(status,{'Content-Type':'application/json','Cache-Control':'no-store'});res.end(JSON.stringify(data));};
  let body={}; if(req.method==='POST') {let raw='';for await(const chunk of req)raw+=chunk;try{body=JSON.parse(raw);}catch{}}
  if(url.pathname==='/api/live') {
    const data=snapshot(state);
    if(new URL(req.headers.referer || 'http://127.0.0.1').searchParams.has('long')) {
      data.tasks[0].goal='LongContentWithoutSpaces'.repeat(12);
      data.missions[0].title='LongMissionTitle'.repeat(12);
      data.lists.lists.ideas.push('LongListItem'.repeat(25));
    }
    return json(data);
  }
  if(url.pathname==='/api/voices') return json({...voices,enabled:false});
  if(url.pathname==='/api/env') {
    if(req.method==='POST') { setting=body.TTS_PROVIDER || setting; return json({ok:true,changed:Object.keys(body),restart_required:true}); }
    return json({sections:[{title:'Voice',fields:[{key:'TTS_PROVIDER',value:setting,type:'select',options:['elevenlabs','piper'],comment:'Preview configuration'},{key:'WEATHER_LOCATION',value:'Preview location',type:'text',comment:'Preview field'}]}]});
  }
  if(url.pathname==='/api/status') return json({spend:{usd:{},calls:0},daily_budget:0,tts_provider:setting,stt_provider:'Preview',model:'Preview',llm_default:'local',wake_word:'hey_jarvis',mic_device:'Preview',speaker_device:'Preview',speaker_verify:false});
  if(url.pathname==='/api/connections') return json({providers:[{id:'google',name:'Google',configured:false,setup:'Offline fixture: no account access.',accounts:[]}]});
  if(url.pathname==='/api/knowledge') return json({profile:[],facts:[],preferences:[],summaries:[]});
  if(url.pathname==='/api/missions') return json({error:'Offline preview: detail unavailable'},404);
  if(url.pathname==='/api/command') return json({reply:'Offline preview submission'});
  if(url.pathname==='/api/action') { if(body.do==='set_voice')selected=body.name==='George'?'george':'thomas'; return json({ok:true,message:'Offline fixture acknowledged'}); }
  if(url.pathname.startsWith('/api/')) return json({error:'Offline fixture unavailable'},404);
  const file = url.pathname==='/'?'index.html':url.pathname.slice(1);
  if(!['index.html','app.js','jarvis.js','appearance.js','presence.js','cinematic.js','style.css','workspace.css','brand.css','hud.css','presence.css','cinematic.css','hud-static.js','hud-static.css','hud-static.html','brand/zend-logo.png','brand/zend-favicon.png'].includes(file)) return json({error:'Not found'},404);
  let content=fs.readFileSync(path.join(root,file));
  if(file==='index.html') content=Buffer.from(content.toString().replace(/<link[^>]+(?:fonts.googleapis.com|preconnect)[^>]*>/g,'').replace('Personal workspace','OFFLINE PREVIEW · SYNTHETIC DATA').replace('<b>ZEND</b>','<b>ZEND · Offline preview</b>').replace('<body class="offline">','<body class="offline"><div class="preview-notice">OFFLINE PREVIEW · SYNTHETIC DATA · NO LIVE ACTIONS</div>'));
  res.writeHead(200,{'Content-Type':file.endsWith('.js')?'application/javascript':file.endsWith('.css')?'text/css':file.endsWith('.png')?'image/png':'text/html','Cache-Control':'no-store'});res.end(content);
}).listen(Number(process.env.JARVIS_PREVIEW_PORT||8767),'127.0.0.1',()=>console.log('Offline dashboard preview: http://127.0.0.1:'+(process.env.JARVIS_PREVIEW_PORT||8767)));
