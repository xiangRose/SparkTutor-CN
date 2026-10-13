/** Report versions prevent a button from acting on a superseded course or diagnosis. */
const vscode = acquireVsCodeApi();
const version = Number(document.body.dataset.version);
const controls = [...document.querySelectorAll('button[data-action], select')];
const status = document.getElementById('dashboard-status');
let busy = false;

function setBusy(value) {
  busy = value;
  controls.forEach((element) => { element.disabled = value || element.dataset.permanentDisabled === 'true'; });
}

document.querySelectorAll('button[data-action]').forEach((button) => {
  button.addEventListener('click', () => {
    if (busy) return;
    setBusy(true);
    if (status) {
      status.classList.remove('action-error');
      status.textContent = button.dataset.action === 'refresh' ? '正在刷新……' : '正在打开……';
    }
    vscode.postMessage({ type: button.dataset.action, version,
      courseId: button.dataset.course, lessonId: button.dataset.lesson });
  });
});

document.getElementById('course-filter')?.addEventListener('change', (event) => {
  if (busy) return;
  setBusy(true);
  vscode.postMessage({ type: 'filter', version, courseId: event.target.value });
});

window.addEventListener('message', (event) => {
  const message = event.data;
  if (message.type !== 'actionStatus' || message.version !== version) return;
  setBusy(Boolean(message.busy));
  if (status) {
    status.textContent = message.message;
    status.classList.toggle('action-error', Boolean(message.error));
  }
});
