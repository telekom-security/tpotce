/**
 * Copyright (c) 2017
 *  Thomas Müller <thomas.mueller@tmit.eu>
 *
 * This file is licensed under the Affero General Public License version 3 or later.
 * See the COPYING-README file.
 */

$(document).ready(function() {

	// You can Customize here
	window.$buoop = {
		vs: { i: 10, f: 54, o: 0, s: 9, c: 60 },
		reminder: 0,                  // after how many hours should the message reappear
		test: false,                    // true = always show the bar (for testing)
		newwindow: true,                // open link in new window/tab
		url: null,                      // the url to go to after clicking the notification
		noclose:true                  // Do not show the "ignore" button to close the notification
	};

	var path = OC.filePath('core','vendor','browser-update/browser-update.js');
	$.getScript(path);
});