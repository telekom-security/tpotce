// generic layout specific responsive stuff goes here

function openFlyout() {
  $('html').addClass('flyout-is-active');
  $('#wrapper2').on('click', function(e){
    e.preventDefault();
    e.stopPropagation();
    closeFlyout();
  });
}

function closeFlyout() {
  $('html').removeClass('flyout-is-active');
  $('#wrapper2').off('click');
}


function isMobile() {
  return $('.js-flyout-menu-toggle-button').is(":visible");
}

function setupFlyout() {
  var mobileInit = false,
    desktopInit = false;

  /* click handler for mobile menu toggle */
  $('.js-flyout-menu-toggle-button').on('click', function(e) {
    e.preventDefault();
    e.stopPropagation();
    if($('html').hasClass('flyout-is-active')) {
      closeFlyout();
    } else {
      openFlyout();
    }
  });

  /* bind resize handler */
  $(window).resize(function() {
    initMenu();
  })

  /* menu init function for dom detaching and appending on mobile / desktop view */
  function initMenu() {

    var _initMobileMenu = function() {
      /* only init mobile menu, if it hasn't been done yet */
      if(!mobileInit) {

        $('#main-menu > ul').detach().appendTo('.js-project-menu');
        $('#top-menu > ul').detach().appendTo('.js-general-menu');
        $('#sidebar > *').detach().appendTo('.js-sidebar');
        $('#account > ul').detach().appendTo('.js-profile-menu');

        mobileInit = true;
        desktopInit = false;
      }
    }

    var _initDesktopMenu = function() {
      if(!desktopInit) {

        $('.js-project-menu > ul').detach().appendTo('#main-menu');
        $('.js-general-menu > ul').detach().appendTo('#top-menu');
        $('.js-sidebar > *').detach().appendTo('#sidebar');
        $('.js-profile-menu > ul').detach().appendTo('#account');

        desktopInit = true;
        mobileInit = false;
      }
    }

    if(isMobile()) {
      _initMobileMenu();
    } else {
      _initDesktopMenu();
    }
  }

  // init menu on page load
  initMenu();
}

$(document).ready(setupFlyout);
