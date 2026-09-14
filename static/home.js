/* Strona glowna: misja dnia, liga tygodniowa, rajd klasowy (API JSON) */
(function () {
  const medal = ['🥇', '🥈', '🥉'];
  fetch('/api/mission').then(r => r.json()).then(d => {
    // hero misja
    const t = document.getElementById('missionTitle');
    const bar = document.getElementById('missionBar');
    const cnt = document.getElementById('missionCount');
    const btn = document.getElementById('missionBtn');
    if (d.mission) {
      const total = Math.max(d.mission.due, 1);
      t.textContent = 'Powtórz fiszki: ' + d.mission.deck_name;
      bar.style.width = Math.round((d.mission.total - d.mission.due) / Math.max(d.mission.total, 1) * 100) + '%';
      cnt.textContent = d.mission.due + ' kart czeka (z ' + d.mission.total + ' w talii)';
      btn.href = '/srs/study/' + d.mission.deck_id;
    } else {
      t.textContent = 'Brak fiszek na dziś — dodaj nowy materiał!';
      btn.textContent = '➕ Dodaj fiszki';
      btn.href = '/add';
      bar.style.width = '100%';
      cnt.textContent = '';
    }
    // liga
    const lb = document.getElementById('leagueBox');
    lb.innerHTML = d.league.map((row, i) =>
      `<div class="league-row ${row.id === d.me ? 'me' : ''}">
        <span class="medal">${medal[i] || (i + 1) + '.'}</span>
        <span style="font-size:1.3rem">${row.avatar}</span>
        <b>${row.name}</b><span class="xp">${row.week} XP</span></div>`).join('')
      || '<small class="muted">Jeszcze nikt nie zdobył punktów w tym tygodniu</small>';
    lb.insertAdjacentHTML('beforeend',
      `<div class="raid small" style="margin-top:6px">Twój tytuł: <b style="color:#fcd34d">${d.title}</b></div>`);
    // rajd
    const pct = Math.min(100, Math.round(d.raid.done / d.raid.goal * 100));
    document.getElementById('raidBar').style.width = pct + '%';
    document.getElementById('raidText').textContent =
      `Klasa uzbierała ${d.raid.done}/${d.raid.goal} powtórek w tym tygodniu (${pct}%) — wspólny cel: quiz z nagrodami!`;
  }).catch(() => {});
})();