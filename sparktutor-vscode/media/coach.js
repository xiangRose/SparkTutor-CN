/** Only visible, rendered content may request display acknowledgement. */
const vscode = acquireVsCodeApi();
const version = Number(document.body.dataset.version);
let sentDisplay = false;
let busy = false;
function notifyDisplayed() {
  const message = document.getElementById('coach-message');
  if (!message || message.dataset.awaitingAck !== 'true' || sentDisplay || document.visibilityState === 'hidden') return;
  requestAnimationFrame(() => {
    if (sentDisplay || document.visibilityState === 'hidden' || !message.isConnected) return;
    requestAnimationFrame(() => {
      if (sentDisplay || document.visibilityState === 'hidden' || !message.isConnected) return;
      sentDisplay = true;
      vscode.postMessage({ type: 'displayed', version, messageId: message.dataset.messageId });
    });
  });
}
document.addEventListener('visibilitychange', notifyDisplayed);
notifyDisplayed();
document.querySelectorAll('button[data-action]').forEach((button) => {
  button.addEventListener('click', () => {
    if (busy || button.disabled) return;
    busy = true;
    document.querySelectorAll('button[data-action]').forEach((control) => { control.disabled = true; });
    vscode.postMessage({ type: button.dataset.action, version,
      level: Number(button.dataset.level), index: Number(button.dataset.index) });
  });
});
