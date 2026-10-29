'use strict';

window.createSparkTutorFeatures = function ({rpc,esc,state,date,pct,toast,refresh,rotateSession,currentCode}) {
  let sequence = 0;
  const label = {knowledge:'知识任务表现',debugging:'失败过程修复率',transfer:'首次迁移通过率',hint_dependency:'提示使用覆盖率'};
  const num = value => Number.isFinite(Number(value)) ? Number(value) : 0;
  const options = (courses,value) => `<option value="">全部课程</option>${(courses||[]).map(c=>`<option value="${esc(c.id)}" ${c.id===value?'selected':''}>${esc(c.title)}</option>`).join('')}`;
  const scopeControls = () => `<div class="scope-controls"><label for="diagnosis-scope">画像与继续学习范围</label><select id="diagnosis-scope">${options(state.dashboard?.courses,state.scopeCourseId)}</select><span>目录仍展示全部课程，跨课前置使用完整历史。</span></div>`;
  function knowledgeSection() {
    const components=state.dashboard?.knowledgeComponents || state.dashboard?.diagnosis?.knowledgeComponents || {};
    const entries=Object.entries(components);
    if(!entries.length)return '<section class="panel evidence-section"><h2>知识点证据</h2><p class="disclaimer">尚无合格的知识点评估证据。完成课程测试后再查看。</p></section>';
    return `<section class="panel evidence-section"><h2>知识点证据</h2><p class="disclaimer">每个知识点统计相关题目的最近一次有效评估。同一题可关联多个知识点，题目数不能直接相加。每点最多展示 ${num(state.dashboard?.knowledgeEvidenceLimit||100)} 条明细。</p><div class="knowledge-list">${entries.map(([key,item])=>`<details><summary><strong>${esc(item.name||key)}</strong><span>${num(item.latestPassedTasks)} / ${num(item.evaluatedTasks)} 道有效评估通过</span></summary>${(state.dashboard?.knowledgeComponentEvidence?.[key]||[]).map(task=>`<p>${esc(task.title||task.taskId)} · ${task.passed?'最近评估通过':'最近评估未通过'}${task.assisted?' · 使用过帮助':''}</p>`).join('')}<p>最近有效评估通过 ${num(item.latestPassedTasks)} 题，共 ${num(item.evaluatedTasks)} 道已评估题。完整过程可在学习历史中查看。</p></details>`).join('')}</div></section>`;
  }
  function historySupplement() {
    state.trend=null;
    return '<section class="panel evidence-section" id="trend-panel"><div class="section-title"><h2>四维证据趋势</h2><button class="btn small" data-action="feature-trend">查看最近 20 个证据点</button></div><p class="disclaimer">按需重建历史时点；不是经过标定的能力成长曲线，提示覆盖仅描述求助。</p><div id="trend-content"></div></section>';
  }
  function trendChart(data,key) {
    const points=data.points||[], width=440,height=170,left=38,top=16,bottom=38,right=15;
    const x=index=>left+(width-left-right)*(points.length===1?.5:index/(points.length-1));
    const y=score=>top+(height-top-bottom)*(1-score/100);
    let paths=[],segment=[];
    points.forEach((p,i)=>{const d=p.dimensions.find(d=>d.key===key);if(d?.score==null){if(segment.length)paths.push(segment);segment=[];}else segment.push(`${segment.length?'L':'M'}${x(i).toFixed(1)} ${y(d.score).toFixed(1)}`);});
    if(segment.length)paths.push(segment);
    return `<article class="trend-chart"><h3>${esc(label[key])}</h3><svg viewBox="0 0 ${width} ${height}" role="img" aria-label="${esc(label[key])}，纵轴百分比，横轴证据点顺序。缺证据处断开。"><title>${esc(label[key])}（%）</title>${[0,50,100].map(v=>`<line x1="${left}" x2="${width-right}" y1="${y(v)}" y2="${y(v)}" class="chart-grid"/><text x="${left-6}" y="${y(v)+4}" text-anchor="end">${v}</text>`).join('')}<text x="6" y="12">%</text><line x1="${left}" x2="${width-right}" y1="${height-bottom}" y2="${height-bottom}" class="chart-axis"/>${paths.map(p=>`<path d="${p.join(' ')}" class="chart-line"/>`).join('')}${points.map((p,i)=>{const d=p.dimensions.find(d=>d.key===key);return d?.score==null?'':`<circle cx="${x(i)}" cy="${y(d.score)}" r="3" class="chart-point"/>`;}).join('')}<text x="${left}" y="${height-18}">1</text><text x="${width-right}" y="${height-18}" text-anchor="end">${points.length}</text><text x="${width/2}" y="${height-2}" text-anchor="middle">证据点顺序</text></svg></article>`;
  }
  async function loadTrend() {
    const host=document.querySelector('#trend-content');if(!host)return;
    const own=++sequence;host.innerHTML='<p class="disclaimer">正在重建历史证据…</p>';
    try {
      const data=await rpc('getLearningTrend',{courseId:state.historyCourse,window:state.historyWindow,snapshotEventId:state.historyData?.snapshotEventId,windowEnd:state.historyData?.windowEnd});
      if(own!==sequence||!host.isConnected)return;
      state.trend=data;
      host.innerHTML=(data.points.length?`<p class="disclaimer">展示最近 ${data.points.length} / ${data.total} 个证据点；更早记录仍参与计算。</p><div class="trend-grid">${Object.keys(label).map(key=>trendChart(data,key)).join('')}</div><div class="trend-point-list">${data.points.map((p,i)=>`<button class="btn small" data-action="feature-trend-point" data-index="${i}">${i+1}. ${esc(p.eventLabel)} · ${esc(p.title)}</button>`).join('')}</div><div id="trend-evidence"></div>`:'<p class="disclaimer">当前范围没有可展示的评估证据。只有编辑或求助记录时，不生成评分曲线。</p>')+`<p class="disclaimer">${esc(data.disclaimer)}</p>`;
    } catch(error) {if(own===sequence&&host.isConnected)host.innerHTML=`<p class="feedback">${esc(error.message)}</p><button class="btn small" data-action="feature-trend">重新读取</button>`;}
  }
  async function trendPoint(index) {
    const point=state.trend?.points[index],host=document.querySelector('#trend-evidence');if(!point||!host)return;
    const own=++sequence;
    host.innerHTML=`<h3>${esc(point.title)}</h3><p class="disclaimer">${date(point.timestamp)} · ${esc(point.eventLabel)}</p><div class="review-changes">${point.dimensions.map(d=>`<div class="review-change">${esc(d.name)}<strong>${pct(d.score)}</strong>样本 ${num(d.sampleSize)} · 分子 / 分母 ${num(d.numerator)} / ${num(d.denominator)}</div>`).join('')}</div><div class="trend-review"></div>`;
    const review=await rpc('getLearningReview',{courseId:point.courseId,lessonId:point.lessonId,taskId:point.taskId,eventId:point.eventId,scopeCourseId:state.historyCourse,snapshotEventId:state.trend.snapshotEventId});
    if(own!==sequence||!host.isConnected)return;
    host.querySelector('.trend-review').innerHTML=`<h4>此记录前后</h4><div class="review-changes">${review.changes.map(c=>`<div class="review-change">${esc(c.name)}<strong>${pct(c.beforeScore)} → ${pct(c.afterScore)}</strong>${esc(c.explanation)}</div>`).join('')}</div><p class="disclaimer">${esc(review.reason)}</p>`;
  }
  async function renderEnvironment(host=document.querySelector('#content')) {
    const panel=document.createElement('section');panel.className='panel evidence-section';panel.id='environment-report';panel.innerHTML='<h2>详细环境检查</h2><p class="disclaimer">正在检查当前服务端环境…</p>';host.append(panel);
    await refreshEnvironment(panel);
  }
  async function refreshEnvironment(panel=document.querySelector('#environment-report')) {
    if(!panel)return;const own=++sequence;
    try {
      const report=await rpc('getEnvironmentReport');if(own!==sequence||!panel.isConnected)return;
      const statuses={available:'可用',missing:'缺少',warning:'待检查',unverified:'未验证',needs_setup:'待准备'};
      panel.innerHTML=`<div class="section-title"><h2>详细环境检查</h2><button class="btn small" data-action="feature-environment">刷新检查</button></div><p class="disclaimer">检查的是运行网站的电脑；找到依赖不等于真实 Spark 作业已经通过。本次不启动 Spark、不连接远端或调用模型。</p><div class="environment-checks">${(report.checks||[]).map(c=>`<article><h3>${esc(c.title)} <span class="badge ${c.status==='available'?'':'purple'}">${esc(statuses[c.status]||c.status)}</span></h3><p>${esc(c.summary)}</p>${(c.details||[]).map(d=>`<p class="environment-detail">${esc(d)}</p>`).join('')}${c.nextSteps?.length?`<ul>${c.nextSteps.map(s=>`<li>${esc(s)}</li>`).join('')}</ul>`:''}</article>`).join('')}</div><h3>执行方式：${esc(report.execution?.configuredMode)}</h3><p>${esc(report.execution?.summary)}</p><ul>${(report.execution?.nextSteps||[]).map(s=>`<li>${esc(s)}</li>`).join('')}</ul><div class="notice">修改服务端 Python/Java 环境后需要重新启动网站，再运行一题确认实际结果。</div>`;
    } catch(error) {if(panel.isConnected)panel.innerHTML=`<h2>环境检查未完成</h2><p>${esc(error.message)}</p><button class="btn small" data-action="feature-environment">重试</button>`;}
  }
  function download(body,name,type='application/json') {
    const blob=body instanceof Blob?body:new Blob([typeof body==='string'?body:JSON.stringify(body,null,2)],{type});
    const url=URL.createObjectURL(blob),a=document.createElement('a');a.href=url;a.download=name;a.click();setTimeout(()=>URL.revokeObjectURL(url),2000);
  }
  function comparison(student,solution) {
    const left=String(student).split('\n'),right=String(solution).split('\n');
    // Bounded line diff: large exercises retain the side-by-side view without
    // allocating an unbounded dynamic-programming matrix in the browser.
    const max=left.length*right.length;
    let a=[],b=[];
    if(max<=400000){
      const rows=Array.from({length:left.length+1},()=>new Uint16Array(right.length+1));
      for(let i=left.length-1;i>=0;i--)for(let j=right.length-1;j>=0;j--)rows[i][j]=left[i]===right[j]?1+rows[i+1][j+1]:Math.max(rows[i+1][j],rows[i][j+1]);
      let i=0,j=0;while(i<left.length||j<right.length){if(i<left.length&&j<right.length&&left[i]===right[j]){a.push([i+1,left[i],'']);b.push([j+1,right[j],'']);i++;j++;}else if(i<left.length&&(j===right.length||rows[i+1][j]>=rows[i][j+1])){a.push([i+1,left[i],'removed']);b.push(['','','blank']);i++;}else{a.push(['','','blank']);b.push([j+1,right[j],'added']);j++;}}
    }else{a=left.map((line,i)=>[i+1,line,'']);b=right.map((line,i)=>[i+1,line,'']);}
    const column=(items,title)=>`<section><h4>${title}</h4><pre>${items.map(([n,line,cls])=>`<span class="diff-line ${cls}"><i>${n}</i>${esc(line)||' '}</span>`).join('')}</pre></section>`;
    return `<div class="solution-comparison"><p class="disclaimer">左侧为当前作答，右侧为参考答案。红色表示删除，绿色表示新增；答案查看已记入学习记录。</p><div class="diff-columns">${column(a,'你的代码')}${column(b,'参考答案')}</div></div>`;
  }
  function coachDetails(context) {
    if(!context)return '';
    if(!context.available)return '<details><summary>教练的用途和边界</summary><p class="disclaimer">调试教练在已配置的编程练习中开放，支持范围以练习入口为准。教练结合当前代码和可追溯记录提出排查方向，不修改代码，也不代替提交判题。</p></details>';
    return `${context.answerViewed?'<div class="notice warning">本题已查看参考答案；之后的作答与提示属于受答案支持的学习，不能当作独立掌握证据。</div>':''}<details class="coach-history"><summary>查看课程历史画像与当前题目依据</summary><p class="disclaimer">这些比例汇总课程历史，不是当前题目的得分；缺证据不表示零分，提示覆盖只描述求助方式。</p><div class="review-changes">${(context.dimensions||[]).map(d=>`<div class="review-change">${esc(d.name)}<strong>${pct(d.score)}</strong>样本 ${num(d.sampleSize)} · 分子 / 分母 ${num(d.numerator)} / ${num(d.denominator)}</div>`).join('')}</div>${coachEvidence(context.evidence,context.answerViewed)}</details><details><summary>教练的用途和边界</summary><p class="disclaimer">当前题目已配置调试教练。教练从可核查记录中提出待验证方向，不自动修改代码、不代替提交判题。小样例不运行学生代码，不作为新的课程通过或迁移证据。</p></details>`;
  }
  function coachEvidence(items=[],answerViewed=false) {
    return `<details class="coach-evidence"><summary>本次依据（${items.length} 条）</summary>${items.map(item=>`<article><strong>${esc(item.title)}</strong><p class="evidence-id">${esc(item.id)}</p><p>${esc(String(item.id).startsWith('task:')?'当前题目契约：请核对题面输入、输出和课程通过条件。':item.description)}</p></article>`).join('')}${answerViewed?'<p>已记录答案查看，不将后续同题表现解释为未受答案影响的独立作答。</p>':''}</details>`;
  }
  function demoStage(stage,index) {
    const diagnosis=stage.diagnosis||{},recommendation=diagnosis.recommendedExercise,review=stage.review;
    return `<section class="panel demo-stage"><h3>${String(index+1).padStart(2,'0')} / ${esc(stage.title)}</h3><p>${esc(stage.explanation)}</p><div class="demo-scores">${(diagnosis.dimensions||[]).map(d=>`<span>${esc(d.name)} · ${pct(d.score)}</span>`).join('')}</div><details class="demo-details"><summary>查看本阶段诊断、推荐与记录</summary><h4>阶段诊断</h4><p>${esc(diagnosis.diagnosis||'')}</p><div class="review-changes">${(diagnosis.dimensions||[]).map(d=>`<div class="review-change">${esc(d.name)}<strong>${pct(d.score)}</strong>样本 ${num(d.sampleSize)} · ${esc(d.summary||'')}</div>`).join('')}</div><h4>只读推荐</h4>${recommendation?`<p><strong>${esc(recommendation.title)}</strong> · ${esc(recommendation.reason)}</p>`:`<p>${esc(diagnosis.recommendationReason||'暂无合适候选。')}</p>`}<p class="disclaimer">演示推荐只用于解释规则，不会打开或改变自己的学习任务。</p><h4>截至本阶段的演示事件</h4><div class="event-list">${(stage.events||[]).map(event=>`<div class="event-item"><strong>${esc(event.eventType)}</strong><time>${date(event.timestamp)}</time><p>${esc(event.reason||'')}${event.passed==null?'':event.passed?' · 有效通过':' · 未通过'}${event.answerInfluenced?' · 已受答案影响':''}</p></div>`).join('')}</div>${review?`<h4>最近记录前后复盘</h4><div class="review-changes">${(review.changes||[]).map(c=>`<div class="review-change">${esc(c.name)}<strong>${pct(c.beforeScore)} → ${pct(c.afterScore)}</strong>${esc(c.explanation)}</div>`).join('')}</div><p class="disclaimer">${esc(review.reason||'')}</p>`:''}<h4>阶段核查</h4><ul>${(stage.checks||[]).map(check=>`<li>${check.passed?'通过':'未通过'}：${esc(check.label)}</li>`).join('')}</ul></details></section>`;
  }
  function difficultySelect() {
    if(state.lesson?.practiceMode)return '';
    return `<label class="lesson-depth-label">难度 <select id="lesson-depth">${[['beginner','入门'],['intermediate','进阶'],['advanced','挑战']].map(([value,title])=>`<option value="${value}" ${state.depth===value?'selected':''}>${title}</option>`).join('')}</select></label>`;
  }
  async function handleAction(button) {
    const type=button.dataset.action;
    if(!type.startsWith('feature-'))return false;
    if(type==='feature-trend')await loadTrend();
    if(type==='feature-trend-point')await trendPoint(Number(button.dataset.index));
    if(type==='feature-environment')await refreshEnvironment();
    if(type==='feature-editor-search')window.activeSparkTutorEditor?.search();
    if(type==='feature-editor-download')download(currentCode(),'sparktutor-exercise.py','text/x-python;charset=utf-8');
    if(type==='feature-reset-lesson'){
      if(!state.lesson)return true;
      if(!window.confirm('重置当前课节？本课的进度和受管练习代码会清除；其他课节及辅助文件保留。'))return true;
      await rpc('resetWebLesson',{courseId:state.courseId,lessonId:state.lesson.lessonId,contextToken:state.lesson.contextToken,confirmation:'reset-lesson'});
      const prefix=`${state.storagePrefix||'sparktutor.'}draft.${state.courseId}.${state.lesson.lessonId}.`;
      for(const key of Object.keys(localStorage))if(key.startsWith(prefix))localStorage.removeItem(key);
      state.edit=null;state.draftDirty=false;state.lesson=null;state.lastCode='';state.passed=false;
      toast('当前课节已重置。');await refresh();
    }
    return true;
  }
  async function handleChange(event) {
    if(event.target.id==='diagnosis-scope'){state.scopeCourseId=event.target.value;sessionStorage.setItem('sparktutor.scope',state.scopeCourseId);await refresh();return true;}
    return false;
  }
  return {scopeControls,knowledgeSection,historySupplement,renderEnvironment,comparison,coachDetails,coachEvidence,demoStage,difficultySelect,handleAction,handleChange,download,invalidate:()=>{sequence++;}};
};
