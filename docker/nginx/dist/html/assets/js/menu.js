// A menu of the header: a button that opens a list of choices (menuitemradio). Click, Enter, Space or
// the arrow down open it, the arrows move, Enter picks, Escape, Tab or a click elsewhere close it and
// give the focus back to the button. One menu is open at a time.
(function () {
  'use strict';
  var TPot = window.TPot = window.TPot || {};

  var menus = [];

  function closeAll(except) {
    menus.forEach(function (m) { if (m !== except) m.close(false); });
  }

  document.addEventListener('pointerdown', function (ev) {
    if (!ev.target.closest('.menu')) closeAll(null);
  });

  // root: the .menu element; onPick(value) is called when a choice is taken
  TPot.menu = function (root, onPick) {
    var button = root.querySelector('.menu-button');
    var items = Array.prototype.slice.call(root.querySelectorAll('[role="menuitemradio"]'));
    var label = button.querySelector('.menu-label');

    function isOpen() { return root.classList.contains('open'); }

    function focusItem(i) {
      items[(i + items.length) % items.length].focus();
    }

    function checked() {
      for (var i = 0; i < items.length; i++) if (items[i].getAttribute('aria-checked') === 'true') return i;
      return 0;
    }

    var api = {
      open: function () {
        closeAll(api);
        root.classList.add('open');
        button.setAttribute('aria-expanded', 'true');
        focusItem(checked());
      },
      close: function (focus) {
        if (!isOpen()) return;
        root.classList.remove('open');
        button.setAttribute('aria-expanded', 'false');
        if (focus) button.focus();
      },
      // shows a value as chosen, without calling onPick
      set: function (value) {
        items.forEach(function (it) {
          var on = it.getAttribute('data-value') === value;
          it.setAttribute('aria-checked', String(on));
          if (on) label.textContent = it.querySelector('.item-name').textContent;
        });
      }
    };

    button.addEventListener('click', function () { if (isOpen()) api.close(true); else api.open(); });
    button.addEventListener('keydown', function (ev) {
      if (ev.key === 'ArrowDown' || ev.key === 'ArrowUp') {
        ev.preventDefault();
        api.open();
      }
    });

    items.forEach(function (it, i) {
      it.addEventListener('click', function () {
        var value = it.getAttribute('data-value');
        api.set(value);
        api.close(true);
        onPick(value);
      });
      it.addEventListener('keydown', function (ev) {
        if (ev.key === 'ArrowDown') { ev.preventDefault(); focusItem(i + 1); }
        else if (ev.key === 'ArrowUp') { ev.preventDefault(); focusItem(i - 1); }
        else if (ev.key === 'Home') { ev.preventDefault(); focusItem(0); }
        else if (ev.key === 'End') { ev.preventDefault(); focusItem(items.length - 1); }
        else if (ev.key === 'Escape') { ev.preventDefault(); api.close(true); }
        else if (ev.key === 'Tab') { api.close(false); }
      });
    });

    menus.push(api);
    return api;
  };
})();
