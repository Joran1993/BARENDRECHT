/* ── UX micro-interactions ───────────────────────────────────────────────── */
(function () {
  'use strict';

  /* ── Toast ──────────────────────────────────────────────────────────────── */
  window.uxToast = function (msg, type, duration) {
    type = type || 'default';
    duration = duration || 3000;
    // Remove existing toasts
    document.querySelectorAll('.ux-toast').forEach(function (t) { t.remove(); });
    var el = document.createElement('div');
    el.className = 'ux-toast ' + type;
    if (type === 'success') el.innerHTML = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><polyline points="20 6 9 17 4 12"/></svg>';
    if (type === 'error')   el.innerHTML = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><line x1="15" y1="9" x2="9" y2="15"/><line x1="9" y1="9" x2="15" y2="15"/></svg>';
    if (type === 'info')    el.innerHTML = '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"/><line x1="12" y1="8" x2="12" y2="12"/><line x1="12" y1="16" x2="12.01" y2="16"/></svg>';
    el.appendChild(document.createTextNode(msg));
    document.body.appendChild(el);
    setTimeout(function () {
      el.classList.add('leaving');
      setTimeout(function () { el.remove(); }, 250);
    }, duration);
  };

  /* ── Button spinner helper ───────────────────────────────────────────────── */
  window.uxBtnLoading = function (btn, loading) {
    if (loading) {
      btn._origText = btn.innerHTML;
      btn.disabled = true;
      btn.innerHTML = '<span class="spinner"></span>' + (btn.dataset.loadingText || 'Bezig…');
    } else {
      btn.disabled = false;
      btn.innerHTML = btn._origText || btn.innerHTML;
    }
  };

  document.addEventListener('DOMContentLoaded', function () {

    /* ── Header scroll shadow ──────────────────────────────────────────────── */
    var hdr = document.querySelector('.hdr');
    if (hdr) {
      function checkScroll() {
        var scrolled = false;
        document.querySelectorAll('.tab.active').forEach(function (t) {
          if (t.scrollTop > 2) scrolled = true;
        });
        if (window.scrollY > 2) scrolled = true;
        hdr.classList.toggle('scrolled', scrolled);
      }
      window.addEventListener('scroll', checkScroll, { passive: true });
      document.querySelectorAll('.tab').forEach(function (t) {
        t.addEventListener('scroll', checkScroll, { passive: true });
      });
    }

    /* ── Tabbar icon bounce ──────────────────────────────────────────────── */
    document.querySelectorAll('.tabbar-btn').forEach(function (btn) {
      btn.addEventListener('click', function () {
        btn.classList.remove('tapped');
        void btn.offsetWidth; // force reflow
        btn.classList.add('tapped');
        setTimeout(function () { btn.classList.remove('tapped'); }, 300);
      });
    });

    /* ── List item stagger ───────────────────────────────────────────────── */
    function staggerItems(root) {
      var rows = root.querySelectorAll('.item-row:not([data-staggered])');
      rows.forEach(function (row, i) {
        row.setAttribute('data-staggered', '1');
        row.style.setProperty('--stagger', Math.min(i * 38, 220) + 'ms');
      });
    }
    // Initial stagger
    document.querySelectorAll('.tab').forEach(staggerItems);
    // Stagger newly added items
    var obs = new MutationObserver(function (mutations) {
      mutations.forEach(function (m) {
        if (m.addedNodes.length) {
          m.addedNodes.forEach(function (n) {
            if (n.nodeType === 1) {
              if (n.classList && n.classList.contains('item-row')) {
                var idx = n.parentElement
                  ? Array.from(n.parentElement.querySelectorAll('.item-row')).indexOf(n)
                  : 0;
                n.setAttribute('data-staggered', '1');
                n.style.setProperty('--stagger', Math.min(idx * 38, 220) + 'ms');
              }
              staggerItems(n);
            }
          });
        }
      });
    });
    document.querySelectorAll('.tab, #items-list, #reacties-list').forEach(function (t) {
      obs.observe(t, { childList: true, subtree: true });
    });

    /* ── Intercept error-bar om toasts te tonen ──────────────────────────── */
    var errBars = document.querySelectorAll('.error-bar');
    errBars.forEach(function (bar) {
      var origDisplay = Object.getOwnPropertyDescriptor(CSSStyleDeclaration.prototype, 'display');
      var observer = new MutationObserver(function () {
        if (bar.style.display === 'block' && bar.textContent.trim()) {
          uxToast(bar.textContent.trim(), 'error', 4000);
        }
      });
      observer.observe(bar, { attributes: true, attributeFilter: ['style'], childList: true });
    });

    /* ── Analyse-knop — spinner tijdens laden ────────────────────────────── */
    var analyseBtn = document.getElementById('analyse-btn');
    if (analyseBtn) {
      var origAnalyseClick = analyseBtn.onclick;
      analyseBtn.addEventListener('click', function () {
        if (!analyseBtn.disabled) {
          setTimeout(function () {
            if (analyseBtn.disabled) {
              var txt = document.getElementById('analyse-txt');
              if (txt) txt.innerHTML = '<span class="spinner"></span> Analyseren…';
            }
          }, 50);
        }
      }, true);
    }

    /* ── Login form — spinner op submit ─────────────────────────────────── */
    var loginForm = document.getElementById('form');
    if (loginForm) {
      loginForm.addEventListener('submit', function () {
        var btn = loginForm.querySelector('button[type=submit]');
        if (btn && !btn.disabled) {
          setTimeout(function () {
            if (btn.disabled) btn.innerHTML = '<span class="spinner"></span> Inloggen…';
          }, 30);
        }
      });
    }

  });
})();
