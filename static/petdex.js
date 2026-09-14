/* ==========================================================================
   PETDEX ANIMATED MASCOT WIDGET (Hermes Agent Pets - Screenshot Replica)
   ========================================================================== */

(function() {
  const PET_MAP = {
    'boxcat': { name: 'Boxcat', img: '/static/pets/boxcat.png', sound: 'Miau! 📦' },
    'cache-capy': { name: 'Cache Capy', img: '/static/pets/cache-capy.png', sound: 'Chomp chomp! 🎒' },
    'nukey': { name: 'Nukey', img: '/static/pets/nukey.png', sound: 'Bip bip! 🍿' },
    'socksy': { name: 'Socksy', img: '/static/pets/socksy.png', sound: 'Hejka! 🧦' },
    'daemon-dumpling': { name: 'Daemon Dumpling', img: '/static/pets/daemon-dumpling.png', sound: 'Squeak! 🧢' },
    'cosmo': { name: 'Crafternauta Cosmo', img: '/static/pets/cosmo.png', sound: '3... 2... 1... W kosmos! 🚀' },
    'byte-bunny': { name: 'Byte Bunny', img: '/static/pets/byte-bunny.png', sound: 'Kic kic! 🐰' },
    'cash-cuy': { name: 'Cash Cuy', img: '/static/pets/cash-cuy.png', sound: 'Squeak squeak! 👔' },
    'prompt-penguin': { name: 'Prompt Penguin', img: '/static/pets/prompt-penguin.png', sound: 'Noot noot! 🐧' },
    'scoop': { name: 'Scoop', img: '/static/pets/scoop.png', sound: 'Mniam! 🍦' },
    'kebo': { name: 'Kebo', img: '/static/pets/kebo.png', sound: 'Zzz... 🐨' },
    'skipper': { name: 'TuxTerm', img: '/static/pets/skipper.png', sound: 'System v26.0 gotowy! 🐧' }
  };

  const PHRASES = [
    "Dobra robota dzisiaj! 🌟",
    "Zróbmy 5 fiszek z angielskiego! 🇬🇧",
    "Pamiętasz o spotkaniu na Google Meet? 📹",
    "Sprawdź e-dziennik VULCAN! 🏫",
    "Odrabiamy matematykę? 📐",
    "Super seria nauki! 🔥",
    "Przeczytajmy rozdział Baśnioboru! 📖",
    "Masz ochotę na quiz w ClassQuiz? 🎯"
  ];

  let currentSlug = localStorage.getItem('portal_pet_slug') || 'boxcat';

  document.addEventListener('DOMContentLoaded', () => {
    const bodyAvatar = document.body.getAttribute('data-avatar');
    if (bodyAvatar && (PET_MAP[bodyAvatar] || bodyAvatar.includes('/static/pets/'))) {
      currentSlug = bodyAvatar.replace('/static/pets/', '').replace('.png', '');
      localStorage.setItem('portal_pet_slug', currentSlug);
    }

    createPetWidget();
  });

  function createPetWidget() {
    if (document.getElementById('petdex-widget')) return;

    const petInfo = PET_MAP[currentSlug] || PET_MAP['boxcat'];

    const widget = document.createElement('div');
    widget.id = 'petdex-widget';
    widget.className = 'petdex-container';
    widget.innerHTML = `
      <div class="petdex-speech" id="petdexSpeech" style="display:none;">
        <span id="petdexText">${petInfo.sound}</span>
        <button class="petdex-close" onclick="document.getElementById('petdexSpeech').style.display='none'">✕</button>
      </div>
      <div class="petdex-avatar-wrap" id="petdexAvatarWrap" title="Kliknij pupilka!">
        <img class="petdex-sprite" id="petdexSpriteImg" src="${petInfo.img}" alt="${petInfo.name}">
        <div class="petdex-shadow"></div>
      </div>
    `;

    document.body.appendChild(widget);

    const avatarWrap = document.getElementById('petdexAvatarWrap');
    avatarWrap.addEventListener('click', onPetClick);

    setInterval(randomPetBehavior, 7000);
  }

  function onPetClick() {
    const petInfo = PET_MAP[currentSlug] || PET_MAP['boxcat'];
    const speech = document.getElementById('petdexSpeech');
    const textEl = document.getElementById('petdexText');
    const sprite = document.getElementById('petdexSpriteImg');

    sprite.classList.add('petdex-bounce');
    setTimeout(() => sprite.classList.remove('petdex-bounce'), 800);

    createHearts();

    const randomPhrase = PHRASES[Math.floor(Math.random() * PHRASES.length)];
    textEl.innerText = `${petInfo.sound} ${randomPhrase}`;
    speech.style.display = 'flex';

    setTimeout(() => {
      speech.style.display = 'none';
    }, 6000);
  }

  function createHearts() {
    const widget = document.getElementById('petdex-widget');
    if (!widget) return;

    for (let i = 0; i < 3; i++) {
      const heart = document.createElement('span');
      heart.className = 'petdex-heart';
      heart.innerText = ['❤️', '✨', '🐾', '🌟'][Math.floor(Math.random() * 4)];
      heart.style.left = (15 + (i * 16)) + 'px';
      widget.appendChild(heart);

      setTimeout(() => heart.remove(), 1200);
    }
  }

  function randomPetBehavior() {
    const sprite = document.getElementById('petdexSpriteImg');
    if (!sprite) return;

    const rand = Math.random();
    if (rand < 0.35) {
      sprite.classList.add('petdex-walk');
      setTimeout(() => sprite.classList.remove('petdex-walk'), 2000);
    } else if (rand < 0.6) {
      sprite.classList.add('petdex-jump');
      setTimeout(() => sprite.classList.remove('petdex-jump'), 600);
    }
  }

  window.selectPetdexPet = function(slug) {
    const cleanSlug = slug.replace('/static/pets/', '').replace('.png', '');
    currentSlug = cleanSlug;
    localStorage.setItem('portal_pet_slug', cleanSlug);

    const petInfo = PET_MAP[cleanSlug] || PET_MAP['boxcat'];
    const sprite = document.getElementById('petdexSpriteImg');
    if (sprite) {
      sprite.src = petInfo.img;
      onPetClick();
    }
  };

})();
