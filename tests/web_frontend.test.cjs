const {test}=require('node:test');
const assert=require('node:assert/strict');
const vm=require('node:vm');
const fs=require('node:fs');
const path=require('node:path');

const assets=path.join(__dirname,'../src/sparktutor/web/static');
const source=fs.readFileSync(path.join(assets,'features.js'),'utf8');
const escape=value=>String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));

test('check-in is never automatic on mount, focus, or refresh and needs a click',async()=>{
 const calls=[],events={},pageEvents={},windowEvents={};let tick;
 const host={isConnected:true,innerHTML:'',addEventListener:(name,fn)=>events[name]=fn,removeEventListener:name=>delete events[name]};
 const context={window:{addEventListener:(name,fn)=>windowEvents[name]=fn,removeEventListener:name=>delete windowEvents[name]},document:{hidden:false,addEventListener:(name,fn)=>pageEvents[name]=fn,removeEventListener:name=>delete pageEvents[name]},setInterval:fn=>{tick=fn;return 1;},clearInterval(){}};
 vm.runInNewContext(fs.readFileSync(path.join(assets,'checkin.js'),'utf8'),context);
 const status={today:'2026-10-27',month:'2026-10',firstWeekday:3,daysInMonth:31,currentStreak:0,totalDays:0,monthCount:0,checkedDates:[],checkedInToday:false};
 const panel=context.window.createSparkTutorCheckIn({esc:escape,icon:()=>'',toast(){},rpc:async method=>{calls.push(method);return method==='checkIn'?{...status,checkedInToday:true,recorded:true,totalDays:1,currentStreak:1,monthCount:1,checkedDates:['2026-10-27']}:status;}});
 const settle=()=>new Promise(setImmediate);
 panel.mount(host);await settle();windowEvents.focus();await settle();pageEvents.visibilitychange();await settle();tick();await settle();
 assert.ok(calls.every(method=>method==='getCheckInStatus'));
 assert.ok(host.innerHTML.includes('点击签到'));
 const click={target:{closest:()=>({disabled:false,dataset:{checkinAction:'sign'}})}};
 events.click(click);await settle();assert.equal(calls.filter(method=>method==='checkIn').length,1);
 events.click(click);await settle();assert.equal(calls.filter(method=>method==='checkIn').length,1);
 assert.ok(host.innerHTML.includes('今日已签到'));panel.destroy();
});
function appearanceSetup({stored=null,blocked=false,dark=false}={}){
 const listeners={},mediaListeners={},windowListeners={},values={};
 const root={style:{setProperty:(name,value)=>values[name]=value},dataset:{}};
 const media={matches:dark,addEventListener:(name,callback)=>mediaListeners[name]=callback};
 const context={window:{matchMedia:()=>media,addEventListener:(name,callback)=>windowListeners[name]=callback},
  document:{documentElement:root,addEventListener:(name,callback)=>listeners[name]=callback,querySelector:()=>null,querySelectorAll:()=>[]},
  localStorage:{getItem:()=>{if(blocked)throw Error('blocked');return stored;},setItem:(_,value)=>{if(blocked)throw Error('blocked');stored=value;}}};
 vm.runInNewContext(fs.readFileSync(path.join(assets,'appearance.js'),'utf8'),context);
 return {context,root,values,media,mediaListeners,windowListeners,listeners,stored:()=>stored};
}

test('invalid or malicious visual preferences fall back to readable defaults',()=>{
 const {root,values,context}=appearanceSetup({stored:JSON.stringify({size:'9999',font:'<script>bad</script>',mode:'dark',accent:'url(evil)',spacing:'invalid'})});
 assert.equal(root.style.fontSize,'18px');assert.equal(root.dataset.theme,'dark');
 assert.equal(values['--accent'],'#255540');assert.ok(!context.window.SparkTutorAppearance.panel().includes('<script>'));
 assert.equal(appearanceSetup({stored:'invalid json'}).root.style.fontSize,'18px');
});

test('visual changes work when browser storage is blocked',()=>{
 const {root,listeners}=appearanceSetup({blocked:true});
 assert.doesNotThrow(()=>listeners.change({target:{dataset:{appearance:'size'},value:'22'}}));
 assert.equal(root.style.fontSize,'22px');
});

test('system theme changes affect automatic mode while explicit mode stays selected',()=>{
 const {root,media,mediaListeners,listeners,stored}=appearanceSetup();
 media.matches=true;mediaListeners.change();assert.equal(root.dataset.theme,'dark');
 listeners.change({target:{dataset:{appearance:'mode'},value:'light'}});
 mediaListeners.change();assert.equal(root.dataset.theme,'light');
 assert.equal(JSON.parse(stored()).mode,'light');
});

