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
 const host={isConnected:true,innerHTML:'',querySelector:()=>null};
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
 const host={isConnected:true,innerHTML:'',querySelector:selector=>nodes[selector]};
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
