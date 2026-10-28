(function(){
 'use strict';
 const meta=name=>document.querySelector(`meta[name="${name}"]`)?.content||'';
 const user={id:meta('sparktutor-user'),name:meta('sparktutor-name'),role:meta('sparktutor-role'),mustChange:meta('sparktutor-password-change')==='true'};
 const esc=value=>String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
 const $=selector=>document.querySelector(selector);
 async function api(path,values){
  const response=await fetch(path,{method:values?'POST':'GET',headers:{'Content-Type':'application/json','X-SparkTutor-Auth':'1','X-SparkTutor-Token':meta('sparktutor-token')},...(values?{body:JSON.stringify(values)}:{})});
  let data;try{data=await response.json();}catch{throw Error('连接暂时中断，请稍后重试。');}
  if(response.status===401){location.reload();throw Error('请重新登录。');}
  if(!response.ok)throw Error(data.error||'操作未完成。');return data;
 }
 function signal(){try{localStorage.setItem('sparktutor.auth.changed',String(Date.now()));}catch{}}
 function renderAccount(){
  $('#content').innerHTML=`<div class="page-heading"><div><p class="eyebrow">YOUR ACCOUNT</p><h1>我的账号</h1><p>学习进度、历史、代码与模型配置都保存在你的专属空间。</p></div></div><div class="account-layout"><section class="panel"><h2>账号信息</h2><div class="account-profile"><span class="account-avatar">${esc(user.name.slice(0,1))}</span><div class="account-profile-name"><strong>${esc(user.name)}</strong><p class="disclaimer">${user.role==='admin'?'管理员':'学习者'}</p></div></div>${user.role==='admin'?'<a class="btn" href="#admin">进入管理后台 →</a>':''}<p class="disclaimer">退出后可在其他设备登录，继续查看同一个账号的学习记录。</p><button class="btn" data-account-action="logout">退出登录</button></section><section class="panel"><h2>${user.mustChange?'请先修改初始密码':'修改密码'}</h2><p class="disclaimer">${user.mustChange?'这是首次登录或管理员重置后的密码。修改后再进入学习空间。':'修改后，所有设备需要使用新密码重新登录。'}</p><form data-account-form="password"><div class="field"><label for="old-password">原密码</label><input id="old-password" type="password" autocomplete="current-password" required maxlength="128"></div><div class="field"><label for="new-password">新密码</label><input id="new-password" type="password" autocomplete="new-password" required minlength="10" maxlength="128"><small>10–128 个字符，建议组合字母、数字和符号。</small></div><div class="field"><label for="new-password-confirm">确认新密码</label><input id="new-password-confirm" type="password" autocomplete="new-password" required maxlength="128"></div><p class="account-message" role="status"></p><button class="btn primary" type="submit">保存新密码</button></form></section></div>`;
 }
 async function renderAdmin(){
  if(user.role!=='admin'){location.hash='home';return;}
  const route=location.hash,{users}=await api('/api/admin/users');if(location.hash!==route)return;
  const active=users.filter(u=>u.enabled).length;
  $('#content').innerHTML=`<div class="page-heading"><div><p class="eyebrow">WORKSPACE ADMINISTRATION</p><h1>管理后台</h1><p>管理学习者账号与访问状态。停用账号会立即撤销其登录。</p></div></div><div class="admin-stats"><section class="panel">全部账号<strong>${users.length}</strong></section><section class="panel">正常账号<strong>${active}</strong></section><section class="panel">管理员<strong>${users.filter(u=>u.role==='admin').length}</strong></section></div><div class="admin-grid"><section class="panel"><div class="section-title"><h2>用户管理</h2><button class="btn small" data-account-action="refresh-users">刷新</button></div><p id="admin-message" class="account-message" role="status"></p><div class="admin-list">${users.map(u=>`<article class="admin-user"><div class="admin-user-info"><h3>${esc(u.displayName)} <span class="badge">${u.role==='admin'?'管理员':'学习者'}</span></h3><p>账号：${esc(u.username)} · ${u.enabled?'正常':'已停用'}</p><p>最近登录：${u.lastLogin?new Date(u.lastLogin*1000).toLocaleString('zh-CN'):'尚未登录'}</p></div><button class="btn small" data-account-action="user-status" data-user="${esc(u.id)}" data-enabled="${!u.enabled}" ${u.id===user.id?'disabled':''}>${u.enabled?'停用':'启用'}</button><details><summary>重置密码</summary><form class="admin-reset" data-account-form="reset-password" data-user="${esc(u.id)}"><label class="sr-only" for="reset-${esc(u.id)}">新密码</label><input id="reset-${esc(u.id)}" name="password" type="password" required minlength="10" maxlength="128" autocomplete="new-password" placeholder="设置至少 10 位临时密码"><button class="btn small" type="submit">重置</button><p class="account-message" role="status"></p></form></details></article>`).join('')}</div></section><section class="panel"><h2>创建账号</h2><form data-account-form="create-user"><div class="field"><label for="create-name">昵称</label><input id="create-name" required maxlength="40" autocomplete="off"></div><div class="field"><label for="create-username">账号</label><input id="create-username" required minlength="3" maxlength="32" pattern="[A-Za-z0-9_]{3,32}" autocomplete="off"></div><div class="field"><label for="create-password">初始密码</label><input id="create-password" type="password" required minlength="10" maxlength="128" autocomplete="new-password"></div><div class="field"><label for="create-role">角色</label><select id="create-role"><option value="student">学习者</option><option value="admin">管理员</option></select></div><p class="account-message" role="status"></p><button class="btn primary" type="submit">创建账号</button></form></section></div>`;
 }
 document.addEventListener('click',async event=>{
  const button=event.target.closest('[data-account-action]');if(!button)return;
  const action=button.dataset.accountAction;button.disabled=true;
  try{
   if(action==='logout'){await window.SparkTutorAccount.beforeLogout?.();await api('/api/auth/logout',{});window.SparkTutorAccount.afterLogout?.();signal();location.reload();}
   if(action==='refresh-users')await renderAdmin();
   if(action==='user-status'){await api('/api/admin/status',{userId:button.dataset.user,enabled:button.dataset.enabled==='true'});await renderAdmin();}
  }catch(error){const message=$('#admin-message');if(message)message.textContent=error.message;else window.alert(error.message);}
  finally{if(button.isConnected)button.disabled=false;}
 });
 document.addEventListener('submit',async event=>{
  const form=event.target;if(!form.dataset.accountForm)return;event.preventDefault();
  const button=form.querySelector('button[type=submit]'),message=form.querySelector('.account-message');button.disabled=true;message.textContent='';
  try{
   if(form.dataset.accountForm==='password'){
    if($('#new-password').value!==$('#new-password-confirm').value)throw Error('两次输入的新密码不一致。');
    await api('/api/auth/password',{oldPassword:$('#old-password').value,newPassword:$('#new-password').value});signal();location.reload();
   }
   if(form.dataset.accountForm==='create-user'){await api('/api/admin/create',{username:$('#create-username').value.trim(),displayName:$('#create-name').value.trim(),password:$('#create-password').value,role:$('#create-role').value});await renderAdmin();$('#admin-message').textContent='账号已创建。';}
   if(form.dataset.accountForm==='reset-password'){await api('/api/admin/password',{userId:form.dataset.user,newPassword:form.elements.password.value});form.reset();message.textContent='密码已重置，原登录已撤销。请将临时密码告知本人。';}
  }catch(error){message.textContent=error.message;}
  finally{if(button.isConnected)button.disabled=false;}
 });
 window.addEventListener('storage',event=>{if(event.key==='sparktutor.auth.changed')location.reload();});
 window.SparkTutorAccount={user,renderAccount,renderAdmin};
 if(user.id){
  const name=$('.profile > div');if(name)name.innerHTML=`${esc(user.name)}<small>${user.role==='admin'?'管理员':'专属学习空间'}</small>`;
  const nav=document.querySelector('.sidebar nav');
  nav?.insertAdjacentHTML('beforeend',`<a href="#account" data-nav="account"><span class="nav-icon">◉</span>我的账号</a>${user.role==='admin'?'<a href="#admin" data-nav="admin"><span class="nav-icon">⚙</span>管理后台</a>':''}`);
 }
})();