test('resetting preferences in another tab restores default appearance',()=>{
 const {root,windowListeners}=appearanceSetup({stored:null});
 root.style.fontSize='22px';windowListeners.storage({key:'sparktutor.appearance'});
 assert.equal(root.style.fontSize,'18px');
});
function setup(overrides={}){
 const calls=[],rotations=[],nodes={},downloads=[];
 const context={window:{confirm:()=>false},document:{querySelector:selector=>nodes[selector],createElement:()=>({click(){downloads.push(this.download);}})},sessionStorage:{setItem(){}},localStorage:{},Blob,URL:{createObjectURL:()=> 'blob:unit-test',revokeObjectURL(){}},atob,setTimeout:()=>0,console};
 vm.runInNewContext(source,context);
 const state={scopeCourseId:'',historyCourse:'',historyWindow:'all',dashboard:{courses:[]},...overrides.state};
 const {state:ignoredState,responses:ignoredResponses,...hooks}=overrides;
 const api=context.window.createSparkTutorFeatures({rpc:async(method,params)=>{calls.push({method,params});return overrides.responses?.[method]??{};},esc:escape,state,heading:()=>'',date:x=>x,pct:x=>x==null?'证据不足':`${x}%`,toast(){},refresh:async()=>calls.push({method:'refresh'}),rotateSession:id=>rotations.push(id),currentCode:()=> 'print(1)',...hooks});
 return{api,state,calls,rotations,nodes,downloads,context};
}

test('code comparison escapes both columns and marks additions and removals',()=>{
 const {api}=setup();const html=api.comparison('<script>alert(1)</script>\nold','new\n<script>safe</script>');
 assert.ok(!html.includes('<script>'));assert.ok(html.includes('&lt;script&gt;'));assert.ok(html.includes('diff-line removed'));assert.ok(html.includes('diff-line added'));
});

test('knowledge details use backend counts and authoritatively mapped task evidence',()=>{
 const {api}=setup({state:{dashboard:{knowledgeComponents:{'dataframe.filter':{evaluatedTasks:2,latestPassedTasks:1}},knowledgeComponentEvidence:{'dataframe.filter':[{taskId:'task-1',title:'<img onerror=bad>',passed:true,assisted:true}]},knowledgeEvidenceLimit:100}}});
 const html=api.knowledgeSection();assert.ok(html.includes('1 / 2'));assert.ok(html.includes('&lt;img onerror=bad&gt;'));assert.ok(!html.includes('<img'));assert.ok(html.includes('使用过帮助'));
});

test('scope controls escape imported titles and changing scope refreshes the selected range',async()=>{
 const {api,state,calls}=setup({state:{dashboard:{courses:[{id:'course',title:'<unsafe>'}]}}});
 assert.ok(api.scopeControls().includes('&lt;unsafe&gt;'));
 assert.equal(await api.handleChange({target:{id:'diagnosis-scope',value:'course'}}),true);assert.equal(state.scopeCourseId,'course');assert.deepEqual(calls,[{method:'refresh'}]);
});

test('removed data controls cannot export or delete records',async()=>{
 const {api,calls,downloads}=setup();
 assert.equal(api.renderData,undefined);
 for(const action of ['feature-summary','feature-local-drafts','feature-backup','feature-backup-all','feature-clear-records'])await api.handleAction({dataset:{action}});
 assert.deepEqual(calls,[]);assert.deepEqual(downloads,[]);
});

test('a late trend response cannot repaint a page after navigation invalidates it',async()=>{
 let resolve;const response=new Promise(r=>resolve=r);
 const {api,nodes}=setup({rpc:()=>response});const host={isConnected:true,innerHTML:''};nodes['#trend-content']=host;
 const pending=api.handleAction({dataset:{action:'feature-trend'}});api.invalidate();resolve({points:[],disclaimer:'late'});await pending;
 assert.ok(!host.innerHTML.includes('late'));
});

test('students can still download their current exercise code',async()=>{
 const {api,calls,downloads}=setup();
 await api.handleAction({dataset:{action:'feature-editor-download'}});
 assert.deepEqual(calls,[]);assert.deepEqual(downloads,['sparktutor-exercise.py']);
});

test('the offline editor and app are valid browser scripts with pinned source dependencies',()=>{
 for(const file of ['app.js','features.js','vendor/editor.bundle.js'])new vm.Script(fs.readFileSync(path.join(assets,file),'utf8'),{filename:file});
 const metadata=JSON.parse(fs.readFileSync(path.join(assets,'editor-src/package.json'),'utf8'));
 for(const version of Object.values(metadata.dependencies))assert.match(version,/^\d+\.\d+\.\d+$/);
 assert.ok(fs.readFileSync(path.join(assets,'vendor/EDITOR-LICENSES.txt'),'utf8').includes('MIT'));
});

test('a retired-session response rotates the browser session without retrying the old write',async()=>{
 const app=fs.readFileSync(path.join(assets,'app.js'),'utf8');
 const rpcSource=app.slice(app.indexOf('async function rpc('),app.indexOf('\nfunction toast('));
 const requests=[],rotations=[],queued=[];
 const context=vm.createContext({state:{lesson:{contextToken:'old-context'}},token:'test-token',session:'retired-session',
  fetch:async(url,options)=>{requests.push({url,options});return{ok:false,json:async()=>({error:'学习会话已清除',sessionReset:true,sessionId:'fresh-session'})};},
  rotateSession:id=>rotations.push(id),queueMicrotask:fn=>queued.push(fn),route(){} });
 vm.runInContext(rpcSource,context);
 await assert.rejects(vm.runInContext("rpc('saveDraft',{stepId:'old-step',code:'local copy'})",context),/学习会话已清除/);
 assert.equal(requests.length,1);assert.deepEqual(rotations,['fresh-session']);assert.equal(queued.length,1);
});

