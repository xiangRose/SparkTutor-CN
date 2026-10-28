/* Only visual preferences are stored here; account and AI settings stay separate. */
(function () {
  'use strict';
  const key = 'sparktutor.appearance';
  const defaults = {size:'18',font:'system',mode:'system',accent:'forest',spacing:'comfortable'};
  const allowed = {size:['16','18','20','22'],font:['system','sans','serif'],mode:['system','light','dark'],accent:['forest','blue','violet','amber'],spacing:['comfortable','compact']};
  const fonts = {system:'"Segoe UI", "PingFang SC", "Microsoft YaHei", system-ui, sans-serif',sans:'"Microsoft YaHei", "PingFang SC", sans-serif',serif:'"Noto Serif SC", "Songti SC", SimSun, serif'};
  const accents = {forest:['#255540','#deeddc','#193b2b'],blue:['#245cb0','#e2edff','#123269'],violet:['#7040ad','#efe5fc','#46256c'],amber:['#885619','#faedcf','#59370f']};
  const media = window.matchMedia('(prefers-color-scheme: dark)');
  let preferences = {...defaults};
  function normalize(value) {
    return Object.fromEntries(Object.entries(defaults).map(([name,fallback])=>[name,allowed[name].includes(String(value?.[name])) ? String(value[name]) : fallback]));
  }
  function read() { try { return normalize(JSON.parse(localStorage.getItem(key))); } catch { return {...defaults}; } }
  function apply() {
    const root = document.documentElement;
    root.style.fontSize = `${preferences.size}px`;
    root.style.setProperty('--ui-font',fonts[preferences.font]);
    ['--accent','--accent-soft','--accent-deep'].forEach((name,i)=>root.style.setProperty(name,accents[preferences.accent][i]));
    root.dataset.theme = preferences.mode === 'system' ? (media.matches ? 'dark' : 'light') : preferences.mode;
    root.dataset.spacing = preferences.spacing;
    root.style.colorScheme = root.dataset.theme;
  }
  function panel() {
    const select = (name,label,options)=>`<div class="field"><label for="appearance-${name}">${label}</label><select id="appearance-${name}" data-appearance="${name}">${options.map(([value,text])=>`<option value="${value}"${preferences[name]===value?' selected':''}>${text}</option>`).join('')}</select></div>`;
    return `<section class="panel appearance-panel" aria-labelledby="appearance-title"><div class="section-title"><div><h2 id="appearance-title">外观设置</h2><p>调整后立即生效，并记住这台浏览器的偏好。</p></div><button type="button" class="btn small" data-appearance-reset>恢复默认</button></div><div class="appearance-options">${select('size','界面字号',[['16','标准'],['18','舒适（默认）'],['20','大字'],['22','特大字']])}${select('font','界面字体',[['system','系统字体'],['sans','黑体'],['serif','宋体']])}${select('mode','日间与夜间',[['system','跟随系统'],['light','日间'],['dark','夜间']])}${select('accent','主题色',[['forest','森林绿'],['blue','晴空蓝'],['violet','鸢尾紫'],['amber','暖琥珀']])}${select('spacing','界面间距',[['comfortable','舒适'],['compact','紧凑']])}</div><div class="appearance-preview"><strong>让每次学习，都看得舒服。</strong><p>中文与 English 清晰可读，代码保留等宽字体。</p><span class="badge">实时预览</span></div><p id="appearance-status" role="status" class="disclaimer"></p></section>`;
  }
  function persist() {
    apply();
    let message = '外观偏好已保存。';
    try { localStorage.setItem(key,JSON.stringify(preferences)); } catch { message = '外观已应用；浏览器未允许保存，刷新后将恢复默认。'; }
    const status = document.querySelector('#appearance-status');
    if (status) status.textContent = message;
  }
  document.addEventListener('change',event=>{
    const name=event.target.dataset?.appearance;
    if (!allowed[name]?.includes(event.target.value)) return;
    preferences[name]=event.target.value; persist();
  });
  document.addEventListener('click',event=>{
    if (!event.target.closest('[data-appearance-reset]')) return;
    preferences={...defaults}; persist();
    document.querySelectorAll('[data-appearance]').forEach(input=>{input.value=preferences[input.dataset.appearance];});
  });
  media.addEventListener('change',apply);
  window.addEventListener('storage',event=>{if(event.key===key||event.key===null){preferences=read();apply();document.querySelectorAll('[data-appearance]').forEach(input=>{input.value=preferences[input.dataset.appearance];});}});
  preferences=read(); apply();
  window.SparkTutorAppearance={panel};
})();
