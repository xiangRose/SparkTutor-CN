/* Standalone AI settings. Credential values never enter browser storage. */
(function () {
  'use strict';

  window.createSparkTutorSettings = function ({rpc, esc, icon = () => '', toast = () => {}, renderEnvironment}) {
    let host = null;
    let settings = null;
    let busy = false;
    let sequence = 0;
    const query = selector => host?.querySelector(selector);
    const providerNames = {auto:'自动选择',anthropic:'Anthropic','openai-compatible':'兼容接口',none:'尚未配置',copilot:'Copilot（仅 VS Code）'};
    const executionModeNames = {auto:'自动检测',dry_run:'仅检查 Python 语法',isolated:'账号隔离 Spark',local:'本机 Spark',lakehouse:'Docker Lakehouse',databricks:'Databricks Connect'};
    const selected = (current, value) => current === value ? ' selected' : '';

    function storageStatus() {
      const target = query('#settings-storage-status');
      if (!target || !settings) return;
      const storageMessage = settings.storageWarning || (settings.remember
        ? `已记住本机参数；密钥存储：${settings.secureStorage || '仅服务内存'}。`
        : '当前设置仅用于本标签页的服务会话；密钥不会写入浏览器存储。');
      target.textContent = `当前已保存的执行方式：${executionModeNames[settings.mode] || '尚未确认'}。${storageMessage}`;
      target.classList.toggle('settings-warning', Boolean(settings.storageWarning));
      if (query('#settings-header-names')) query('#settings-header-names').textContent = settings.extraHeaderNames?.join('、') || '暂无';
      if (query('#settings-resolved-provider')) query('#settings-resolved-provider').textContent = providerNames[settings.resolvedProvider] || '尚未配置';
    }

    async function render(target = document.querySelector('#content')) {
      host = target;
      const currentSequence = ++sequence;
      const route = window.location?.hash;
      const result = await rpc('webSettings');
      if (currentSequence !== sequence || !target?.isConnected || route !== window.location?.hash) return;
      settings = result;
      const headers = result.extraHeaderNames?.length ? result.extraHeaderNames.join('、') : '暂无';
      target.innerHTML = `<div class="page-heading"><div><p class="eyebrow">YOUR SPARK JOURNEY</p><h1>设置</h1><p>配置学习环境与 AI 连接，也可以通过辅助示例了解学习画像。运行 Spark 代码不需要模型密钥。</p></div></div>
        <div class="settings-grid settings-parity"><form id="settings-form" class="panel">
          <div class="section-title"><h2>AI 导师与调试教练</h2><span class="badge" id="settings-resolved-provider">${esc(providerNames[result.resolvedProvider] || '尚未配置')}</span></div>
          <div class="field"><label for="ai-provider">导师问答与代码评审</label><select id="ai-provider">
            <option value="auto"${selected(result.provider,'auto')}>自动选择已配置的提供方</option>
            <option value="anthropic"${selected(result.provider,'anthropic')}>Anthropic</option>
            <option value="openai-compatible"${selected(result.provider,'openai-compatible')}>OpenAI 兼容接口</option>
            <option value="copilot" disabled${selected(result.provider,'copilot')}>GitHub Copilot（仅 VS Code）</option>
          </select><small>自动模式优先使用已配置的 Anthropic，再尝试兼容接口。调试教练始终使用下方的兼容接口配置。</small></div>
          <fieldset class="settings-provider-fields"><legend>兼容接口 / 调试教练</legend>
            <div class="field"><label for="ai-base">API 基础地址</label><input id="ai-base" type="url" autocomplete="off" placeholder="https://your-provider.example/v1" value="${esc(result.baseUrl || '')}"><small>填写服务商要求的基础路径；本机 Ollama 等无密钥服务可以留空密钥。</small></div>
            <div class="field"><label for="ai-model">模型名称</label><input id="ai-model" autocomplete="off" value="${esc(result.model || '')}" placeholder="服务商提供的模型标识"></div>
            <div class="field"><label for="ai-key">API 密钥</label><input id="ai-key" type="password" autocomplete="new-password" placeholder="${result.hasApiKey ? '已设置；留空保留当前密钥' : '输入密钥；本机无鉴权服务可留空'}"><button class="text-link settings-inline-action" type="button" data-action="settings-clear-key">清除兼容接口密钥</button></div>
            <details class="settings-headers"><summary>额外请求头</summary><p class="disclaimer">可用于 X-Api-Key 或自定义鉴权。已保存的头名称：<span id="settings-header-names">${esc(headers)}</span>。值不回显；留空保留，输入 {} 或点击清除可删除。</p><label class="sr-only" for="ai-extra-headers">额外请求头 JSON</label><textarea id="ai-extra-headers" rows="4" spellcheck="false" autocomplete="off" placeholder='{"X-Api-Key":"填写服务商要求的值"}'></textarea><button class="text-link settings-inline-action" type="button" data-action="settings-clear-headers">清除额外请求头</button></details>
          </fieldset>
          <fieldset class="settings-provider-fields"><legend>Anthropic</legend>
            <div class="field"><label for="anthropic-model">Anthropic 模型</label><input id="anthropic-model" autocomplete="off" value="${esc(result.anthropicModel || '')}"></div>
            <div class="field"><label for="anthropic-key">Anthropic API 密钥</label><input id="anthropic-key" type="password" autocomplete="new-password" placeholder="${result.hasAnthropicApiKey ? '已设置；留空保留当前密钥' : '输入 Anthropic 密钥'}"><button class="text-link settings-inline-action" type="button" data-action="settings-clear-anthropic">清除 Anthropic 密钥</button></div>
          </fieldset>
          <div class="field"><label for="execution-mode-select">代码执行方式</label><select id="execution-mode-select">${Object.entries(executionModeNames).map(([value,label])=>`<option value="${value}"${selected(result.mode || 'auto',value)}>${label}</option>`).join('')}</select><small>选择后需点击下方“保存设置”才会生效。自动检测找不到可用 Spark 时会退回语法检查；语法通过不代表代码已执行或答案正确。真实运行需要 Python、Java 和 PySpark 等执行环境，与模型密钥无关。请查看环境检查并实际运行课程测试。</small></div>
          <label class="settings-remember"><input id="ai-remember" type="checkbox"${result.remember ? ' checked' : ''}><span>记住这台服务器上的设置</span></label>
          <p class="disclaimer settings-storage-note">勾选后，本机服务新开的学习标签页会使用这些参数。密钥仅在支持的本机安全存储中加密保存（${esc(result.secureStorage || '仅服务内存')}）；不支持时仅保存接口和模型。共享设备建议只使用本次会话。</p>
          <p id="settings-storage-status" class="disclaimer" role="status"></p>
          <div class="inline-actions"><button class="btn primary" type="submit">保存设置</button><button class="btn" type="button" data-action="settings-test-main">检查导师连接</button><button class="btn" type="button" data-action="settings-test-coach">检查教练连接</button></div>
          <p class="disclaimer">连接检查只发送一条短测试消息，不发送题面或代码，也不计入学习、提交或求助记录。</p><div id="settings-connection-status" role="status" aria-live="polite"></div>
        </form><aside class="settings-guidance"><section class="panel"><h2>连接状态如何理解</h2><p>返回正文说明接口能够生成回答；空正文、仅思考或截断回复会标记为尚未验证。认证、额度和网络问题会分别提示，可以手动重试。</p><p class="disclaimer">${esc(result.copilotMessage || 'GitHub Copilot 的模型接口属于 VS Code，独立网页请配置 Anthropic 或兼容模型服务。')}</p></section></aside></div>`;
      if (window.SparkTutorAppearance) target.insertAdjacentHTML('afterbegin',window.SparkTutorAppearance.panel());
      if (window.SparkTutorAccount?.user.id) {
        const mode=query('#execution-mode-select');
        mode.value=result.mode||'dry_run';mode.disabled=true;
        const message=result.isolatedExecutionEnabled
          ? '执行方式由服务器管理：每次提交在独立 Spark 环境运行，只处理当前账号和题目的作业。环境故障、资源繁忙或超时不会记为通过。'
          : '管理员尚未启用账号隔离执行服务，目前仅检查语法，不能通过要求真实执行的课程题。';
        mode.insertAdjacentHTML('afterend',`<small>${esc(message)}</small>`);
        if(result.isolatedExecutionEnabled){
          const status=await rpc('getExecutionServiceStatus');
          mode.insertAdjacentHTML('afterend',`<p class="disclaimer" role="status">${esc(status.message||'执行服务状态尚未确认。')}</p>`);
        }
      }
      storageStatus();
      target.insertAdjacentHTML('beforeend', `<section class="panel settings-tools" aria-labelledby="settings-tools-title">
        <div class="section-title"><h2 id="settings-tools-title">辅助功能</h2></div>
        <details class="settings-demo"><summary>学习诊断示例</summary>
          <p>让系统演示一段“做错题 → 查看提示 → 修改通过 → 尝试新题”的过程，了解学习画像、推荐和复盘如何生成。适合了解系统、团队展示和功能验收。</p>
          <p class="disclaimer">使用预设案例，与自己的学习记录隔离，不计入我的成绩；无需配置 AI 模型。</p>
          <a class="btn" href="#demo">打开学习诊断示例 ${icon('arrow')}</a>
        </details></section>`);
      if (renderEnvironment) await renderEnvironment(target);
    }

    async function save({silent = false} = {}) {
      const form = query('#settings-form');
      if (!form || !form.isConnected) throw new Error('请先打开环境与 AI 设置。');
      const parameters = {
        provider:query('#ai-provider').value, baseUrl:query('#ai-base').value.trim(),
        model:query('#ai-model').value.trim(), apiKey:query('#ai-key').value,
        anthropicModel:query('#anthropic-model').value.trim(), anthropicApiKey:query('#anthropic-key').value,
        remember:query('#ai-remember').checked, mode:query('#execution-mode-select').value,
      };
      const text = query('#ai-extra-headers').value.trim();
      if (text) {
        try { parameters.extraHeaders = JSON.parse(text); }
        catch { throw new Error('额外请求头需要是有效的 JSON 对象。'); }
        if (!parameters.extraHeaders || Array.isArray(parameters.extraHeaders) || typeof parameters.extraHeaders !== 'object') throw new Error('额外请求头需要是 JSON 对象，例如 {"X-Api-Key":"值"}。');
      }
      const result = await rpc('configureWebSettings', parameters);
      settings = result;
      if (form.isConnected) {
        for (const id of ['#ai-key','#anthropic-key','#ai-extra-headers']) { const input = query(id); if (input) input.value = ''; }
        if (query('#ai-key')) query('#ai-key').placeholder = result.hasApiKey ? '已设置；留空保留当前密钥' : '尚未设置密钥';
        if (query('#anthropic-key')) query('#anthropic-key').placeholder = result.hasAnthropicApiKey ? '已设置；留空保留当前密钥' : '尚未设置密钥';
        if (query('#settings-connection-status')) query('#settings-connection-status').textContent = '';
        storageStatus();
      }
      if (!silent) toast(`设置已保存；执行方式：${executionModeNames[result.mode] || '尚未确认'}。${result.storageWarning || ''}`);
      return result;
    }

    async function run(operation) {
      if (busy) return;
      busy = true;
      const controls = [...(query('#settings-form')?.querySelectorAll('button') || [])].map(button=>[button,button.disabled]);
      controls.forEach(([button])=>{button.disabled=true;});
      try { await operation(); }
      catch (error) { toast(error.message || '设置操作未完成，请重试。', true); }
      finally { busy=false; controls.forEach(([button,disabled])=>{if(button.isConnected)button.disabled=disabled;}); }
    }

    async function handleAction(button) {
      const action = button?.dataset?.action;
      if (!['settings-test-main','settings-test-coach','settings-clear-key','settings-clear-anthropic','settings-clear-headers'].includes(action)) return false;
      await run(async()=>{
        if (action === 'settings-test-main' || action === 'settings-test-coach') {
          await save({silent:true});
          const status = query('#settings-connection-status');
          if (status) {status.className='settings-check';status.textContent='正在发送短测试消息，请稍候…';}
          const result = await rpc('checkWebAiConnection',{coach:action === 'settings-test-coach'});
          if (status?.isConnected) {status.className=`settings-check ${result.ok ? 'is-success' : result.state === 'unverified' ? 'is-unverified' : 'is-error'}`;status.textContent=result.message;}
          toast(result.message, !result.ok && result.state !== 'unverified');
          return;
        }
        const flags = {'settings-clear-key':'clearKey','settings-clear-anthropic':'clearAnthropicKey','settings-clear-headers':'clearExtraHeaders'};
        if (flags[action]) {
          settings = await rpc('configureWebSettings',{[flags[action]]:true});
          const input = query(action === 'settings-clear-key' ? '#ai-key' : action === 'settings-clear-anthropic' ? '#anthropic-key' : '#ai-extra-headers');
          if (input) {input.value='';input.placeholder='已清除；可重新填写';}
          if (query('#settings-connection-status')) query('#settings-connection-status').textContent = '';
          storageStatus();toast('已清除对应配置；本机环境变量需在启动服务的环境中另行调整。');
        }
      });
      return true;
    }

    async function handleSubmit(event) {
      if (event.target?.id !== 'settings-form') return false;
      event.preventDefault();
      await run(()=>save());
      return true;
    }

    return {render,save,handleAction,handleSubmit};
  };
})();
