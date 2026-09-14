/* ============ MODERN THEME SWITCHER POPOVER & STORAGE ============ */
(function() {
  var savedTheme = localStorage.getItem('portal_theme') || 'notion-light';
  document.documentElement.setAttribute('data-theme', savedTheme);

  document.addEventListener('DOMContentLoaded', function() {
    var themeBtn = document.getElementById('themeBtn');
    var themePopover = document.getElementById('themePopover');

    if (themeBtn && themePopover) {
      themeBtn.addEventListener('click', function(e) {
        e.stopPropagation();
        var isHidden = themePopover.hasAttribute('hidden');
        if (isHidden) {
          themePopover.removeAttribute('hidden');
          updateThemeActiveState();
        } else {
          themePopover.setAttribute('hidden', '');
        }
      });

      document.addEventListener('click', function(e) {
        if (themePopover && !themePopover.contains(e.target) && e.target !== themeBtn) {
          themePopover.setAttribute('hidden', '');
        }
      });
    }

    updateThemeActiveState();
  });

  window.setPortalTheme = function(themeName) {
    document.documentElement.setAttribute('data-theme', themeName);
    localStorage.setItem('portal_theme', themeName);
    updateThemeActiveState();

    var themePopover = document.getElementById('themePopover');
    if (themePopover) themePopover.setAttribute('hidden', '');
  };

  function updateThemeActiveState() {
    var current = localStorage.getItem('portal_theme') || 'notion-light';
    var items = document.querySelectorAll('.theme-popover-item');
    items.forEach(function(item) {
      var t = item.getAttribute('data-theme-val');
      if (t === current) {
        item.classList.add('active');
      } else {
        item.classList.remove('active');
      }
    });
  }
})();
