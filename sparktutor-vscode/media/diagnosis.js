/** Messages carry actions only; the extension owns the validated recommendation. */
const vscode = acquireVsCodeApi();
const refresh = document.getElementById('refresh');
const openExercise = document.getElementById('open-exercise');
const status = document.getElementById('exercise-status');

refresh.addEventListener('click', () => {
  refresh.disabled = true;
  if (openExercise) openExercise.disabled = true;
  vscode.postMessage({ type: 'refresh' });
});

openExercise?.addEventListener('click', () => {
  openExercise.disabled = true;
  refresh.disabled = true;
  if (status) {
    status.classList.remove('action-error');
    status.textContent = '正在打开推荐练习……';
  }
  vscode.postMessage({ type: 'openExercise' });
});

window.addEventListener('message', (event) => {
  if (event.data.type !== 'exerciseStatus') return;
  if (openExercise) openExercise.disabled = false;
  refresh.disabled = false;
  if (status) {
    status.textContent = event.data.message;
    status.classList.toggle('action-error', Boolean(event.data.error));
  }
});