test('a lost tunnel produces a readable connection message instead of a JSON parser error',async()=>{
 const app=fs.readFileSync(path.join(assets,'app.js'),'utf8');
 const source=app.slice(app.indexOf('async function rpc('),app.indexOf('\nfunction toast('));
 const context=vm.createContext({state:{},token:'test-token',session:'test-session',fetch:async()=>({json:async()=>{throw new SyntaxError('no tunnel');}})});
 vm.runInContext(source,context);
 await assert.rejects(vm.runInContext("rpc('getLearningDashboard')",context),/公网地址可能已失效/);
});

test('removed browser draft export does not create downloads',async()=>{
 const {api,downloads,calls}=setup();await api.handleAction({dataset:{action:'feature-local-drafts'}});
 assert.deepEqual(downloads,[]);assert.deepEqual(calls,[]);
});

test('a lesson reset notification is handled even when no execution operation is active',async()=>{
 const app=fs.readFileSync(path.join(assets,'app.js'),'utf8');
 const pollSource=app.slice(app.indexOf('async function pollNotifications('),app.indexOf('async function beginOperation('));
 const loaded=[],removed=[];const local={
  'sparktutor.draft.course.lesson.beginner.step':'unsaved code',
  setItem(key,value){this[key]=value;},removeItem(key){delete this[key];removed.push(key);},
 };
 const state={notificationCursor:0,operationId:'',courseId:'course',lessonIdx:2,depth:'beginner',lesson:{lessonId:'lesson',step:{id:'step'}},draftDirty:true};
 const context=vm.createContext({window:{},state,localStorage:local,sessionStorage:{removeItem(){}},location:{hash:'#lesson'},
  rpc:async()=>({cursor:1,notifications:[{method:'lessonReset',params:{courseId:'course',lessonId:'lesson'}}]}),
  currentCode:()=> 'unsaved code',destroyEditor(){},toast(){},loadLesson:async(...args)=>loaded.push(args),route(){},console});
 vm.runInContext(pollSource,context);await vm.runInContext('pollNotifications()',context);
 assert.equal(loaded.length,1);assert.deepEqual(loaded[0],['course',2,'beginner']);assert.equal(state.notificationCursor,1);assert.equal(state.lesson,null);
 assert.ok(removed.includes('sparktutor.draft.course.lesson.beginner.step'));
 assert.ok(Object.keys(local).some(key=>key.startsWith('sparktutor.recovery.')));
});

test('demo details expose evidence and reviews without real-learning action buttons',()=>{
 const {api}=setup();const html=api.demoStage({title:'示例',diagnosis:{diagnosis:'诊断',dimensions:[],recommendedExercise:{title:'推荐练习',reason:'原因'}},events:[],checks:[],review:{changes:[],reason:'复盘'}},0);
 assert.ok(html.includes('只读推荐'));assert.ok(html.includes('前后复盘'));assert.ok(!html.includes('data-action='));
});

test('coach evidence escapes metadata and omits the full task-body duplication',()=>{
 const {api}=setup();const html=api.coachEvidence([{id:'event:1',title:'<unsafe>',description:'记录结果：未通过'},{id:'task:1',title:'题目契约',description:'PRIVATE_FULL_TASK'}],true);
 assert.ok(html.includes('&lt;unsafe&gt;'));assert.ok(html.includes('event:1'));assert.ok(!html.includes('PRIVATE_FULL_TASK'));assert.ok(html.includes('已记录答案查看'));
});

function executionView(){
 const app=fs.readFileSync(path.join(assets,'app.js'),'utf8');
 const source=app.slice(app.indexOf('function showFeedback('),app.indexOf('async function moveStep('));
 const nodes={'#feedback':{innerHTML:''},'#next-button':{disabled:false},'#run-output':{textContent:''},'#execution-mode':{textContent:''}};
 const diagnostics=[];const state={passed:false};
 const context=vm.createContext({$:selector=>nodes[selector],esc:escape,state,window:{activeSparkTutorEditor:{showDiagnostics:items=>diagnostics.push(items)}}});
 vm.runInContext(source,context);
 return {nodes,state,diagnostics,feedback:result=>context.showFeedback(result),execution:(result,submission=false)=>context.showExecution(result,submission)};
}

test('unavailable AI, execution infrastructure and syntax-only submissions are unfinished assessments',()=>{
 for(const metadata of [
  {assessmentSource:'ai_unavailable',failureKind:'infrastructure'},
  {assessmentSource:'ai_review',assessmentEligible:false,failureKind:'infrastructure'},
  {assessmentSource:'course_tests',failureKind:'infrastructure'},
  {assessmentSource:'course_tests',failureKind:'dry_run'},
  {assessmentSource:'course_tests',assessmentEligible:false,failureKind:'unverified'},
  {assessmentSource:'unverified',assessmentEligible:false},
 ]){
  const view=executionView();
  view.feedback({passed:false,...metadata,feedback:[{line:1,message:'<private-error>',suggestion:'检查配置'}]});
  assert.ok(view.nodes['#feedback'].innerHTML.includes('尚未完成判题'));
  assert.ok(!view.nodes['#feedback'].innerHTML.includes('再检查一下'));
  assert.ok(view.nodes['#feedback'].innerHTML.includes('&lt;private-error&gt;'));
  assert.equal(view.nodes['#next-button'].disabled,true);
  assert.equal(view.state.passed,false);
  assert.equal(view.diagnostics[0].length,0);
 }
});

