/* Portfoliobot Dashboard — micro-interactions + Sparplan customize.
   Hallmark: animate transform + opacity only. Reduced-motion respected.
   anime.js loaded from CDN (defer). Sparplan-Logik laeuft unabhaengig
   von anime.js, damit Section-Collapse auch ohne CDN funktioniert.
*/
(function () {
  'use strict';

  // 0) Theme (Light/Dark) Init. Reihenfolge:
  //    1. localStorage "theme" (explizite User-Wahl) gewinnt
  //    2. sonst System-Preference via prefers-color-scheme
  //    3. sonst Default light
  // Setzt data-theme auf <html> BEVOR das CSS zum ersten Mal paintet -- wird
  // vor dem ersten <script>-Tick in head ausgefuehrt.
  (function initTheme() {
    var stored = null;
    try { stored = localStorage.getItem('theme'); } catch (e) { stored = null; }
    var theme;
    if (stored === 'light' || stored === 'dark') {
      theme = stored;
    } else if (window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches) {
      theme = 'dark';
    } else {
      theme = 'light';
    }
    document.documentElement.setAttribute('data-theme', theme);
  })();

  // 0b) Theme-Toggle. Klick auf [data-theme-toggle]: light <-> dark,
  //     speichert in localStorage. Funktioniert auch wenn anime.js nicht
  //     geladen ist (fruehe Bindung).
  (function initThemeToggle() {
    var btn = document.querySelector('[data-theme-toggle]');
    if (!btn) return;
    btn.addEventListener('click', function () {
      var current = document.documentElement.getAttribute('data-theme') || 'light';
      var next = current === 'dark' ? 'light' : 'dark';
      document.documentElement.setAttribute('data-theme', next);
      try { localStorage.setItem('theme', next); } catch (e) { /* private mode */ }
      syncTradingViewTheme();
    });
  })();

  // 0c) TradingView-iframe theme-sync: tauscht das `theme=...`-Segment in der
  // iframe-src aus, sobald sich data-theme auf <html> aendert. Damit folgt
  // der Widget dem Light/Dark-Mode des Dashboards ohne Reload.
  (function initTradingViewTheme() {
    function sync() {
      var iframes = document.querySelectorAll('.tv-iframe');
      if (!iframes.length) return;
      var theme = document.documentElement.getAttribute('data-theme') === 'dark' ? 'dark' : 'light';
      iframes.forEach(function (f) {
        try {
          var src = f.getAttribute('src') || '';
          if (!src) return;
          var updated = src.replace(/([?&])theme=(light|dark)/, '$1theme=' + theme);
          if (updated === src) {
            // falls theme-Param noch nicht da: anhaengen
            updated = src + (src.indexOf('?') >= 0 ? '&' : '?') + 'theme=' + theme;
          }
          f.setAttribute('src', updated);
        } catch (e) { /* cross-origin reload-sicher */ }
      });
    }
    window.syncTradingViewTheme = sync;
    // initial nach DOMContentLoaded
    if (document.readyState === 'loading') {
      document.addEventListener('DOMContentLoaded', sync);
    } else {
      sync();
    }
  })();

  // 1) Sparplan: per-section collapse/expand with localStorage state.
  // Like Reinstrom-Wiki Spielmodus: a top-bar mode toggle plus per-section
  // state, persisted client-side.
  (function initSparplanSections() {
    var STORAGE_KEY = 'sparplan-sections-v1';
    // Default: all sections visible
    var DEFAULT_STATE = {};

    function loadState() {
      try {
        var raw = localStorage.getItem(STORAGE_KEY);
        if (!raw) return JSON.parse(JSON.stringify(DEFAULT_STATE));
        return JSON.parse(raw);
      } catch (e) {
        return JSON.parse(JSON.stringify(DEFAULT_STATE));
      }
    }

    function saveState(state) {
      try { localStorage.setItem(STORAGE_KEY, JSON.stringify(state)); } catch (e) {}
    }

    function isCollapsed(state, track, section) {
      return Boolean(state[track] && state[track][section]);
    }

    function setCollapsed(state, track, section, value) {
      if (!state[track]) state[track] = {};
      state[track][section] = !!value;
      saveState(state);
    }

    function applyState() {
      var state = loadState();
      document.querySelectorAll('[data-section]').forEach(function (el) {
        var track = el.getAttribute('data-track') || (el.closest('.track') || {}).getAttribute?.('data-track');
        // Track aus dem naechsten .track-Element ableiten
        if (!track) {
          var tEl = el.closest('.track');
          if (tEl) track = tEl.getAttribute('data-track');
        }
        var section = el.getAttribute('data-section');
        if (!track || !section) return;
        if (isCollapsed(state, track, section)) {
          el.classList.add('is-collapsed');
        } else {
          el.classList.remove('is-collapsed');
        }
      });
    }

    applyState();

    // Per-section toggle (click on header with data-section-toggle)
    document.addEventListener('click', function (ev) {
      var toggleEl = ev.target.closest('[data-section-toggle]');
      if (!toggleEl) return;
      if (toggleEl.hasAttribute('data-no-collapse')) return;
      var trackEl = toggleEl.closest('.track');
      if (!trackEl) return;
      var track = trackEl.getAttribute('data-track');
      var section = toggleEl.getAttribute('data-section-toggle');
      if (!track || !section) return;
      var state = loadState();
      var nowCollapsed = !isCollapsed(state, track, section);
      setCollapsed(state, track, section, nowCollapsed);
      // Toggle the corresponding section
      var sectionEl = document.querySelector('[data-section="' + section + '"]');
      if (sectionEl) sectionEl.classList.toggle('is-collapsed', nowCollapsed);
    });

    // "Alle einklappen" / "Alle ausklappen"
    var collapseAll = document.querySelector('[data-collapse-all]');
    var expandAll = document.querySelector('[data-expand-all]');

    if (collapseAll) {
      collapseAll.addEventListener('click', function () {
        var state = loadState();
        document.querySelectorAll('.track').forEach(function (trackEl) {
          var track = trackEl.getAttribute('data-track');
          if (!track) return;
          if (!state[track]) state[track] = {};
          trackEl.querySelectorAll('[data-section]').forEach(function (el) {
            var section = el.getAttribute('data-section');
            if (section) {
              state[track][section] = true;
              el.classList.add('is-collapsed');
            }
          });
        });
        saveState(state);
      });
    }

    if (expandAll) {
      expandAll.addEventListener('click', function () {
        var state = loadState();
        document.querySelectorAll('.track').forEach(function (trackEl) {
          var track = trackEl.getAttribute('data-track');
          if (!track) return;
          if (!state[track]) state[track] = {};
          trackEl.querySelectorAll('[data-section]').forEach(function (el) {
            var section = el.getAttribute('data-section');
            if (section) {
              state[track][section] = false;
              el.classList.remove('is-collapsed');
            }
          });
        });
        saveState(state);
      });
    }
  })();

  // 2) Sparplan: pro-Track hide/show + Reset, localStorage-backed.
  // Like Reinstrom-Wiki Spielmodus: lokaler Customization-State, der beim
  // Reload erhalten bleibt. Reset = loescht alle Sparplan-States und reload.
  (function initSparplanTracks() {
    var SECTIONS_KEY = 'sparplan-sections-v1';
    var TRACKS_KEY = 'sparplan-tracks-v1';
    var DEFAULT_TRACKS = {};

    function loadTracks() {
      try {
        var raw = localStorage.getItem(TRACKS_KEY);
        if (!raw) return JSON.parse(JSON.stringify(DEFAULT_TRACKS));
        return JSON.parse(raw);
      } catch (e) {
        return JSON.parse(JSON.stringify(DEFAULT_TRACKS));
      }
    }
    function saveTracks(state) {
      try { localStorage.setItem(TRACKS_KEY, JSON.stringify(state)); } catch (e) {}
    }
    function isHidden(state, track) {
      return Boolean(state[track] && state[track].hidden);
    }
    function setHidden(state, track, value) {
      if (!state[track]) state[track] = {};
      state[track].hidden = !!value;
      saveTracks(state);
    }

    function rebuildShowRow(state) {
      var row = document.querySelector('[data-track-show-row]');
      if (!row) return;
      // Bestehende Buttons entfernen
      while (row.firstChild) row.removeChild(row.firstChild);
      // Pro verstecktem Track einen Show-Button erzeugen
      document.querySelectorAll('.track[data-track]').forEach(function (trackEl) {
        var key = trackEl.getAttribute('data-track');
        if (!isHidden(state, key)) return;
        var labelEl = trackEl.querySelector('.track-titles .h2');
        var label = labelEl ? labelEl.textContent.trim() : key;
        var btn = document.createElement('button');
        btn.type = 'button';
        btn.setAttribute('data-track-show', key);
        btn.textContent = '+ ' + label + ' einblenden';
        row.appendChild(btn);
      });
    }

    function applyTrackState() {
      var state = loadTracks();
      document.querySelectorAll('.track[data-track]').forEach(function (trackEl) {
        var key = trackEl.getAttribute('data-track');
        if (isHidden(state, key)) {
          trackEl.classList.add('is-hidden');
        } else {
          trackEl.classList.remove('is-hidden');
        }
      });
      rebuildShowRow(state);
    }

    applyTrackState();

    // Hide-Button im Track-Header
    document.addEventListener('click', function (ev) {
      var btn = ev.target.closest('[data-track-hide]');
      if (!btn) return;
      var key = btn.getAttribute('data-track-hide');
      if (!key) return;
      var state = loadTracks();
      setHidden(state, key, true);
      applyTrackState();
    });

    // Show-Button in der Toolbar (dynamisch eingefuegt)
    document.addEventListener('click', function (ev) {
      var btn = ev.target.closest('[data-track-show]');
      if (!btn) return;
      var key = btn.getAttribute('data-track-show');
      if (!key) return;
      var state = loadTracks();
      setHidden(state, key, false);
      applyTrackState();
    });

    // Reset: loescht beide Sparplan-Storages und reload
    var resetBtn = document.querySelector('[data-reset-sparplan]');
    if (resetBtn) {
      resetBtn.addEventListener('click', function () {
        try { localStorage.removeItem(SECTIONS_KEY); } catch (e) {}
        try { localStorage.removeItem(TRACKS_KEY); } catch (e) {}
        // Sanity-Reset via reload (so wird der applyState sauber neu initialisiert)
        location.reload();
      });
    }
  })();

  // 3) Micro-Interactions (anime.js). Opt-in: nur wenn anime.js geladen.
  if (typeof window.anime === 'undefined') {
    // anime.js failed to load -- content is fully readable, nur die Stagger-
    // Animationen fehlen. Bewusst kein throw, kein console.error.
    return;
  }

  var prefersReduced = window.matchMedia('(prefers-reduced-motion: reduce)').matches;

  // 3a) Stagger the topbar links in
  var topbar = document.querySelector('.topbar');
  if (topbar) {
    var navLinks = topbar.querySelectorAll('.nav-link, .brand');
    if (!prefersReduced) {
      window.anime({
        targets: navLinks,
        opacity: [0, 1],
        translateY: [-6, 0],
        delay: window.anime.stagger(40, { start: 50 }),
        duration: 320,
        easing: 'cubicBezier(0.22, 1, 0.36, 1)'
      });
    } else {
      navLinks.forEach(function (el) { el.style.opacity = '1'; });
    }
  }

  // 3b) Stagger page-head + main children
  var root = document.querySelector('[data-stagger-root]');
  if (root) {
    var groups = root.querySelectorAll('[data-stagger-children]');
    if (!prefersReduced) {
      groups.forEach(function (group, gIdx) {
        var children = group.children;
        if (!children.length) return;
        window.anime({
          targets: children,
          opacity: [0, 1],
          translateY: [12, 0],
          delay: window.anime.stagger(50, { start: 200 + gIdx * 60 }),
          duration: 380,
          easing: 'cubicBezier(0.22, 1, 0.36, 1)'
        });
      });
    } else {
      groups.forEach(function (group) {
        Array.prototype.forEach.call(group.children, function (el) { el.style.opacity = '1'; });
      });
    }
  }

  // 3c) Live "Stand" tick (every 30s)
  var standEls = document.querySelectorAll('.topbar-meta .mono, .footer-inner .mono');
  setInterval(function () {
    var now = new Date();
    var stamp = now.getFullYear() + '-' + String(now.getMonth() + 1).padStart(2, '0') + '-' + String(now.getDate()).padStart(2, '0') + ' ' + String(now.getHours()).padStart(2, '0') + ':' + String(now.getMinutes()).padStart(2, '0');
    standEls.forEach(function (el) {
      if (/Stand \d{4}-\d{2}-\d{2} \d{2}:\d{2}/.test(el.textContent)) {
        el.textContent = el.textContent.replace(/Stand \d{4}-\d{2}-\d{2} \d{2}:\d{2}/, 'Stand ' + stamp);
      }
    });
  }, 30000);
})();
