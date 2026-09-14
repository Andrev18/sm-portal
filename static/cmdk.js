/* Ctrl+K command palette + skroty klawiszowe z podpowiedniami */
(function () {
  const overlay = document.getElementById('cmdOverlay');
  const input = document.getElementById('cmdInput');
  const list = document.getElementById('cmdList');
  const btn = document.getElementById('cmdBtn');
  if (!overlay) return;

  const CMDS = [
    {k: 'Start', ico: '🏠', url: '/', key: 'S'},
    {k: 'Fiszki — ucz się', ico: '🃏', url: '/srs', key: 'F'},
    {k: 'Dodaj (notatka/zdjęcie/PDF)', ico: '➕', url: '/add', key: 'D'},
    {k: 'Biblioteka', ico: '📚', url: '/library', key: 'B'},
    {k: 'Przedmioty', ico: '🧩', url: '/subjects', key: 'P'},
    {k: 'Ogłoszenia', ico: '📢', url: '/announcements', key: 'O'},
    {k: 'Forum', ico: '💬', url: '/forum'},
    {k: 'Chat', ico: '🗨️', url: '/chat', key: 'C'},
    {k: 'Pomysły', ico: '💡', url: '/ideas', key: 'I'},
    {k: 'Ranking / Liga', ico: '🏆', url: '/leaderboard', key: 'R'},
    {k: 'Ekwipunek (avatary)', ico: '🎒', url: '/locker', key: 'E'},
    {k: 'Moje statystyki', ico: '📈', url: '/stats'},
  ];
  if (document.body.dataset.role === 'admin' || document.body.dataset.role === 'teacher') {
    CMDS.push({k: 'Panel nauczyciela', ico: '📊', url: '/teacher', key: 'T'});
  }

  let sel = 0, filtered = CMDS;

  function render() {
    list.innerHTML = filtered.map((c, i) =>
      `<li class="cmd-item ${i === sel ? 'sel' : ''}" data-url="${c.url}">
        <span>${c.ico}</span><b>${c.k}</b>
        ${c.key ? `<kbd>${c.key}</kbd>` : ''}
      </li>`).join('');
    [...list.querySelectorAll('.cmd-item')].forEach(li => li.addEventListener('click', () => location.href = li.dataset.url));
  }

  function open() { overlay.hidden = false; input.value = ''; filtered = CMDS; sel = 0; render(); input.focus(); }
  function close() { overlay.hidden = true; }

  btn.addEventListener('click', open);
  overlay.addEventListener('mousedown', e => { if (e.target === overlay) close(); });
  input.addEventListener('input', () => {
    const q = input.value.toLowerCase();
    filtered = CMDS.filter(c => c.k.toLowerCase().includes(q));
    sel = 0; render();
  });
  input.addEventListener('keydown', e => {
    if (e.key === 'ArrowDown') { sel = Math.min(sel + 1, filtered.length - 1); render(); e.preventDefault(); }
    if (e.key === 'ArrowUp') { sel = Math.max(sel - 1, 0); render(); e.preventDefault(); }
    if (e.key === 'Enter' && filtered[sel]) location.href = filtered[sel].url;
    if (e.key === 'Escape') close();
  });

  const KEYMAP = {};
  CMDS.forEach(c => { if (c.key) KEYMAP[c.key.toLowerCase()] = c.url; });

  document.addEventListener('keydown', e => {
    if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === 'k') { e.preventDefault(); overlay.hidden ? open() : close(); return; }
    if (e.key === 'Escape' && !overlay.hidden) { close(); return; }
    if (overlay.hidden && !e.ctrlKey && !e.metaKey && !e.altKey) {
      const tag = (e.target.tagName || '').toLowerCase();
      if (tag === 'input' || tag === 'textarea' || tag === 'select') return;
      const url = KEYMAP[e.key.toLowerCase()];
      if (url) location.href = url;
    }
  });
})();