test('learner errors and AI review advice retain different feedback and editor diagnostics',()=>{
 const feedback=[{line:2,message:'应用名称不符合要求'}];
 const learner=executionView();learner.feedback({passed:false,assessmentSource:'structural',assessmentEligible:true,failureKind:'learner',feedback});
 assert.ok(learner.nodes['#feedback'].innerHTML.includes('再检查一下'));
 assert.equal(learner.diagnostics[0],feedback);
 const ai=executionView();ai.feedback({passed:false,assessmentSource:'ai_review',assessmentEligible:false,feedback});
 assert.ok(ai.nodes['#feedback'].innerHTML.includes('AI 评审建议修改'));
 assert.ok(!ai.nodes['#feedback'].innerHTML.includes('尚未完成判题'));
 const passed=executionView();passed.feedback({passed:true,assessmentSource:'structural',assessmentEligible:true,feedback:[]});
 assert.equal(passed.nodes['#next-button'].disabled,false);
 assert.equal(passed.state.passed,true);
});

test('syntax-only output explains that no code or course tests executed without requiring a model',()=>{
 const view=executionView();view.execution({mode:'dry_run',exitCode:0,stdout:'[dry-run] Syntax OK',stderr:''});
 const output=view.nodes['#run-output'].textContent;
 assert.ok(output.includes('未执行代码或课程测试'));
 assert.ok(output.includes('不代表答案正确'));
 assert.ok(output.includes('运行代码不需要模型密钥'));
 assert.ok(output.includes('[dry-run] Syntax OK'));
 assert.ok(view.nodes['#execution-mode'].textContent.includes('未执行代码'));
});

test('real execution and local checks never claim to be syntax-only or manufacture program output',()=>{
 const view=executionView();view.execution({mode:'local',exitCode:0,stdout:'MnMCount\n',stderr:''});
 assert.equal(view.nodes['#run-output'].textContent,'MnMCount\n');
 assert.equal(view.nodes['#execution-mode'].textContent,'本机 Spark · exit 0');
 view.execution({},true);
 assert.equal(view.nodes['#run-output'].textContent,'本地检查已完成，请查看左侧反馈。');
 assert.equal(view.nodes['#execution-mode'].textContent,'本地检查');
});

test('settings distinguish Spark execution from optional model configuration and disclose automatic fallback',async()=>{
 const source=fs.readFileSync(path.join(assets,'settings.js'),'utf8');
 const context={window:{location:{hash:'#settings'}},document:{querySelector:()=>null}};
 vm.runInNewContext(source,context);
 const api=context.window.createSparkTutorSettings({rpc:async()=>({provider:'auto',resolvedProvider:'none',mode:'auto'}),esc:escape});
 const host={isConnected:true,innerHTML:'',querySelector:()=>null,insertAdjacentHTML(position,html){this.innerHTML+=html;}};
 await api.render(host);
 assert.ok(host.innerHTML.includes('运行 Spark 代码不需要模型密钥'));
 assert.ok(host.innerHTML.includes('找不到可用 Spark 时会退回语法检查'));
 assert.ok(host.innerHTML.includes('语法通过不代表代码已执行或答案正确'));
});

function settingsView(savedMode='auto',saveResponse={mode:'local',remember:false}){
 const source=fs.readFileSync(path.join(assets,'settings.js'),'utf8');
 const calls=[],toasts=[];
 const nodes={
  '#settings-form':{isConnected:true},
  '#settings-storage-status':{textContent:'',classList:{toggle(){}}},
  '#ai-provider':{value:'auto'},'#ai-base':{value:''},'#ai-model':{value:''},'#ai-key':{value:''},
  '#anthropic-model':{value:''},'#anthropic-key':{value:''},'#ai-extra-headers':{value:''},
  '#ai-remember':{checked:false},'#execution-mode-select':{value:savedMode},
 };
 const context={window:{location:{hash:'#settings'}},document:{querySelector:()=>null}};
 vm.runInNewContext(source,context);
 const host={isConnected:true,innerHTML:'',querySelector:selector=>nodes[selector],insertAdjacentHTML(position,html){this.innerHTML+=html;}};
 const api=context.window.createSparkTutorSettings({rpc:async(method,params)=>{
  if(method==='webSettings')return{provider:'auto',resolvedProvider:'none',mode:savedMode};
  calls.push({method,params});
  if(saveResponse instanceof Error)throw saveResponse;
  return saveResponse;
 },esc:escape,toast:message=>toasts.push(message)});
 return {api,host,nodes,calls,toasts};
}

test('selecting an execution mode leaves the saved mode unchanged until settings are saved',async()=>{
 const view=settingsView();await view.api.render(view.host);
 view.nodes['#execution-mode-select'].value='local';
 assert.ok(view.host.innerHTML.includes('选择后需点击下方“保存设置”才会生效'));
 assert.ok(view.nodes['#settings-storage-status'].textContent.includes('当前已保存的执行方式：自动检测'));
 assert.equal(view.calls.length,0);assert.equal(view.toasts.length,0);
});

