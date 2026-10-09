/** Buttons identify only entries in the current report; the extension owns task and event IDs. */
const vscode = acquireVsCodeApi();
const version = Number(document.body.dataset.version);
const status = document.getElementById('history-action-status');
const course = document.getElementById('history-course');
const windowFilter = document.getElementById('history-window');
let sending = false;

function send(message) {
  if (sending) return;
  sending = true;
  document.querySelectorAll('button, select').forEach((element) => { element.disabled = true; });
  if (status) status.textContent = '正在读取……';
  vscode.postMessage({ ...message, version });
}

document.querySelectorAll('button[data-action]').forEach((button) => {
  button.addEventListener('click', () => {
    if (button.disabled) return;
    send({ type: button.dataset.action,
      taskIndex: button.dataset.taskIndex === undefined ? undefined : Number(button.dataset.taskIndex),
      eventIndex: button.dataset.eventIndex === undefined ? undefined : Number(button.dataset.eventIndex),
      pointIndex: button.dataset.pointIndex === undefined ? undefined : Number(button.dataset.pointIndex) });
  });
});
const filter = () => send({ type: 'filter', courseId: course.value, window: windowFilter.value });
course.addEventListener('change', filter);
windowFilter.addEventListener('change', filter);

const focusTarget = document.getElementById(document.body.dataset.focus || '');
if (focusTarget) {
  focusTarget.scrollIntoView({ block: 'start' });
  focusTarget.focus({ preventScroll: true });
}
