(function () {
  'use strict';

  // Mobile nav toggle
  var toggle = document.querySelector('.cui-nav-toggle');
  var sidebar = document.querySelector('#cui-sidebar');
  if (toggle && sidebar) {
    toggle.addEventListener('click', function () {
      var isOpen = sidebar.classList.toggle('is-open');
      toggle.setAttribute('aria-expanded', isOpen ? 'true' : 'false');
    });
    // Close on outside click
    document.addEventListener('click', function (e) {
      if (!sidebar.contains(e.target) && !toggle.contains(e.target)) {
        sidebar.classList.remove('is-open');
        toggle.setAttribute('aria-expanded', 'false');
      }
    });
    // Close on Escape
    document.addEventListener('keydown', function (e) {
      if (e.key === 'Escape') {
        sidebar.classList.remove('is-open');
        toggle.setAttribute('aria-expanded', 'false');
      }
    });
  }

  // User menu dropdown
  var userMenuTrigger = document.querySelector('.cui-user-menu__trigger');
  var userMenuDropdown = document.querySelector('.cui-user-menu__dropdown');
  if (userMenuTrigger && userMenuDropdown) {
    userMenuTrigger.addEventListener('click', function () {
      var isOpen = userMenuDropdown.hasAttribute('data-open');
      if (isOpen) {
        userMenuDropdown.removeAttribute('data-open');
        userMenuTrigger.setAttribute('aria-expanded', 'false');
      } else {
        userMenuDropdown.setAttribute('data-open', '');
        userMenuTrigger.setAttribute('aria-expanded', 'true');
      }
    });
    document.addEventListener('click', function (e) {
      if (!userMenuTrigger.contains(e.target)) {
        userMenuDropdown.removeAttribute('data-open');
        userMenuTrigger.setAttribute('aria-expanded', 'false');
      }
    });
  }
}());
