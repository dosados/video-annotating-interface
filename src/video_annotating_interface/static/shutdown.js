'use strict';
(() => {
  const button = document.getElementById('shutdown');
  button.onclick = async () => {
    if (!confirm('Save the current frame and stop the application? / Сохранить текущий кадр и завершить приложение?')) return;
    button.disabled = true;
    const controls = [...document.querySelectorAll('button, input, select, a')];
    const disabled = controls.map(control => control.disabled);
    const block = event => { event.preventDefault(); event.stopImmediatePropagation(); };
    try {
      // Block further edits while the final frame save is in flight.
      for (const type of ['click', 'keydown', 'pointerdown']) document.addEventListener(type, block, true);
      if (window.prepareShutdown && !await window.prepareShutdown()) return;
      controls.forEach(control => { control.disabled = true; });
      const response = await fetch('/api/shutdown', {
        method: 'POST', headers: {'Content-Type': 'application/json'}, body: '{}'
      });
      const result = await response.json();
      if (!response.ok) throw Error(result.error || 'Could not stop the application');
      const message = document.createElement('p');
      message.setAttribute('role', 'status');
      message.textContent = 'Application stopped. You can close this tab. / Приложение завершено. Можно закрыть вкладку.';
      document.body.replaceChildren(message);
    } catch (error) {
      alert(error.message);
    } finally {
      for (const type of ['click', 'keydown', 'pointerdown']) document.removeEventListener(type, block, true);
      controls.forEach((control, index) => { control.disabled = disabled[index]; });
      button.disabled = false;
    }
  };
})();
