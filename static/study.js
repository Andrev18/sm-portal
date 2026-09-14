/* Misja dnia full-screen: sesja JSON (bez przeladowan), flip 3D, swipe, SFX, lektor, konfetti, XP */
(function () {
  const params = new URLSearchParams(location.search);
  const deckId = params.get('deck') || window.location.pathname.split('/').pop();
  const scene = document.getElementById('scene');
  if (!scene) return;

  let queue = [], idx = 0, combo = 0, flipped = false, busy = false, gainedTotal = 0;

  /* --- SFX (WebAudio, zero plikow) */
  let actx = null;
  function sfx(kind) {
    try {
      actx = actx || new (window.AudioContext || window.webkitAudioContext)();
      const o = actx.createOscillator(), g = actx.createGain();
      o.connect(g); g.connect(actx.destination);
      if (kind === 'good') { o.frequency.value = 660; g.gain.setValueAtTime(.12, actx.currentTime); g.gain.exponentialRampToValueAtTime(.001, actx.currentTime + .18); o.start(); o.stop(actx.currentTime + .18); }
      else if (kind === 'bad') { o.frequency.value = 140; g.gain.setValueAtTime(.14, actx.currentTime); g.gain.exponentialRampToValueAtTime(.001, actx.currentTime + .25); o.type = 'sine'; o.start(); o.stop(actx.currentTime + .25); }
      else { o.frequency.value = 440; g.gain.setValueAtTime(.06, actx.currentTime); g.gain.exponentialRampToValueAtTime(.001, actx.currentTime + .08); o.start(); o.stop(actx.currentTime + .08); }
    } catch (e) {}
  }
  function buzz(ms) { if (navigator.vibrate) navigator.vibrate(ms); }

  function confetti() {
    for (let i = 0; i < 26; i++) {
      const s = document.createElement('span');
      s.textContent = ['⭐', '✨', '🎉', '⚡', '💎'][i % 5];
      s.style.cssText = 'position:fixed;z-index:200;font-size:' + (13 + Math.random() * 16) + 'px;pointer-events:none;' +
        'left:' + (30 + Math.random() * 40) + 'vw;top:38vh;transition:transform 1s ease-out,opacity 1s;';
      document.body.appendChild(s);
      requestAnimationFrame(() => {
        s.style.transform = `translate(${(Math.random() - .5) * 320}px,${180 + Math.random() * 220}px) rotate(${Math.random() * 360}deg)`;
        s.style.opacity = '0';
      });
      setTimeout(() => s.remove(), 1100);
    }
  }

  function show(c) {
    document.getElementById('front').textContent = c.front;
    const back = document.getElementById('back');
    back.innerHTML = (c.back.startsWith('<img') || c.back.includes('<img')) ? c.back : c.back.replace(/</g, '&lt;');
    card.classList.remove('flipped');
    card.style.transition = 'none'; card.style.transform = ''; card.style.opacity = '1';
    setTimeout(() => card.style.transition = '', 50);
    flipped = false; busy = false;
    rates.style.visibility = 'hidden';
    hint.textContent = '👇 Przesuń kartę albo kliknij, żeby odwrócić';
    posEl.textContent = idx + 1;
    document.getElementById('bar').style.width = Math.min(100, idx / Math.max(total, 1) * 100) + '%';
    speakBtn.dataset.text = c.front;
  }

  function speak(text) {
    try {
      const u = new SpeechSynthesisUtterance(text);
      u.lang = 'pl-PL'; u.rate = 0.95;
      speechSynthesis.cancel(); speechSynthesis.speak(u);
    } catch (e) {}
  }
  const speakBtn = document.getElementById('speak1');
  speakBtn.addEventListener('click', e => { e.stopPropagation(); speak(document.getElementById('front').textContent.trim()); });

  const card = document.getElementById('card');
  const rates = document.getElementById('rates');
  const hint = document.getElementById('hint');
  const posEl = document.getElementById('pos');
  let total = 7;

  card.addEventListener('click', e => {
    if (e.target.closest('.speak-btn') || busy) return;
    flipped = !flipped;
    card.classList.toggle('flipped', flipped);
    sfx('tap');
    if (flipped) { rates.style.visibility = 'visible'; hint.textContent = '👇 Oceń (strzałki ← → też działają)'; }
    else { rates.style.visibility = 'hidden'; hint.textContent = '👇 Przesuń kartę albo kliknij, żeby odwrócić'; }
  });

  function finish() {
    document.getElementById('bar').style.width = '100%';
    scene.innerHTML = `<div class="flip-face flip-front" style="position:static;border-radius:24px;padding:40px 24px;display:block">
      <div style="font-size:3.4rem">🎉</div>
      <h2 style="font-family:var(--font-head)">Sesja ukończona!</h2>
      <p style="font-weight:800;color:#fbbf24">+${gainedTotal} XP</p>
      <a class="btn primary big" href="/srs">Wróć do tali</a>
      <a class="btn ghost big" href="/" style="margin-top:8px">🏠 Baza</a></div>`;
    confetti(); sfx('good');
  }

  function rate(r) {
    if (busy) return;
    busy = true;
    const c = queue[idx];
    if (r >= 2) { combo++; sfx('good'); buzz(30); confettiMini(); } else { combo = 0; sfx('bad'); buzz(60); }
    card.style.transition = 'transform .35s ease-in, opacity .35s';
    card.style.transform = (flipped ? 'rotateY(180deg) ' : '') + `translateX(${r >= 2 ? 130 : -130}px) rotate(${r >= 2 ? 14 : -14}deg)`;
    card.style.opacity = '0';
    const fd = new FormData(); fd.append('rating', r);
    fetch('/api/review/' + c.id, {method: 'POST', body: fd})
      .then(res => res.json())
      .then(d => {
        gainedTotal += d.gained || 0;
        document.getElementById('combo').textContent = combo;
        document.getElementById('gained').textContent = gainedTotal;
        idx++;
        if (d.next && idx < total) { setTimeout(() => show(d.next), 250); }
        else setTimeout(finish, 250);
      }).catch(() => { busy = false; });
  }
  function confettiMini() {
    if (combo < 3) return;
    for (let i = 0; i < 10; i++) {
      const s = document.createElement('span');
      s.textContent = '✨';
      s.style.cssText = 'position:fixed;z-index:200;font-size:14px;pointer-events:none;left:' + (40 + Math.random() * 20) + 'vw;top:45vh;transition:transform .8s,opacity .8s;';
      document.body.appendChild(s);
      requestAnimationFrame(() => { s.style.transform = `translate(${(Math.random() - .5) * 200}px,150px)`; s.style.opacity = '0'; });
      setTimeout(() => s.remove(), 900);
    }
  }

  rates.querySelectorAll('[data-r]').forEach(b => b.addEventListener('click', () => rate(+b.dataset.r)));

  /* swipe */
  let sx = 0, dx = 0;
  scene.addEventListener('touchstart', e => { sx = e.touches[0].clientX; dx = 0; }, {passive: true});
  scene.addEventListener('touchmove', e => {
    dx = e.touches[0].clientX - sx;
    if (Math.abs(dx) > 20) {
      const base = flipped ? 'rotateY(180deg) ' : '';
      card.style.transform = base + `translateX(${dx}px) rotate(${dx / 20}deg)`;
    }
  }, {passive: true});
  scene.addEventListener('touchend', () => {
    if (Math.abs(dx) > 90) rate(dx > 0 ? 3 : 0);
    else if (dx !== 0) { card.style.transform = flipped ? 'rotateY(180deg)' : ''; card.style.opacity = '1'; }
  });

  /* klawiatura: spacja flip, strzalki oceny */
  document.addEventListener('keydown', e => {
    if (e.key === ' ') { e.preventDefault(); card.click(); }
    if (e.key === 'ArrowRight') rate(3);
    if (e.key === 'ArrowLeft') rate(0);
    if (['1', '2', '3', '4'].includes(e.key)) rate(+e.key - 1);
  });

  /* start: pobierz sesje */
  const cardEl = document.getElementById('card');
  fetch('/api/session/' + deckId).then(r => r.json()).then(d => {
    queue = d.cards;
    total = Math.min(7, queue.length);
    document.getElementById('total').textContent = total;
    if (!queue.length) {
      scene.innerHTML = '<div class="flip-face flip-front" style="position:static;display:block;padding:40px"><h2>🎉 Wszystko powtórzone!</h2><a class="btn primary big" href="/srs" style="margin-top:14px">← Talie</a></div>';
      return;
    }
    show(queue[0]);
  });
})();