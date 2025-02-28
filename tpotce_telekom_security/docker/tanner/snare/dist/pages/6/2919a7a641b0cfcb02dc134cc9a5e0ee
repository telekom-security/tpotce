/* Redmine - project management software
   Copyright (C) 2006-2017  Jean-Philippe Lang */

var contextMenuObserving;

function contextMenuRightClick(event) {
  var target = $(event.target);
  if (target.is('a')) {return;}
  var tr = target.closest('.hascontextmenu').first();
  if (tr.length < 1) {return;}
  event.preventDefault();
  if (!contextMenuIsSelected(tr)) {
    contextMenuUnselectAll();
    contextMenuAddSelection(tr);
    contextMenuSetLastSelected(tr);
  }
  contextMenuShow(event);
}

function contextMenuClick(event) {
  var target = $(event.target);
  var lastSelected;

  if (target.is('a') && target.hasClass('submenu')) {
    event.preventDefault();
    return;
  }
  contextMenuHide();
  if (target.is('a') || target.is('img')) { return; }
  if (event.which == 1 || (navigator.appVersion.match(/\bMSIE\b/))) {
    var tr = target.closest('.hascontextmenu').first();
    if (tr.length > 0) {
      // a row was clicked, check if the click was on checkbox
      if (target.is('input')) {
        // a checkbox may be clicked
        if (target.prop('checked')) {
          tr.addClass('context-menu-selection');
        } else {
          tr.removeClass('context-menu-selection');
        }
      } else {
        if (event.ctrlKey || event.metaKey) {
          contextMenuToggleSelection(tr);
        } else if (event.shiftKey) {
          lastSelected = contextMenuLastSelected();
          if (lastSelected.length) {
            var toggling = false;
            $('.hascontextmenu').each(function(){
              if (toggling || $(this).is(tr)) {
                contextMenuAddSelection($(this));
              }
              if ($(this).is(tr) || $(this).is(lastSelected)) {
                toggling = !toggling;
              }
            });
          } else {
            contextMenuAddSelection(tr);
          }
        } else {
          contextMenuUnselectAll();
          contextMenuAddSelection(tr);
        }
        contextMenuSetLastSelected(tr);
      }
    } else {
      // click is outside the rows
      if (target.is('a') && (target.hasClass('disabled') || target.hasClass('submenu'))) {
        event.preventDefault();
      } else if (target.is('.toggle-selection') || target.is('.ui-dialog *') || $('#ajax-modal').is(':visible')) {
        // nop
      } else {
        contextMenuUnselectAll();
      }
    }
  }
}

function contextMenuCreate() {
  if ($('#context-menu').length < 1) {
    var menu = document.createElement("div");
    menu.setAttribute("id", "context-menu");
    menu.setAttribute("style", "display:none;");
    document.getElementById("content").appendChild(menu);
  }
}

function contextMenuShow(event) {
  var mouse_x = event.pageX;
  var mouse_y = event.pageY;  
  var mouse_y_c = event.clientY;  
  var render_x = mouse_x;
  var render_y = mouse_y;
  var dims;
  var menu_width;
  var menu_height;
  var window_width;
  var window_height;
  var max_width;
  var max_height;
  var url;

  $('#context-menu').css('left', (render_x + 'px'));
  $('#context-menu').css('top', (render_y + 'px'));
  $('#context-menu').html('');

  url = $(event.target).parents('form').first().data('cm-url');
  if (url == null) {alert('no url'); return;}

  $.ajax({
    url: url,
    data: $(event.target).parents('form').first().serialize(),
    success: function(data, textStatus, jqXHR) {
      $('#context-menu').html(data);
      menu_width = $('#context-menu').width();
      menu_height = $('#context-menu').height();
      max_width = mouse_x + 2*menu_width;
      max_height = mouse_y_c + menu_height;

      var ws = window_size();
      window_width = ws.width;
      window_height = ws.height;

      /* display the menu above and/or to the left of the click if needed */
      if (max_width > window_width) {
       render_x -= menu_width;
       $('#context-menu').addClass('reverse-x');
      } else {
       $('#context-menu').removeClass('reverse-x');
      }

      if (max_height > window_height) {
       render_y -= menu_height;
       $('#context-menu').addClass('reverse-y');
        // adding class for submenu
        if (mouse_y_c < 325) {
          $('#context-menu .folder').addClass('down');
        }
      } else {
        // adding class for submenu
        if (window_height - mouse_y_c < 345) {
          $('#context-menu .folder').addClass('up');
        } 
        $('#context-menu').removeClass('reverse-y');
      }

      if (render_x <= 0) render_x = 1;
      if (render_y <= 0) render_y = 1;
      $('#context-menu').css('left', (render_x + 'px'));
      $('#context-menu').css('top', (render_y + 'px'));
      $('#context-menu').show();

      //if (window.parseStylesheets) { window.parseStylesheets(); } // IE
    }
  });
}

function contextMenuSetLastSelected(tr) {
  $('.cm-last').removeClass('cm-last');
  tr.addClass('cm-last');
}

function contextMenuLastSelected() {
  return $('.cm-last').first();
}

function contextMenuUnselectAll() {
  $('input[type=checkbox].toggle-selection').prop('checked', false);
  $('.hascontextmenu').each(function(){
    contextMenuRemoveSelection($(this));
  });
  $('.cm-last').removeClass('cm-last');
}

function contextMenuHide() {
  $('#context-menu').hide();
}

function contextMenuToggleSelection(tr) {
  if (contextMenuIsSelected(tr)) {
    contextMenuRemoveSelection(tr);
  } else {
    contextMenuAddSelection(tr);
  }
}

function contextMenuAddSelection(tr) {
  tr.addClass('context-menu-selection');
  contextMenuCheckSelectionBox(tr, true);
  contextMenuClearDocumentSelection();
}

function contextMenuRemoveSelection(tr) {
  tr.removeClass('context-menu-selection');
  contextMenuCheckSelectionBox(tr, false);
}

function contextMenuIsSelected(tr) {
  return tr.hasClass('context-menu-selection');
}

function contextMenuCheckSelectionBox(tr, checked) {
  tr.find('input[type=checkbox]').prop('checked', checked);
}

function contextMenuClearDocumentSelection() {
  // TODO
  if (document.selection) {
    document.selection.empty(); // IE
  } else {
    window.getSelection().removeAllRanges();
  }
}

function contextMenuInit() {
  contextMenuCreate();
  contextMenuUnselectAll();
  
  if (!contextMenuObserving) {
    $(document).click(contextMenuClick);
    $(document).contextmenu(contextMenuRightClick);
    contextMenuObserving = true;
  }
}

function toggleIssuesSelection(el) {
  var checked = $(this).prop('checked');
  var boxes = $(this).parents('table').find('input[name=ids\\[\\]]');
  boxes.prop('checked', checked).parents('.hascontextmenu').toggleClass('context-menu-selection', checked);
}

function window_size() {
  var w;
  var h;
  if (window.innerWidth) {
    w = window.innerWidth;
    h = window.innerHeight;
  } else if (document.documentElement) {
    w = document.documentElement.clientWidth;
    h = document.documentElement.clientHeight;
  } else {
    w = document.body.clientWidth;
    h = document.body.clientHeight;
  }
  return {width: w, height: h};
}

$(document).ready(function(){
  contextMenuInit();
  $('input[type=checkbox].toggle-selection').on('change', toggleIssuesSelection);
});
