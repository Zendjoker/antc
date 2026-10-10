// Shared asymmetric vector composition, existing controls, one state-driven motion clock.
(() => {
  const NS='http://www.w3.org/2000/svg', root=document.documentElement;
  const host=document.querySelector('.hero-art');
  const element=(tag,attrs={},parent)=>{const n=document.createElementNS(NS,tag);for(const [k,v] of Object.entries(attrs))n.setAttribute(k,v);parent?.append(n);return n;};
  const stage=document.createElement('div');stage.className='cinematic-engine jarvis-instrument';host.prepend(stage);
  const svg=window.JarvisComposition(stage);svg.setAttribute('class','holo-svg');
  const layers=[['outer-backplane',1.2],['lower-edge-seat',-2.1]].map(([name,speed],index)=>({node:svg.querySelector('[data-structure="'+name+'"]'),angle:0,speed,index}));
  layers.push({node:svg.querySelector('[data-motion="aperture"]'),angle:0,speed:-1.8,index:2});
  const sweep=element('g',{class:'holo-sweep'},svg);
  element('path',{d:'M545 358L735 197 A249 249 0 0 1 794 358Z',class:'scan-wedge'},sweep);
  element('path',{d:'M735 197 A249 249 0 0 1 794 358',class:'scan-edge'},sweep);
  
  const menu=document.createElement('nav');menu.className='holo-menu';menu.hidden=true;menu.setAttribute('aria-label','Jarvis instrument controls');stage.append(menu);
  const dock=document.createElement('nav');dock.className='holo-command-dock';dock.setAttribute('aria-label','Jarvis controls');stage.append(dock);
  const sectors=[['Conversation','#chat-scroll'],['Tasks','#tasks'],['Devices','#devices'],['System','#status-cards'],['Settings','#page-settings']];
  const panel=document.createElement('section');panel.className='holo-panel';panel.hidden=true;panel.setAttribute('aria-label','Jarvis control panel');document.querySelector('#page-home').append(panel);
  const panelHead=document.createElement('header'),panelTitle=document.createElement('h2'),close=document.createElement('button');close.type='button';close.textContent='Close';close.className='btn';panelHead.append(panelTitle,close);
  const panelBody=document.createElement('div');panelBody.className='holo-panel-body';panel.append(panelHead,panelBody);
  let moved=null,enabled=false,phase='connecting',frame=0,last=0,elapsed=0,sweepAngle=0,snapshot={online:false};
  let sampleCount=0,sampleTime=0,slowFrames=0,callbackTime=0,measured={};
  const telemetry=document.createElement('div');telemetry.className='holo-telemetry';
  const state=document.createElement('b'),operation=document.createElement('span'),progress=document.createElement('span'),performanceLabel=document.createElement('span');
  performanceLabel.hidden=true;telemetry.setAttribute('aria-live','polite');telemetry.setAttribute('role','status');telemetry.append(state,operation,progress,performanceLabel);stage.append(telemetry);
  const legend=document.createElement('div');legend.className='holo-legend';legend.textContent='ZEND / VISUAL ENGINE\nASYMMETRIC VECTOR CORE\nVECTOR INSTRUMENT\nSTATE FROM LIVE RUNTIME';stage.append(legend);
  const restore=()=>{if(moved){moved.parent.insertBefore(moved.node,moved.next?.parentNode===moved.parent?moved.next:null);moved=null;}};
  function closePanel(){restore();panel.hidden=true;root.classList.remove('holo-panel-open');}
  function select(index) {
    closePanel();menu.hidden=true;
    const [name,selector]=sectors[index],node=document.querySelector(selector);
    panelTitle.textContent=name;panel.hidden=false;root.classList.add('holo-panel-open');
    moved={node,parent:node.parentNode,next:node.nextSibling};panelBody.append(node);close.focus();
    if(name==='System') loadStatus().catch(()=>{showBanner('Status unavailable. Close and reopen to retry.',true);});
    if(name==='Settings') loadSettings().catch(e=>showBanner(e.message+' Close and reopen to retry.',true));
  }
  sectors.forEach(([name],i)=>{
    const button=document.createElement('button');button.type='button';button.textContent=name;button.className='holo-sector';
    const a=(-90+i*72)*Math.PI/180;button.style.left=`${50+40*Math.cos(a)}%`;button.style.top=`${50+40*Math.sin(a)}%`;
    button.addEventListener('click',()=>select(i));menu.append(button);
    const ringButton=document.createElement('button');ringButton.type='button';ringButton.textContent=name;ringButton.className='holo-ring-control';
    ringButton.style.left=`${50+46*Math.cos(a)}%`;ringButton.style.top=`${50+46*Math.sin(a)}%`;ringButton.setAttribute('aria-label',`Open ${name.toLowerCase()}`);ringButton.addEventListener('click',()=>select(i));dock.append(ringButton);
  });
  close.addEventListener('click',()=>{closePanel();document.querySelector('#jarvis-expand').focus();});
  const motion=matchMedia('(prefers-reduced-motion: reduce)');
  const unavailable=()=>['offline','disconnected','connecting','quiet','error','interrupted'].includes(phase);
  function animate(now) {
    frame=0;if(!enabled||document.hidden||motion.matches||unavailable())return;
    const began=performance.now(),interval=last?(now-last)/1000:0,delta=Math.min(interval,.06);last=now;elapsed+=delta;
    const activity=['processing','executing'].includes(phase)?2.8:phase==='listening'?1.4:1;
    for(const layer of layers) {
      const mechanical= .35+.65*Math.pow(Math.sin(elapsed*.16+layer.index*.41),2);
      layer.angle+=delta*layer.speed*activity*mechanical;
      layer.node.setAttribute('transform',`rotate(${layer.angle} 545 358)`);
    }
    sweepAngle+=delta*(phase==='processing'?38:12);sweep.setAttribute('transform',`rotate(${sweepAngle} 545 358)`);
    if(interval>0){sampleCount++;sampleTime+=interval; if(interval>1/50)slowFrames++;callbackTime+=performance.now()-began;}
    if(sampleCount>=300){measured={frames:sampleCount,fps:sampleCount/sampleTime,slowFrames,callbackMs:callbackTime/sampleCount};performanceLabel.textContent=`RENDER ${measured.fps.toFixed(1)} FPS · ${measured.callbackMs.toFixed(2)} MS/FRAME`;stage.dataset.performance=JSON.stringify(measured);sampleCount=0;sampleTime=0;slowFrames=0;callbackTime=0;}
    frame=requestAnimationFrame(animate);
  }
  function reconcile(){cancelAnimationFrame(frame);frame=0;last=0;sampleCount=0;sampleTime=0;slowFrames=0;callbackTime=0;performanceLabel.textContent=motion.matches?'REDUCED MOTION':unavailable()?'MOTION PAUSED':'';if(enabled&&!document.hidden&&!motion.matches&&!unavailable())frame=requestAnimationFrame(animate);}
  function update(data,nextPhase){
    nextPhase=nextPhase==='idle_check'?'speaking':nextPhase;
    if(data.online!==true && !['offline','connecting','disconnected'].includes(nextPhase)) nextPhase='offline';
    snapshot=data;const changed=phase!==nextPhase;phase=nextPhase;stage.dataset.phase=phase;
    const labels={wake_word_only:'IDLE',processing:'THINKING',executing:'TASK RUNNING',offline:'ZEND UNAVAILABLE',disconnected:'CONNECTION LOST',connecting:'CONNECTING',quiet:'QUIET',listening:'LISTENING',speaking:'SPEAKING',interrupted:'INTERRUPTED',error:'ERROR',call:'IN CALL'};
    state.textContent=labels[phase]||phase.replace(/_/g,' ').toUpperCase();
    const task=data.online&&(data.tasks||[]).find(t=>String(t.state).toUpperCase()==='RUNNING');
    operation.textContent=task?task.goal:data.online?'LIVE RUNTIME · '+(data.state?.label||data.state?.name||'Unknown'):'RUNTIME UNAVAILABLE';
    operation.title=operation.textContent;
    const steps=task?.steps||[],done=steps.filter(s=>['COMPLETED','SUCCEEDED','DONE'].includes(String(s.state).toUpperCase())).length;
    progress.textContent=steps.length?`${done}/${steps.length} REPORTED STEPS · ${Math.round(done/steps.length*100)}%`:'';
    if(changed)reconcile();
  }
  window.JarvisHUD={enable(value){enabled=value;stage.dataset.enabled=String(value);menu.hidden=true;closePanel();const button=document.querySelector('#jarvis-expand');if(value){button.setAttribute('aria-label','Open Jarvis commands');button.querySelector('span').textContent='COMMANDS';update(snapshot,phase);}reconcile();},menu(){menu.hidden=!menu.hidden;if(!menu.hidden){closePanel();menu.querySelector('button').focus();}},update,stats:()=>({...measured,enabled,phase,layers:layers.length}),destroy(){cancelAnimationFrame(frame);restore();document.removeEventListener('visibilitychange',reconcile);document.removeEventListener('keydown',handleKey,true);motion.removeEventListener('change',reconcile);stage.remove();panel.remove();}};
  document.addEventListener('visibilitychange',reconcile);motion.addEventListener('change',reconcile);
  function handleKey(e){if(enabled&&e.key==='Escape'&&(!panel.hidden||!menu.hidden)){e.preventDefault();e.stopImmediatePropagation();closePanel();menu.hidden=true;document.querySelector('#jarvis-expand').focus();}}
  document.addEventListener('keydown',handleKey,true);
  window.addEventListener('pagehide',e=>{if(!e.persisted)window.JarvisHUD.destroy();});
  window.dispatchEvent?.(new Event('jarvis:hud-ready'));
})();