test('save confirmation reports the server-returned execution mode rather than the selected input',async()=>{
 const view=settingsView('auto',{mode:'dry_run',remember:false});await view.api.render(view.host);
 view.nodes['#execution-mode-select'].value='local';await view.api.save();
 assert.equal(view.calls[0].method,'configureWebSettings');assert.equal(view.calls[0].params.mode,'local');
 assert.ok(view.nodes['#settings-storage-status'].textContent.includes('当前已保存的执行方式：仅检查 Python 语法'));
 assert.ok(view.toasts[0].includes('执行方式：仅检查 Python 语法'));
 assert.ok(!view.toasts[0].includes('本机 Spark'));
});

test('failed saving does not mark an unsaved execution selection as active',async()=>{
 const view=settingsView('dry_run',new Error('保存失败'));await view.api.render(view.host);
 view.nodes['#execution-mode-select'].value='local';await assert.rejects(view.api.save(),/保存失败/);
 assert.ok(view.nodes['#settings-storage-status'].textContent.includes('当前已保存的执行方式：仅检查 Python 语法'));
 assert.equal(view.toasts.length,0);
});

test('diagnostic examples and behavior records stay out of primary navigation while settings lives in the account menu',()=>{
 const index=fs.readFileSync(path.join(assets,'index.html'),'utf8');
 const primary=index.match(/<nav\b[^>]*aria-label="主导航"[^>]*>([\s\S]*?)<\/nav>/)?.[1];
 assert.ok(primary);assert.ok(!primary.includes('href="#demo"'));assert.ok(!primary.includes('href="#events"'));assert.ok(!index.includes('data-nav="demo"'));
 const settings=index.match(/<div\b[^>]*id="account-popover"[^>]*>([\s\S]*?)<\/div>/)?.[1]?.match(/<a\b[^>]*href="#settings"[^>]*>([\s\S]*?)<\/a>/)?.[1];
 assert.equal(settings?.replace(/<[^>]+>/g,'').trim(),'设置');
});

/** Execute the real settings renderer, router and explicit demo action with local RPC doubles. */
function demoNavigationView(){
 const app=fs.readFileSync(path.join(assets,'app.js'),'utf8');
 const settingsSource=fs.readFileSync(path.join(assets,'settings.js'),'utf8');
 const calls=[],nodes={},nav=['home','courses','diagnosis','history','events','data','settings'].map(name=>({
  dataset:{nav:name},active:false,classList:{toggle(className,enabled){if(className==='active')this.owner.active=enabled;}},
 }));
 nav.forEach(link=>{link.classList.owner=link;});
 const content={isConnected:true,innerHTML:'',querySelector:selector=>nodes[selector],
  insertAdjacentHTML(position,html){this.innerHTML=position==='afterbegin'?html+this.innerHTML:this.innerHTML+html;}};
 Object.assign(nodes,{'#content':content,'#breadcrumb':{textContent:''},'#menu-toggle':{setAttribute(){}},
  '.sidebar':{classList:{remove(){}}},'#demo-scenario':{value:'transfer_retry'},'#demo-mode':{value:'recorded'},
  '#demo-result':{innerHTML:''}});
 const location={hash:'#settings'},state={busy:false,routeVersion:0,operationId:''};
 const rpc=async(method,params)=>{
  calls.push({method,params});
  if(method==='webSettings')return{provider:'auto',resolvedProvider:'none',mode:'local'};
  if(method==='getEnvironmentReport')return{checks:[],execution:{configuredMode:'local'}};
  if(method==='runDemoScenario')return{provenanceLabel:params.mode==='recorded'?'合成轨迹':'真实 Spark',checks:[],stages:[],disclaimer:'隔离演示'};
  throw new Error(`Unexpected RPC ${method}`);
 };
 const document={title:'',querySelector:selector=>nodes[selector]||null,querySelectorAll:selector=>selector==='[data-nav]'?nav:[]};
 const context=vm.createContext({window:{location,scrollTo(){}},location,document,state,rpc,esc:escape,
  checkinPanel:null,isStudyTab:false,
  $:selector=>nodes[selector]||null,heading:(title,subtitle)=>`<h1>${escape(title)}</h1><p>${escape(subtitle)}</p>`,
  rememberDraft:()=>true,flushEdits:async()=>{},destroyEditor(){},loading:()=>'<p>加载中</p>',toast(){},
  beginOperation:async()=>{state.operationId='test-demo-operation';},
  features:{invalidate(){},handleAction:async()=>false,demoStage:()=>''},
 });
 vm.runInContext(settingsSource,context);
 context.settingsPanel=context.window.createSparkTutorSettings({rpc,esc:escape,
  renderEnvironment:async host=>{await rpc('getEnvironmentReport');host.insertAdjacentHTML('beforeend','<section id="environment-report">环境检查</section>');}});
 vm.runInContext(app.match(/^const names = .*;$/m)[0],context);
 vm.runInContext(app.slice(app.indexOf('function renderDemo('),app.indexOf('async function action(')),context);
 vm.runInContext(app.slice(app.indexOf('async function action('),app.indexOf('async function saveSettings(')),context);
 return{calls,nodes,nav,content,document,state,
  async navigate(hash){location.hash=hash;await context.route();},
  async run(){await context.action({dataset:{action:'run-demo'},innerHTML:'运行演示',disabled:false,isConnected:true});},
 };
}

