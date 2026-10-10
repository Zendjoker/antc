// Shared vector composition. Runtime and motion belong to the existing HUD controller.
window.JarvisComposition = function(host) {
 const NS='http://www.w3.org/2000/svg';
 const S=(tag,a={},parent=svg)=>{const n=document.createElementNS(NS,tag);Object.entries(a).forEach(([k,v])=>n.setAttribute(k,v));parent.append(n);return n;};
 const svg=document.createElementNS(NS,'svg');svg.setAttribute('viewBox','0 0 1240 760');svg.setAttribute('role','img');svg.setAttribute('aria-label','Asymmetric red and white Jarvis reactor: broken mechanical arcs, overlapping left instrumentation wedge, dense tick fans, lower diagnostic consoles and right orbital satellite.');host.append(svg);
 const pt=(x,y,r,a)=>[x+r*Math.cos(a*Math.PI/180),y+r*Math.sin(a*Math.PI/180)];
 const path=(d,c='soft fine',parent=svg)=>S('path',{d,class:c,fill:'none'},parent);
 const line=(x1,y1,x2,y2,c='muted fine',parent=svg)=>S('line',{x1,y1,x2,y2,class:c},parent);
 const arc=(x,y,r,a,b,c='white fine',parent=svg)=>{const p=pt(x,y,r,a),q=pt(x,y,r,b);return path('M'+p.join(' ')+'A'+r+' '+r+' 0 '+(b-a>180?1:0)+' 1 '+q.join(' '),c,parent);};
 const band=(x,y,r,w,a,b,c='fill-white',parent=svg)=>{const p=pt(x,y,r,a),q=pt(x,y,r,b),s=pt(x,y,r-w,b),t=pt(x,y,r-w,a);return S('path',{d:'M'+p.join(' ')+'A'+r+' '+r+' 0 '+(b-a>180?1:0)+' 1 '+q.join(' ')+'L'+s.join(' ')+'A'+(r-w)+' '+(r-w)+' 0 '+(b-a>180?1:0)+' 0 '+t.join(' ')+'Z',class:c},parent);};
 const text=(x,y,t,c='',parent=svg,rotate)=>{const n=S('text',{x,y,class:c,...(rotate?{transform:'rotate('+rotate+' '+x+' '+y+')'}:{})},parent);n.textContent=t;return n;};
 const ticks=(x,y,r,a,b,count,major=6,parent=svg)=>{for(let i=0;i<=count;i++){const angle=a+(b-a)*i/count,p=pt(x,y,r,angle),q=pt(x,y,r+(i%major===0?22:12),angle);line(...p,...q,i%major===0?'white fine':'soft fine',parent).setAttribute('style','stroke-width:'+(i%major===0?1.5:1.05));}};
 const gear=(x,y,r,segments,parent=svg)=>{arc(x,y,r,0,359,'muted fine',parent);for(let i=0;i<segments;i++){const a=i*360/segments;band(x,y,r-4,2,a,a+360/segments*.65,i%4===0?'fill-red':'fill-white',parent);}};
 const cx=545,cy=358;
 // Exposed backplane: large unequal arc lengths, abrupt shoulders and interrupted assembly.
 const back=S('g',{'data-structure':'outer-backplane'});
 arc(cx,cy,305,-147,-10,'soft fine',back);arc(cx,cy,304,26,126,'white fine',back);
 arc(cx,cy,321,-108,-43,'deep-red fine',back);arc(cx,cy,321,16,97,'deep-red fine',back);
 band(cx,cy,315,7,-36,12,'fill-white',back);band(cx,cy,315,10,-7,31,'fill-red',back);
 band(cx,cy,315,7,54,88,'fill-white',back);band(cx,cy,315,11,92,114,'fill-red',back);
 band(cx,cy,328,5,71,105,'fill-white',back);band(cx,cy,319,4,-133,-111,'fill-red',back);
 for(let a=-130;a<-30;a+=11){const p=pt(cx,cy,298,a),q=pt(cx,cy,273,a);line(...p,...q,'soft fine',back);}
 path('M300 155L324 140L359 146L377 130L408 135L429 125L450 129L472 114L507 125L532 120L576 129L607 124L639 144L668 144L686 163','white fine',back);
 ticks(cx,cy,249,-134,-23,77,7,back);ticks(cx,cy,250,27,105,60,6,back);
 // Broad mechanical teeth arranged in one working sector rather than repeated full rings.
 const teeth=S('g',{'data-structure':'mechanical-band'});
 for(let i=0;i<13;i++){const a=32+i*5.2;band(cx,cy,287,19,a,a+3.1,i%3===0?'fill-red':'fill-dark',teeth);band(cx,cy,283,2,a+.3,a+2.7,'fill-white',teeth);}
 for(let i=0;i<9;i++){const a=112+i*5.8;band(cx,cy,267,10,a,a+3.7,i%2?'fill-dark':'fill-red',teeth);}
 for(let a=11;a<30;a+=6){band(cx,cy,255,20,a,a+2,'fill-white',teeth);band(cx,cy,269,3,a,a+2,'fill-red',teeth);}
 // Central reactor is an angular aperture; its sparse dark interior gives the instruments hierarchy.
 const reactor=S('g',{'data-structure':'reactor-aperture'});
 arc(cx,cy,180,-143,43,'muted hair',reactor);arc(cx,cy,180,65,228,'soft hair',reactor);
 arc(cx,cy,156,-176,-39,'deep-red fine',reactor);arc(cx,cy,155,24,163,'deep-red fine',reactor);
 band(cx,cy,157,3,-114,-61,'fill-red',reactor);band(cx,cy,158,2,72,97,'fill-white',reactor);
 const aperture=S('g',{'data-motion':'aperture'},reactor);
 for(let i=0;i<6;i++){const g=S('g',{transform:'rotate('+i*60+' '+cx+' '+cy+')'},aperture);path('M'+(cx-18)+' '+(cy-43)+'L'+(cx+22)+' '+(cy-73)+'L'+(cx+51)+' '+(cy-146)+'L'+(cx+63)+' '+(cy-132)+'L'+(cx+37)+' '+(cy-66)+'L'+(cx+9)+' '+(cy-38),'white fine',g);path('M'+(cx+29)+' '+(cy-82)+'L'+(cx+48)+' '+(cy-128),'red heavy',g);}
 S('polygon',{points:[[cx,cy-39],[cx+33,cy-19],[cx+33,cy+19],[cx,cy+39],[cx-33,cy+19],[cx-33,cy-19]].map(p=>p.join(',')).join(' '),fill:'#07090c',class:'soft fine'},reactor);
 S('polygon',{points:[[cx,cy-13],[cx+13,cy],[cx,cy+13],[cx-13,cy]].map(p=>p.join(',')).join(' '),fill:'#f02a43',class:'red fine'},reactor);
 text(cx-24,cy+64,'REACTOR','micro',reactor);
 for(let i=0;i<34;i++){const a=-170+i*10.2,p=pt(cx,cy,204,a);S('circle',{cx:p[0],cy:p[1],r:i%6===0?2:1,fill:i%6===0?'#ff344c':'#cad8e6'},reactor);}
 for(const a of [-113,-79,-45,48,91,123]){const p=pt(cx,cy,225,a);gear(p[0],p[1],7,12,reactor);line(...pt(cx,cy,165,a),...pt(cx,cy,211,a),'muted hair',reactor);}
 // The left wedge overlaps and occludes the circular assembly, anchoring the asymmetry.
 const wedge=S('g',{'data-structure':'left-cutaway'});
 path('M93 128L126 90L172 84L424 270L401 415L134 546L91 410L80 314L61 299L70 217Z','panel-edge',wedge);
 path('M110 119L151 101L170 108L181 131L394 285L380 405L145 522L121 467','soft fine',wedge);
 path('M84 289L111 302L105 317L79 305L77 282','white fine',wedge);
 path('M145 506L321 419L354 392L378 391','white fine',wedge);
 line(170,102,422,272,'muted hair',wedge);line(134,534,401,410,'soft fine',wedge);
 text(206,149,'ZEND / CORE','technical',wedge,36);
 text(145,493,'REACTOR SCHEMATIC','legend',wedge,-27);
 for(let i=0;i<35;i++){const x=87+i*1.15,y=282-i*4.2;line(x,y,x+7,y-1,i%5===0?'red heavy':'soft fine',wedge);}
 // Two distinct satellite instruments embedded in the foreground panel.
 gear(191,247,60,72,wedge);arc(191,247,49,-70,185,'white fine',wedge);arc(191,247,34,15,260,'red heavy',wedge);
 for(let i=0;i<10;i++)band(191,247,46,5,i*36+4,i*36+19,i%3?'fill-white':'fill-red',wedge);
 S('circle',{cx:191,cy:247,r:23,class:'muted fine',fill:'#070a0f'},wedge);
 line(183,247,199,247,'red fine',wedge);line(191,239,191,255,'red fine',wedge);
 gear(178,392,44,36,wedge);arc(178,392,32,-166,42,'white fine',wedge);arc(178,392,28,28,170,'deep-red fine',wedge);
 text(140,454,'VOICE MATRIX','micro',wedge,-27);
 // An original spatial reactor lattice replaces the reference's human portrait.
 const lattice=S('g',{'data-structure':'reactor-lattice'},wedge);
 for(let j=0;j<18;j++){const y=267+j*6,w=18+Math.sin(j/17*Math.PI)*29;path('M'+(311-w)+' '+y+'L311 '+(y-9)+'L'+(311+w)+' '+y+'L311 '+(y+9)+'Z',j%3?'soft hair':'red fine',lattice);}
 for(let k=-3;k<=3;k++){let d='';for(let j=0;j<18;j++){const y=267+j*6,w=18+Math.sin(j/17*Math.PI)*29;d+=(j?'L':'M')+(311+w*k/3)+' '+y;}path(d,k%2?'deep-red fine':'white hair',lattice);}
 text(279,402,'ASSEMBLY / 01','micro',wedge);
 // Lower cutaway is a separate stepped housing and a second family of diagnostics.
 const lower=S('g',{'data-structure':'lower-console'});
 path('M355 450L417 505L400 615L373 650L263 577L241 519L263 484Z','panel-edge',lower);
 path('M355 461L398 511L383 598L370 628L278 569L257 523L275 496','white fine',lower);
 path('M274 502L287 530L359 589L376 570','red heavy',lower);
 for(let j=0;j<22;j++){const p=pt(350,491,78,93+j*2.6),q=pt(350,491,84,93+j*2.6);line(...p,...q,'soft fine',lower);}
 gear(358,557,34,60,lower);arc(358,557,21,0,235,'white fine',lower);line(335,557,380,557,'muted hair',lower);line(358,534,358,580,'muted hair',lower);
 text(273,551,'SECTOR / MEMORY','micro',lower,39);
 gear(464,605,31,80);arc(464,605,25,-166,6,'deep-red fine');arc(464,605,17,0,270,'soft hair');
 for(let j=0;j<7;j++)band(cx,cy,293,11,72+j*4.4,74+j*4.4,'fill-red');
 // Right-hand labels are sector names, deliberately not fabricated live percentages.
 const labels=S('g',{'data-structure':'sector-readouts',transform:'rotate(-14 723 285)'});
 ['VOICE','THINK','TOOLS','MEMORY'].forEach((t,i)=>{text(714,269+i*24,t,'legend',labels);path('M786 '+(257+i*24)+'L803 '+(265+i*24)+'L792 '+(273+i*24),'muted fine',labels);});
 path('M800 445L846 448L862 461L838 525L799 552L780 544L793 519L821 473L798 465Z','soft fine');
 path('M822 457L837 461L809 514','red fine');
 // Remote satellite: orbital geometry with its own scale, axis and optical depth.
 const satellite=S('g',{'data-structure':'orbital-satellite',transform:'rotate(-17 1031 333)'});
 arc(1031,333,128,0,359,'deep-red fine',satellite);arc(1031,333,122,-169,113,'red fine',satellite);
 ticks(1031,333,111,-177,179,94,12,satellite);
 S('circle',{cx:1031,cy:333,r:105,class:'soft fine',fill:'#06080c'},satellite);
 for(const angle of [-38,23,83])S('ellipse',{cx:1031+(angle===23?17:-14),cy:333+(angle===83?-12:12),rx:104,ry:47,transform:'rotate('+angle+' 1031 333)',class:'soft fine',fill:'none'},satellite);
 gear(1031,333,30,50,satellite);S('circle',{cx:1031,cy:333,r:24,class:'white fine',fill:'none'},satellite);
 line(919,333,1144,333,'muted hair',satellite);line(1031,224,1031,446,'muted hair',satellite);
 band(1031,333,132,4,-18,13,'fill-red',satellite);
 text(976,479,'ORBITAL / SCHEMATIC','micro');

 // Raised and recessed mechanical details, attached to specific sectors.
 const detailing=S('g',{'data-structure':'recessed-instrumentation'});
 for(let j=0;j<9;j++){
   const a=34+j*7.4,mid=pt(cx,cy,302,a);
   const g=S('g',{transform:'rotate('+(a+90)+' '+mid[0]+' '+mid[1]+')'},detailing);
   S('rect',{x:mid[0]-5,y:mid[1]-17,width:10,height:34,rx:2,fill:'#05070b',stroke:'#707e8f','stroke-width':.7},g);
   line(mid[0]-3,mid[1]-12,mid[0]+3,mid[1]-12,'white fine',g);
   for(let k=0;k<5;k++)line(mid[0]-2,mid[1]-7+k*3,mid[0]+2,mid[1]-7+k*3,'muted fine',g);
 }
 for(let j=0;j<7;j++){
   const a=53+j*7,p=pt(cx,cy,221,a),g=S('g',{transform:'rotate('+(a-90)+' '+p[0]+' '+p[1]+')'},detailing);
   path('M'+(p[0]-11)+' '+(p[1]-5)+'L'+(p[0]+9)+' '+(p[1]-5)+'L'+(p[0]+13)+' '+p[1]+'L'+(p[0]+9)+' '+(p[1]+5)+'H'+(p[0]-11)+'Z','soft fine',g);
   S('rect',{x:p[0]-8,y:p[1]-2,width:13,height:4,fill:j%3===0?'#fb2943':'#111c28'},g);
   for(let k=0;k<4;k++)line(p[0]-8+k*4,p[1]+9,p[0]-8+k*4,p[1]+12,'muted hair',g);
 }
 band(cx,cy,310,12,-110,-79,'fill-dark',detailing);
 band(cx,cy,310,3,-110,-79,'fill-white',detailing);
 band(cx,cy,280,3,-106,-50,'fill-white',detailing);
 for(let a=-105;a<-48;a+=3){const p=pt(cx,cy,280,a),q=pt(cx,cy,284,a);line(...p,...q,'muted fine',detailing);}
 ticks(cx,cy,293,-3,8,22,11,detailing);
 path('M355 202L400 232L416 224L374 192','muted fine',detailing);
 for(let k=0;k<6;k++)path('M'+(351+k*4)+' '+(201+k*3)+'L'+(357+k*4)+' '+(207+k*3),'white fine',detailing);
 text(415,206,'APERTURE / VECTOR PATH','micro',detailing,35);
 // Thin optical depth offsets and stepped edge seats.
 path('M89 132L127 94L172 88L428 272L407 421L137 552','muted fine');
 path('M123 93L173 90L432 277','white fine');
 path('M270 488L242 520L264 584L375 657L403 621','muted fine');
 path('M796 447L849 453L864 469','white fine');
 const seat=S('g',{'data-structure':'lower-edge-seat'});
 for(let j=0;j<35;j++){const p=pt(cx,cy,330,58+j*1.1),q=pt(cx,cy,334,58+j*1.1);line(...p,...q,'soft fine',seat);}
 for(let j=0;j<12;j++){const p=pt(cx,cy,192,10+j*2.5);text(p[0],p[1],'·','micro',seat);}

 // Exposed mechanical connectors and small instrumentation tags.
 path('M852 264L878 247L899 249','muted hair');path('M879 406L906 429L926 429','muted hair');
 for(let j=0;j<5;j++){line(382+j*3,204+j*3,411+j*3,232+j*3,'soft fine');}
 text(337,98,'INTERFACE GEOMETRY','micro',svg,-39);
 text(527,716,'ZEND / VISUAL ASSEMBLY','micro');
 return svg;
};
if(document.querySelector('#composition')) window.JarvisComposition(document.querySelector('#composition'));


