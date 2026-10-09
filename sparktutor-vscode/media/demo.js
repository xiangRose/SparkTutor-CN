/** No task IDs, scores or scenario payloads are accepted from a run button. */
const vscode = acquireVsCodeApi();
const version = Number(document.body.dataset.version);
const mode = document.getElementById('demo-mode');
const scenario = document.getElementById('demo-scenario');
const status = document.getElementById('demo-status');
let sending = false;

function send(message) {
  if (sending) return;
  sending = true;
  document.querySelectorAll('button, select').forEach((element) => { element.disabled = true; });
  if (status) status.textContent = message.type === 'run' ? '演示请求已发送，正在等待阶段报告……' : '正在更新演示视图……';
  vscode.postMessage({ ...message, version });
}
const choose = () => {
  if (mode.disabled || scenario.disabled) return;
  send({ type: 'selection', mode: mode.value, scenarioId: scenario.value });
};
mode.addEventListener('change', choose);
scenario.addEventListener('change', choose);
document.querySelectorAll('button[data-action]').forEach((button) => {
  button.addEventListener('click', () => {
    if (button.disabled) return;
    send({ type: button.dataset.action,
      ...(button.dataset.stageIndex === undefined ? {} : { stageIndex: Number(button.dataset.stageIndex) }) });
  });
});
const focusTarget = document.getElementById(document.body.dataset.focus || '');
if (focusTarget) { focusTarget.scrollIntoView({ block: 'start' }); focusTarget.focus({ preventScroll: true }); }