test('opening and refreshing settings exposes a folded example link without running a scenario',async()=>{
 const view=demoNavigationView();
 for(let i=0;i<2;i++){
  await view.navigate('#settings');
  assert.ok(view.content.innerHTML.includes('<h1>设置</h1>'));
  assert.match(view.content.innerHTML,/<details\b[^>]*class="settings-demo"[^>]*>\s*<summary>学习诊断示例<\/summary>/);
  assert.ok(!/<details\b[^>]*class="settings-demo"[^>]*\bopen\b/.test(view.content.innerHTML));
  assert.match(view.content.innerHTML,/<a\b[^>]*href="#demo"[^>]*>打开学习诊断示例/);
  assert.ok(view.content.innerHTML.includes('与自己的学习记录隔离'));
  assert.ok(!view.content.innerHTML.includes('data-action="run-demo"'));
 }
 assert.deepEqual(view.calls.map(call=>call.method),['webSettings','getEnvironmentReport','webSettings','getEnvironmentReport']);
 assert.equal(view.nav.filter(link=>link.active).map(link=>link.dataset.nav).join(','),'settings');
});

test('settings exposes diagnostic examples while the environment check is still pending',async()=>{
 const source=fs.readFileSync(path.join(assets,'settings.js'),'utf8');
 const context={window:{location:{hash:'#settings'}},document:{querySelector:()=>null}};
 vm.runInNewContext(source,context);
 const calls=[];
 let finishEnvironment,enteredEnvironment,renderComplete=false;
 const pendingEnvironment=new Promise(resolve=>{finishEnvironment=resolve;});
 const environmentStarted=new Promise(resolve=>{enteredEnvironment=resolve;});
 const host={isConnected:true,innerHTML:'',querySelector:()=>null,
  insertAdjacentHTML(position,html){this.innerHTML+=html;}};
 const api=context.window.createSparkTutorSettings({
  rpc:async method=>{calls.push(method);return{provider:'auto',resolvedProvider:'none',mode:'auto'};},
  esc:escape,renderEnvironment:()=>{enteredEnvironment();return pendingEnvironment;},
 });
 const rendering=api.render(host).then(()=>{renderComplete=true;});
 try{
  await environmentStarted;
  assert.equal(renderComplete,false);
  assert.match(host.innerHTML,/<details\b[^>]*class="settings-demo"/);
  assert.match(host.innerHTML,/<a\b[^>]*href="#demo"[^>]*>打开学习诊断示例/);
  assert.deepEqual(calls,['webSettings']);
  assert.ok(!calls.includes('runDemoScenario'));
 }finally{finishEnvironment();await rendering;}
 assert.equal(renderComplete,true);
});

test('account settings retain appearance and diagnostic examples when isolated execution is disabled',async()=>{
 const source=fs.readFileSync(path.join(assets,'settings.js'),'utf8');
 const mode={value:'local',disabled:false,notice:'',insertAdjacentHTML(position,html){this.notice+=html;}};
 const host={isConnected:true,innerHTML:'',querySelector:selector=>selector==='#execution-mode-select'?mode:null,
  insertAdjacentHTML(position,html){this.innerHTML=position==='afterbegin'?html+this.innerHTML:this.innerHTML+html;}};
 const context={window:{location:{hash:'#settings'},SparkTutorAccount:{user:{id:'learner-account'}},
  SparkTutorAppearance:{panel:()=>'<section id="appearance-preferences">外观偏好</section>'}},document:{querySelector:()=>null}};
 vm.runInNewContext(source,context);
 const calls=[];
 const api=context.window.createSparkTutorSettings({rpc:async method=>{calls.push(method);return{provider:'auto',resolvedProvider:'none',mode:'dry_run'};},esc:escape});
 await api.render(host);
 assert.equal(mode.value,'dry_run');assert.equal(mode.disabled,true);
 assert.ok(mode.notice.includes('不能通过要求真实执行的课程题'));
 assert.ok(host.innerHTML.includes('id="appearance-preferences"'));
 assert.match(host.innerHTML,/<details\b[^>]*class="settings-demo"/);
 assert.match(host.innerHTML,/<a\b[^>]*href="#demo"/);
 assert.ok(!host.innerHTML.includes('href="#data"'));
 assert.deepEqual(calls,['webSettings']);
});

test('managed account settings preserve isolated mode and report execution service readiness',async()=>{
 const source=fs.readFileSync(path.join(assets,'settings.js'),'utf8');
 const mode={value:'local',disabled:false,notice:'',insertAdjacentHTML(position,html){this.notice+=html;}};
 const host={isConnected:true,innerHTML:'',querySelector:selector=>selector==='#execution-mode-select'?mode:null,
  insertAdjacentHTML(position,html){this.innerHTML+=html;}};
 const context={window:{location:{hash:'#settings'},SparkTutorAccount:{user:{id:'learner-account'}}},document:{querySelector:()=>null}};
 vm.runInNewContext(source,context);
 const calls=[];
 const api=context.window.createSparkTutorSettings({rpc:async method=>{calls.push(method);
  if(method==='getExecutionServiceStatus')return{enabled:true,ready:false,message:'隔离执行镜像未就绪'};
  return{provider:'auto',resolvedProvider:'none',mode:'isolated',accountExecutionManaged:true,isolatedExecutionEnabled:true};
 },esc:escape});
 await api.render(host);
 assert.equal(mode.value,'isolated');assert.equal(mode.disabled,true);
 assert.ok(mode.notice.includes('每次提交在独立 Spark 环境运行'));
 assert.ok(mode.notice.includes('隔离执行镜像未就绪'));
 assert.ok(!mode.notice.includes('目前仅检查语法'));
 assert.deepEqual(calls,['webSettings','getExecutionServiceStatus']);
});

