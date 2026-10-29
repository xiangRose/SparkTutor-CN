'use strict';
(() => {
  const pet=document.querySelector('#spark-pet');
  if(!pet)return;
  const figure=pet.querySelector('#spark-pet-figure');
  const image=pet.querySelector('.spark-pet-image');
  const speech=pet.querySelector('.spark-pet-speech');
  const menu=pet.querySelector('#spark-pet-menu');
  const actions=pet.querySelector('#spark-pet-actions');
  const toolsToggle=pet.querySelector('#spark-pet-tools-toggle');
  const toolsBar=pet.querySelector('#spark-pet-tools');
  const launcher=document.querySelector('#spark-pet-show');
  const panel=pet.querySelector('#spark-pet-panel');
  const question=pet.querySelector('#spark-pet-question');
  const activityList=pet.querySelector('#spark-pet-activity-list');
  const sizeInput=pet.querySelector('#spark-pet-size');
  const statusText=pet.querySelector('#spark-pet-status');
  const unread=pet.querySelector('#spark-pet-unread');
  const user=document.querySelector('meta[name="sparktutor-user"]')?.content||'guest';
  const key=`sparktutor.pet.${user}`;
  const pictures={idle:'/assets/pet-idle.png',wave:'/assets/pet-idle.png',cheer:'/assets/pet-cheer.png',sleep:'/assets/pet-sleep.png'};
  const lines={idle:'今天也一起学点新东西吧！',wave:'嗨！继续探索 Spark 吧～',cheer:'太棒了！又向前迈了一步 ✦',sleep:'休息一下，灵感会回来哦…'};
  const poses=['wave','cheer','sleep'];
  let quietTimer,poseTimer,drag=null,cycle=0,recognition=null,activity=[],restoredQuestionId=null;
  let saved={};try{saved=JSON.parse(localStorage.getItem(key)||'{}')||{}}catch{}
  const defaultSize=()=>window.innerWidth<=700?88:112;
  const migrateLayout=saved.layoutVersion!==2;
  if(migrateLayout)saved={...saved,size:defaultSize(),layoutVersion:2};
  const store=(hidden=pet.hidden)=>{try{const position=pet.hidden?saved:{x:pet.offsetLeft,y:pet.offsetTop};saved={...saved,x:position.x,y:position.y,hidden};localStorage.setItem(key,JSON.stringify(saved))}catch{}};
  const clamp=(x,y)=>({x:Math.max(0,Math.min(window.innerWidth-pet.offsetWidth,x)),y:Math.max(0,Math.min(window.innerHeight-pet.offsetHeight,y))});
  const place=(x,y)=>{const p=clamp(x,y);pet.style.left=`${p.x}px`;pet.style.top=`${p.y}px`;pet.dataset.dock=p.x+pet.offsetWidth/2<window.innerWidth/2?'left':'right'};
  let avoidanceFrame=null;
  const avoidControls=()=>{
    if(pet.hidden||drag||typeof pet.getBoundingClientRect!=='function')return;
    const gap=10,width=window.innerWidth,height=window.innerHeight;
    const visibleRect=node=>{
      if(!node||node.hidden||typeof node.getBoundingClientRect!=='function')return null;
      const rect=node.getBoundingClientRect();
      if(!(rect.width>0&&rect.height>0))return null;
      return rect;
    };
    const protectedSelectors='.chat-form,.editor-actions,.lesson-controls'
      +',#settings-form .field :is(input,select,textarea),.appearance-panel .field :is(input,select,textarea),[data-account-form] .field :is(input,select,textarea)'
      +',#settings-form .inline-actions button,.appearance-panel [data-appearance-reset],[data-account-form] button[type="submit"]'
      +(toolsBar.hidden&&panel.hidden&&menu.hidden?',.editor-wrap':'');
    const controls=Array.from(document.querySelectorAll(protectedSelectors)).map(visibleRect).filter(Boolean)
      .map(rect=>({left:Math.max(0,rect.left),top:Math.max(0,rect.top),right:Math.min(width,rect.right),bottom:Math.min(height,rect.bottom)}))
      .filter(rect=>rect.right>rect.left&&rect.bottom>rect.top);
    // Moving to the other edge turns popovers inward. Re-read their geometry
    // once after a move so an open panel also stays within the viewport.
    for(let pass=0;pass<2;pass++){
      const root=visibleRect(pet);if(!root)return;
      const occupied=[pet,menu,toolsBar,panel];if(!speech.classList.contains('is-quiet'))occupied.push(speech);
      const boxes=occupied.map(visibleRect).filter(Boolean).map(rect=>({left:rect.left-root.left,top:rect.top-root.top,right:rect.right-root.left,bottom:rect.bottom-root.top}));
      const edges={left:Math.min(...boxes.map(rect=>rect.left)),top:Math.min(...boxes.map(rect=>rect.top)),right:Math.max(...boxes.map(rect=>rect.right)),bottom:Math.max(...boxes.map(rect=>rect.bottom))};
      const overlap=(x,y)=>boxes.reduce((total,box)=>total+controls.reduce((sum,rect)=>sum+
        Math.max(0,Math.min(x+box.right,rect.right+gap)-Math.max(x+box.left,rect.left-gap))*
        Math.max(0,Math.min(y+box.bottom,rect.bottom+gap)-Math.max(y+box.top,rect.top-gap)),0),0);
      const overflow=(x,y)=>Math.max(0,-x-edges.left)+Math.max(0,x+edges.right-width)+Math.max(0,-y-edges.top)+Math.max(0,y+edges.bottom-height);
      if(overlap(root.left,root.top)===0&&overflow(root.left,root.top)===0)return;
      const xs=new Set([root.left,gap-edges.left,width-gap-edges.right]);
      const ys=new Set([root.top,gap-edges.top,height-gap-edges.bottom]);
      for(const rect of controls){
        xs.add(rect.left-gap-edges.right);xs.add(rect.right+gap-edges.left);
        ys.add(rect.top-gap-edges.bottom);ys.add(rect.bottom+gap-edges.top);
      }
      let best=null;
      for(const x of xs)for(const y of ys){
        const point=clamp(x,y),area=overlap(point.x,point.y),outside=overflow(point.x,point.y);
        const distance=(point.x-root.left)**2+(point.y-root.top)**2;
        const candidate={...point,area,outside,distance};
        if(!best||outside<best.outside||outside===best.outside&&(area<best.area||area===best.area&&distance<best.distance))best=candidate;
      }
      if(!best||best.x===root.left&&best.y===root.top)return;
      place(best.x,best.y);store();
    }
  };
  const scheduleAvoidance=()=>{
    if(avoidanceFrame!==null)return;
    const schedule=window.requestAnimationFrame?.bind(window)||((callback)=>setTimeout(callback,0));
    avoidanceFrame=schedule(()=>{avoidanceFrame=null;avoidControls();});
  };
  const speak=(message)=>{speech.textContent=message;speech.classList.remove('is-quiet');clearTimeout(quietTimer);quietTimer=setTimeout(()=>speech.classList.add('is-quiet'),4300);scheduleAvoidance()};
  const pose=(name,automatic=false)=>{if(!pictures[name])return;pet.dataset.pose=name;image.src=pictures[name];image.alt=`Spark ${name==='sleep'?'闭眼休息':name==='cheer'?'开心欢呼':name==='wave'?'招手':'微笑待机'}`;if(!automatic)speak(lines[name]);clearTimeout(poseTimer);if(name!=='idle')poseTimer=setTimeout(()=>pose('idle',true),name==='sleep'?7500:3800)};
  const toggleMenu=(open)=>{menu.hidden=!open;actions.setAttribute('aria-expanded',String(open));scheduleAvoidance()};
  const toggleTools=(open)=>{toolsBar.hidden=!open;toolsToggle.setAttribute('aria-expanded',String(open));toolsToggle.setAttribute('aria-label',open?'收起桌宠功能':'展开桌宠功能');if(!open)toggleMenu(false);scheduleAvoidance()};
  const setSize=(value,persist=true)=>{
    const size=Math.max(80,Math.min(200,Number(value)||defaultSize()));
    pet.style.setProperty('--pet-width',`${size}px`);sizeInput.value=String(size);
    if(!pet.hidden)place(pet.offsetLeft,pet.offsetTop);saved.size=size;if(persist)store();scheduleAvoidance();
    // Native range controls and layout can settle after the input handler.
    // Re-clamp using the rendered size before checking popover placement again.
    const schedule=window.requestAnimationFrame?.bind(window)||((callback)=>setTimeout(callback,0));
    schedule(()=>{if(!pet.hidden){place(pet.offsetLeft,pet.offsetTop);if(persist)store();scheduleAvoidance();}});
  };
  const showPanel=(section)=>{panel.dataset.section=section;toggleTools(false);toggleMenu(false);panel.hidden=false;activityList.hidden=section!=='activity';pet.querySelector('#spark-pet-form').hidden=section==='activity';pet.querySelector('#spark-pet-panel-title').textContent=section==='activity'?'最近活动':'向 AI 导师提问';if(section==='activity'){unread.hidden=true;renderActivity()}else{renderPendingQuestions();question.focus()}scheduleAvoidance()};
  const renderPendingQuestions=()=>{
    const detail={items:[]};window.dispatchEvent(new CustomEvent('sparkpet:pending',{detail}));
    const items=Array.isArray(detail.items)?detail.items:[];activityList.replaceChildren();activityList.hidden=!items.length;
    if(!items.length)return;
    const title=document.createElement('p');title.textContent='未发送的问题（点击恢复，确认后再发送）';activityList.append(title);
    for(const item of items){
      if(typeof item.question!=='string'||typeof item.id!=='string')continue;
      const button=document.createElement('button');button.type='button';button.className='spark-pet-pending-question';
      button.textContent=`${item.expired?'已过期 · ':''}${item.question}`;
      button.addEventListener('click',()=>{
        if(question.value.trim()&&question.value!==item.question){statusText.textContent='输入框已有文字，请先发送或清空，再恢复问题';return;}
        question.value=item.question;restoredQuestionId=item.id;question.focus();statusText.textContent='已恢复问题，请确认题目与内容后发送';
      });activityList.append(button);
    }
  };
  const renderActivity=()=>{activityList.replaceChildren();if(!activity.length){activityList.textContent='暂无最近活动。';return}for(const item of activity.slice(0,12)){const link=document.createElement('a');link.href=item.href||'#history';link.textContent=item.label;const small=document.createElement('small');small.textContent=`${item.status} · ${new Date(item.time).toLocaleTimeString('zh-CN',{hour:'2-digit',minute:'2-digit'})}`;link.append(small);activityList.append(link)}};
  const report=(detail)=>{const status=detail.status||'ready',labels={running:'运行中',needsInput:'待输入',ready:'已完成',blocked:'失败'};pet.dataset.status=status==='needsInput'?'needs-input':status;statusText.textContent=labels[status]||'准备就绪';if(detail.label){activity.unshift({status:labels[status]||status,label:detail.label,href:detail.href||'#history',time:Date.now()});activity=activity.slice(0,20);unread.hidden=false;if(status==='ready')pose('cheer',true);else if(status==='blocked')pose('sleep',true);else if(status==='needsInput')pose('wave',true);if(!panel.hidden&&panel.dataset.section==='activity')renderActivity()}};
  const channel=typeof BroadcastChannel==='function'?new BroadcastChannel(`sparktutor.pet.activity.${user}`):null;
  window.addEventListener('sparkpet:activity',event=>{report(event.detail||{});channel?.postMessage(event.detail||{})});
  channel?.addEventListener('message',event=>report(event.data||{}));
  setSize(Number.isFinite(saved.size)?saved.size:defaultSize(),false);
  if(!migrateLayout&&Number.isFinite(saved.x)&&Number.isFinite(saved.y))place(saved.x,saved.y);
  else place(window.innerWidth-pet.offsetWidth-12,window.innerHeight-pet.offsetHeight-12);
  if(migrateLayout)store(saved.hidden===true);
  if(saved.hidden){pet.hidden=true;launcher.hidden=false}
  figure.addEventListener('pointerdown',event=>{if(event.button!==0)return;drag={id:event.pointerId,x:event.clientX,y:event.clientY,left:pet.offsetLeft,top:pet.offsetTop,moved:false};figure.setPointerCapture(event.pointerId)});
  figure.addEventListener('pointermove',event=>{if(!drag||event.pointerId!==drag.id)return;const dx=event.clientX-drag.x,dy=event.clientY-drag.y;if(Math.hypot(dx,dy)>5)drag.moved=true;if(drag.moved){pet.classList.add('is-dragging');place(drag.left+dx,drag.top+dy)}});
  const finishDrag=event=>{if(!drag||event.pointerId!==drag.id)return;const moved=drag.moved;drag=null;pet.classList.remove('is-dragging');if(moved)store();else pose(poses[cycle++%poses.length]);scheduleAvoidance()};
  figure.addEventListener('pointerup',finishDrag);
  figure.addEventListener('pointercancel',()=>{drag=null;pet.classList.remove('is-dragging');store();scheduleAvoidance()});
  figure.addEventListener('keydown',event=>{if(event.key==='Enter'||event.key===' '){event.preventDefault();pose(poses[cycle++%poses.length])}else if(['ArrowLeft','ArrowRight','ArrowUp','ArrowDown'].includes(event.key)){event.preventDefault();const step=event.shiftKey?25:10;place(pet.offsetLeft+({'ArrowLeft':-step,'ArrowRight':step}[event.key]||0),pet.offsetTop+({'ArrowUp':-step,'ArrowDown':step}[event.key]||0));store();scheduleAvoidance()}else if(event.key==='Escape'){event.preventDefault();place(window.innerWidth-pet.offsetWidth-12,window.innerHeight-pet.offsetHeight-12);store();scheduleAvoidance()}});
  toolsToggle.addEventListener('click',()=>{panel.hidden=true;toggleTools(toolsBar.hidden)});
  actions.addEventListener('click',()=>{panel.hidden=true;toggleMenu(menu.hidden)});
  menu.addEventListener('click',event=>{const button=event.target.closest('[data-pet-pose]');if(!button)return;pose(button.dataset.petPose);toggleMenu(false)});
  document.querySelector('#spark-pet-hide').addEventListener('click',()=>{toggleTools(false);store(true);pet.hidden=true;launcher.hidden=false});
  figure.addEventListener('contextmenu',event=>{event.preventDefault();document.querySelector('#spark-pet-hide').click()});
  pet.querySelector('#spark-pet-chat').addEventListener('click',()=>showPanel('chat'));
  pet.querySelector('#spark-pet-activity').addEventListener('click',()=>showPanel('activity'));
  pet.querySelector('#spark-pet-panel-close').addEventListener('click',()=>{panel.hidden=true;scheduleAvoidance()});
  pet.querySelector('#spark-pet-form').addEventListener('submit',event=>{event.preventDefault();const text=question.value.trim();if(!text)return;const detail={question:text,restoreId:restoredQuestionId,accepted:false};window.dispatchEvent(new CustomEvent('sparkpet:ask',{detail}));if(!detail.accepted){statusText.textContent='问题尚未发送，文字已保留，请重试';return;}question.value='';restoredQuestionId=null;panel.hidden=true;report({status:'running',label:'问题已保存，等待 AI 导师处理',href:'#lesson'})});
  question.addEventListener('input',()=>{if(!question.value.trim())restoredQuestionId=null;});
  pet.querySelector('#spark-pet-voice').addEventListener('click',()=>{const Speech=window.SpeechRecognition||window.webkitSpeechRecognition;if(!Speech){showPanel('chat');statusText.textContent='当前浏览器不支持语音输入';return}if(recognition){recognition.stop();recognition=null;return}recognition=new Speech();recognition.lang='zh-CN';recognition.interimResults=false;recognition.onresult=event=>{showPanel('chat');question.value=event.results[0][0].transcript;statusText.textContent='已识别语音，请确认后发送'};recognition.onerror=()=>{statusText.textContent='语音识别未完成，请检查麦克风权限'};recognition.onend=()=>{recognition=null};recognition.start();statusText.textContent='正在听你说…'});
  sizeInput.addEventListener('input',()=>setSize(sizeInput.value));
  pet.querySelector('#spark-pet-size-reset').addEventListener('click',()=>{setSize(defaultSize());place(window.innerWidth-pet.offsetWidth-12,window.innerHeight-pet.offsetHeight-12);store()});
  launcher.addEventListener('click',()=>{pet.hidden=false;launcher.hidden=true;place(saved.x??pet.offsetLeft,saved.y??pet.offsetTop);store();pose('wave')});
  window.addEventListener('resize',()=>{if(!pet.hidden){place(pet.offsetLeft,pet.offsetTop);store();scheduleAvoidance()}});
  document.addEventListener('scroll',scheduleAvoidance,{capture:true,passive:true});
  window.addEventListener('hashchange',scheduleAvoidance);
  window.addEventListener('sparkpet:activity',scheduleAvoidance);
  if(typeof MutationObserver==='function'){
    const content=document.querySelector('#content');
    if(content)new MutationObserver(scheduleAvoidance).observe(content,{childList:true,subtree:true});
  }
  document.addEventListener('pointerdown',event=>{if(!pet.contains(event.target))toggleTools(false)});
  document.addEventListener('keydown',event=>{if(event.key==='Escape'){panel.hidden=true;toggleTools(false)}if(event.ctrlKey&&event.altKey&&event.key.toLowerCase()==='p'){event.preventDefault();if(pet.hidden)launcher.click();else showPanel('chat')}});
  if(!window.matchMedia('(prefers-reduced-motion: reduce)').matches)setInterval(()=>{if(!pet.hidden&&!drag&&menu.hidden)pose(poses[cycle++%poses.length],true)},18000);
  scheduleAvoidance();
})();
