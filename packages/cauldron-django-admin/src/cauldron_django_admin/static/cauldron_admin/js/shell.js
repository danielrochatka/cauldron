(function () {
  'use strict';

  var toggle = document.querySelector('.cui-nav-toggle');
  var sidebar = document.querySelector('#cui-sidebar');
  var backdrop = document.querySelector('#cui-sidebar-backdrop');

  if (toggle && sidebar) {
    function openDrawer() {
      sidebar.classList.add('is-open');
      if (backdrop) backdrop.classList.add('is-open');
      toggle.setAttribute('aria-expanded', 'true');
      var focusable = sidebar.querySelectorAll(
        'a[href], button:not([disabled]), [tabindex]:not([tabindex="-1"])'
      );
      if (focusable.length > 0) focusable[0].focus();
    }

    function closeDrawer() {
      sidebar.classList.remove('is-open');
      if (backdrop) backdrop.classList.remove('is-open');
      toggle.setAttribute('aria-expanded', 'false');
      toggle.focus();
    }

    toggle.addEventListener('click', function () {
      if (sidebar.classList.contains('is-open')) {
        closeDrawer();
      } else {
        openDrawer();
      }
    });

    if (backdrop) {
      backdrop.addEventListener('click', closeDrawer);
    }

    document.addEventListener('keydown', function (e) {
      if (e.key === 'Escape' && sidebar.classList.contains('is-open')) {
        closeDrawer();
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
    document.addEventListener('keydown', function (e) {
      if (e.key === 'Escape' && userMenuDropdown.hasAttribute('data-open')) {
        userMenuDropdown.removeAttribute('data-open');
        userMenuTrigger.setAttribute('aria-expanded', 'false');
        userMenuTrigger.focus();
      }
    });
  }
}());
