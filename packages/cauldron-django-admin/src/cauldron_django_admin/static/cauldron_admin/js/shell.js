(function () {
  'use strict';

  // Mobile nav toggle
  var toggle = document.querySelector('.cui-nav-toggle');
  var sidebar = document.querySelector('#cui-sidebar');
  if (toggle && sidebar) {
    function openDrawer() {
      sidebar.classList.add('is-open');
      toggle.setAttribute('aria-expanded', 'true');
      // Move focus to first focusable element inside drawer
      var focusable = sidebar.querySelectorAll(
        'a[href], button:not([disabled]), [tabindex]:not([tabindex="-1"])'
      );
      if (focusable.length > 0) {
        focusable[0].focus();
      }
    }

    function closeDrawer() {
      sidebar.classList.remove('is-open');
      toggle.setAttribute('aria-expanded', 'false');
      // Restore focus to the toggle button
      toggle.focus();
    }

    toggle.addEventListener('click', function () {
      var isOpen = sidebar.classList.contains('is-open');
      if (isOpen) {
        closeDrawer();
      } else {
        openDrawer();
      }
    });

    // Close on outside click
    document.addEventListener('click', function (e) {
      if (sidebar.classList.contains('is-open') &&
          !sidebar.contains(e.target) && !toggle.contains(e.target)) {
        closeDrawer();
      }
    });

    // Close on Escape
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
  }
}());
