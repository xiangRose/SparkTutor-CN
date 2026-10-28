(function(){
 'use strict';
 let mode='login';
 const $=s=>document.querySelector(s);
 document.querySelectorAll('[data-mode]').forEach(button=>button.addEventListener('click',()=>{
  mode=button.dataset.mode;
  $('#login-form').dataset.mode=mode;
  document.querySelectorAll('[data-mode]').forEach(b=>b.setAttribute('aria-selected',String(b===button)));
  $('#display-name-field').hidden=mode!=='register';$('#confirm-field').hidden=mode!=='register';
  $('#confirm-password').required=mode==='register';$('#password').minLength=mode==='register'?10:1;
  $('#password').autocomplete=mode==='register'?'new-password':'current-password';
  $('#login-title').textContent=mode==='register'?'开启你的学习旅程':'欢迎回来';
  $('#login-intro').textContent=mode==='register'?'创建账号，保存属于你的每一步进步。':'你的课程进度与练习代码，已为你留好位置。';
  $('#login-submit').textContent=mode==='register'?'创建账号 →':'登录学习空间 →';$('#login-message').textContent='';
 }));
 $('#login-form').addEventListener('submit',async event=>{
  event.preventDefault();const button=$('#login-submit'),message=$('#login-message');
  if(mode==='register'&&$('#password').value!==$('#confirm-password').value){message.textContent='两次输入的密码不一致。';return;}
  button.disabled=true;message.textContent='';
  try{
   const response=await fetch(`/api/auth/${mode}`,{method:'POST',headers:{'Content-Type':'application/json','X-SparkTutor-Auth':'1'},body:JSON.stringify({username:$('#username').value.trim(),displayName:$('#display-name').value.trim(),password:$('#password').value})});
   let data;try{data=await response.json();}catch{throw Error('暂时无法连接学习空间，请稍后刷新重试。');}
   if(!response.ok)throw Error(data.error||'操作未完成，请重试。');
   if(mode==='register'){$('#login-tab').click();message.textContent='注册成功，请使用新账号登录。';$('#password').value='';$('#password').focus();}
   else{try{localStorage.setItem('sparktutor.auth.changed',String(Date.now()));}catch{}const target=new URL(location.href);target.hash=data.user.mustChangePassword?'account':(target.searchParams.get('study')==='1'?'lesson':'home');location.replace(target.href);location.reload();}
  }catch(error){message.textContent=error.message||'网络连接中断，请稍后重试。';}
  finally{button.disabled=false;}
 });
})();
