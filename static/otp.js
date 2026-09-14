/* OTP: 5 kwadracikow - ergonomiczne jak kod z banku */
(function () {
  const boxes = [...document.querySelectorAll('.otp')];
  const hidden = document.getElementById('pinField');
  const go = document.getElementById('goBtn');
  const mascot = document.getElementById('mascot');
  if (!boxes.length) return;

  function value() { return boxes.map(b => b.value).join(''); }
  function sync() {
    hidden.value = value();
    go.disabled = value().length !== 5;
    boxes.forEach((b, i) => {
      b.classList.toggle('filled', !!b.value);
      b.classList.toggle('active', document.activeElement === b);
    });
    const n = value().length;
    mascot.textContent = n === 0 ? '🦊' : n < 5 ? '🤔' : '😍';
  }
  function focusNext(i) {
    const next = boxes[Math.min(i + 1, 4)];
    if (next) next.focus();
  }
  boxes.forEach((b, i) => {
    b.addEventListener('input', () => {
      b.value = b.value.replace(/\D/g, '').slice(0, 1);
      if (b.value) focusNext(i);
      sync();
      if (value().length === 5) {
        go.classList.add('pulse');
        setTimeout(() => document.getElementById('pinForm').submit(), 400);
      }
    });
    b.addEventListener('keydown', e => {
      if (e.key === 'Backspace' && !b.value && boxes[i - 1]) { boxes[i - 1].focus(); boxes[i - 1].value = ''; sync(); e.preventDefault(); }
    });
    b.addEventListener('focus', sync);
    b.addEventListener('paste', e => {
      const digits = (e.clipboardData.getData('text') || '').replace(/\D/g, '').slice(0, 5);
      if (digits) {
        e.preventDefault();
        digits.split('').forEach((d, k) => { if (boxes[k]) boxes[k].value = d; });
        boxes[Math.min(digits.length, 4)].focus();
        sync();
        if (digits.length === 5) setTimeout(() => document.getElementById('pinForm').submit(), 400);
      }
    });
  });
  document.getElementById('pinForm').addEventListener('submit', () => {
    go.disabled = false;
    go.textContent = 'Sprawdzam… ✨';
  });
  boxes[0].focus();
  sync();
})();