test('a direct demo hash keeps the settings parent active and provides a working return route',async()=>{
 const view=demoNavigationView();await view.navigate('#demo');
 assert.ok(view.content.innerHTML.includes('<h1>学习诊断示例</h1>'));
 assert.equal(view.nodes['#breadcrumb'].textContent,'设置 / 学习诊断示例');
 assert.equal(view.document.title,'学习诊断示例 · SparkTutor');
 assert.equal(view.nav.filter(link=>link.active).map(link=>link.dataset.nav).join(','),'settings');
 assert.deepEqual(view.calls,[]);
 const back=view.content.innerHTML.match(/<a\b[^>]*href="([^"]+)"[^>]*>← 返回设置<\/a>/);
 assert.equal(back?.[1],'#settings');await view.navigate(back[1]);
 assert.ok(view.content.innerHTML.includes('<h1>设置</h1>'));
 assert.equal(view.nodes['#breadcrumb'].textContent,'设置');
 assert.ok(!view.calls.some(call=>call.method==='runDemoScenario'));
});

test('moving the entry preserves both scenarios and modes and requires an explicit run action',async()=>{
 const view=demoNavigationView();await view.navigate('#demo');
 for(const value of ['transfer_retry','transfer_first_pass','recorded','spark'])assert.ok(view.content.innerHTML.includes(`value="${value}"`));
 assert.ok(view.content.innerHTML.includes('data-action="run-demo"'));assert.deepEqual(view.calls,[]);
 for(const scenarioId of ['transfer_retry','transfer_first_pass'])for(const mode of ['recorded','spark']){
  view.nodes['#demo-scenario'].value=scenarioId;view.nodes['#demo-mode'].value=mode;await view.run();
  const request=view.calls.at(-1);assert.equal(request.method,'runDemoScenario');
  assert.deepEqual({...request.params},{scenarioId,mode,operationId:'test-demo-operation'});
  assert.equal(view.state.busy,false);assert.equal(view.state.operationId,'');
 }
 assert.equal(view.calls.length,4);
});

