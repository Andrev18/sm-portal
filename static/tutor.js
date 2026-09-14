/* Lisek - AI korepetytor (DeepSeek), FAB + panel chat */
(function () {
  const btn = document.getElementById('tutorBtn');
  const panel = document.getElementById('tutorPanel');
  const log = document.getElementById('tutorLog');
  const form = document.getElementById('tutorForm');
  const q = document.getElementById('tutorQ');
  if (!btn) return;

  function open() { panel.hidden = !panel.hidden; if (!panel.hidden) q.focus(); }
  btn.addEventListener('click', open);
  document.querySelector('[data-tclose]')?.addEventListener('click', () => panel.hidden = true);

  form.addEventListener('submit', async e => {
    e.preventDefault();
    const question = q.value.trim();
    if (!question) return;
    q.value = '';
    const ask = document.createElement('div');
    ask.className = 'msg'; ask.style.background = 'var(--mint)';
    ask.textContent = question;
    log.appendChild(ask);
    const ans = document.createElement('div');
    ans.className = 'msg typing'; ans.textContent = 'Lisek myśli';
    log.appendChild(ans);
    log.scrollTop = log.scrollHeight;
    try {
      const r = await fetch('/ai/tutor?q=' + encodeURIComponent(question));
      const d = await r.json();
      ans.textContent = d.answer || '…';
    } catch (e) {
      ans.textContent = 'Brak połączenia — spróbuj jeszcze raz.';
    }
    ans.classList.remove('typing');
    log.scrollTop = log.scrollHeight;
  });
})();