function loginNavigationView(href,mustChangePassword=false){
 const nodes={},events={},navigation=[];
 for(const selector of ['#login-form','#login-submit','#login-message','#username','#display-name','#password'])nodes[selector]={value:'test-value',textContent:'',disabled:false,addEventListener:(name,handler)=>events[name]=handler};
 const location={href,reload(){navigation.push({type:'reload',href:this.href});}};
 vm.runInNewContext(fs.readFileSync(path.join(assets,'login.js'),'utf8'),{
  document:{querySelector:selector=>nodes[selector],querySelectorAll:()=>[]},location,URL,
  history:{replaceState(_state,_title,next){location.href=next;navigation.push({type:'replace',href:next});}},
  localStorage:{setItem(){}},fetch:async()=>({ok:true,json:async()=>({user:{mustChangePassword}})})});
 return{nodes,navigation,submit:()=>events.submit({preventDefault(){}})};
}
for(const scenario of [
 {name:'ordinary login opens the learning dashboard',href:'http://example.test/#courses',hash:'#home'},
 {name:'login from a former study tab opens the dashboard and removes study mode',href:'http://example.test/?lang=zh&study=1#lesson',hash:'#home'},
 {name:'required password changes keep their account landing page without study mode',href:'http://example.test/?study=1#lesson',hash:'#account',mustChange:true},
])test(scenario.name,async()=>{
 const view=loginNavigationView(scenario.href,scenario.mustChange);await view.submit();
 assert.deepEqual(view.navigation.map(item=>item.type),['replace','reload']);
 const target=new URL(view.navigation[1].href);
 assert.equal(target.hash,scenario.hash);assert.equal(target.searchParams.has('study'),false);
 assert.equal(target.searchParams.get('lang'),new URL(scenario.href).searchParams.get('lang'));
 assert.equal(view.nodes['#login-message'].textContent,'');assert.equal(view.nodes['#login-submit'].disabled,false);
});
function studyNavigationView({resume=null,tabHref='about:blank'}={}){
 const app=fs.readFileSync(path.join(assets,'app.js'),'utf8'),opened=[],navigated=[],toasts=[];let focusCount=0;
 const tab={location:{get href(){return tabHref;},set href(value){tabHref=value;navigated.push(value);}},focus(){focusCount++;}};
 const location=new URL('http://example.test/#home');
 const state={busy:false,depth:'advanced',depthExplicit:false,scopeCourseId:'core',dashboard:{resume,
  courses:[{id:'core',lessons:[{index:0,status:'completed',depth:'beginner'},{index:1,status:'in_progress',depth:'intermediate'}]},
   {id:'project',lessons:[{index:2,status:'not_started',depth:'advanced'}]}],
  diagnosis:{recommendedExercise:{courseId:'project',lessonId:'pipeline',stepId:'filter'}}}};
 const context=vm.createContext({state,location,isStudyTab:false,accountUser:'test-learner',URL,
  window:{open:(...args)=>{opened.push(args);return tab;}},toast:(...args)=>toasts.push(args),
  rpc:()=>{throw Error('The dashboard must leave lesson loading to its study tab.');}});
 vm.runInContext(app.slice(app.indexOf('function openStudyTab('),app.indexOf('async function loadLesson(')),context);
 vm.runInContext(app.slice(app.indexOf('async function action('),app.indexOf('async function saveSettings(')),context);
 return{state,location,opened,navigated,toasts,focusCount:()=>focusCount,act:dataset=>context.action({dataset,disabled:false,innerHTML:'Learn'})};
}
test('dashboard learning entry points synchronously open a separate study tab and leave the dashboard in place',async()=>{
 const cases=[
  {dataset:{action:'resume'},request:{courseId:'core',lessonIdx:1,depth:'intermediate'}},
  {dataset:{action:'load',course:'core',index:'4'},request:{courseId:'core',lessonIdx:4,depth:'advanced'}},
  {dataset:{action:'course-start',course:'project'},request:{courseId:'project',lessonIdx:2,depth:'advanced'}},
  {dataset:{action:'recommended'},request:{recommended:{courseId:'project',lessonId:'pipeline',stepId:'filter',scopeCourseId:'core'}}},
 ];
 for(const item of cases){
  const view=studyNavigationView();const completion=view.act(item.dataset);
  // Opening before the returned promise settles preserves browser user activation.
  assert.equal(view.opened.length,1);assert.deepEqual(view.opened[0],['','sparktutor-study-test-learner']);
  assert.equal(view.location.hash,'#home');assert.equal(view.navigated.length,1);
  const target=new URL(view.navigated[0]);assert.equal(target.searchParams.get('study'),'1');
  assert.deepEqual(JSON.parse(decodeURIComponent(target.hash.slice('#study/'.length))),item.request);
  await completion;assert.equal(view.location.hash,'#home');assert.equal(view.focusCount(),1);assert.deepEqual(view.toasts,[]);
 }
});
test('continue learning focuses the existing study tab without navigating away from its unsaved answer',async()=>{
 const view=studyNavigationView({resume:{courseId:'core',lessonIdx:1},tabHref:'http://example.test/?study=1#lesson'});
 const completion=view.act({action:'resume'});
 assert.equal(view.opened.length,1);assert.deepEqual(view.navigated,[]);await completion;
 assert.equal(view.focusCount(),1);assert.equal(view.location.hash,'#home');assert.equal(view.state.busy,false);
});
function appSessionView({search='',name='',values={}}={}){
 const app=fs.readFileSync(path.join(assets,'app.js'),'utf8'),stored=new Map(Object.entries(values));
 const context=vm.createContext({window:{name},location:{search},URLSearchParams,
  document:{querySelector:selector=>({content:selector==='meta[name="sparktutor-user"]'?'test-learner':'test-token'})},
  sessionStorage:{getItem:key=>stored.get(key)??null,setItem:(key,value)=>stored.set(key,String(value)),removeItem:key=>stored.delete(key)},
  crypto:{getRandomValues:bytes=>{bytes.fill(9);return bytes;}}});
 vm.runInContext(app.slice(0,app.indexOf('const state =')),context);
 return{stored,name:context.window.name};
}
test('a former named study window releases its name on the dashboard and the next study tab gets an isolated session',()=>{
 const initial={'sparktutor.activeUser':'test-learner','sparktutor.session':'former-study-session','sparktutor.studySession':'1','sparktutor.lastLesson':'saved-navigation'};
 const dashboard=appSessionView({name:'sparktutor-study-test-learner',values:initial});
 assert.equal(dashboard.name,'');assert.equal(dashboard.stored.has('sparktutor.studySession'),false);
 assert.equal(dashboard.stored.get('sparktutor.session'),'former-study-session');
 const study=appSessionView({search:'?study=1',name:'sparktutor-study-test-learner',values:Object.fromEntries(dashboard.stored)});
 assert.notEqual(study.stored.get('sparktutor.session'),dashboard.stored.get('sparktutor.session'));
 assert.equal(study.stored.get('sparktutor.studySession'),'1');assert.equal(study.stored.has('sparktutor.lastLesson'),false);
 const refreshed=appSessionView({search:'?study=1',name:study.name,values:Object.fromEntries(study.stored)});
 assert.equal(refreshed.stored.get('sparktutor.session'),study.stored.get('sparktutor.session'));
 assert.equal(appSessionView({name:'unrelated-window',values:initial}).name,'unrelated-window');
});
test('the main sidebar keeps the dashboard and catalog without a study-tab navigation item',()=>{
 const index=fs.readFileSync(path.join(assets,'index.html'),'utf8');
 const primary=index.match(/<nav\b[^>]*aria-label="主导航"[^>]*>([\s\S]*?)<\/nav>/)?.[1];
 assert.ok(primary);assert.match(primary,/href="#home"[^>]*data-nav="home"/);assert.match(primary,/href="#courses"[^>]*data-nav="courses"/);
 assert.ok(!primary.includes('data-nav="lesson"'));assert.ok(!primary.includes('data-action="study-tab"'));assert.ok(!primary.includes('学习标签页'));
});
