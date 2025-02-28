var videoViewer = {
	UI : {
		playerTemplate : '<header><link href="'+OC.filePath('files_videoplayer', 'videojs', 'src')+'/video-js.css" rel="stylesheet"><script src="'+OC.filePath('files_videoplayer', 'videojs', 'src')+'/video.js"></script>' +
		'</header><video id="my_video_1" class="video-js vjs-sublime-skin" controls preload="auto" width="100%" height="100%" poster="'+OC.filePath('files_videoplayer', '', 'img')+'/poster.png" data-setup=\'{"techOrder": ["html5"]}\'>' +
		'<source type="%type%" src="%src%" />' +
		'</video>',
		show : function () {
			// insert HTML
			$('<div id="videoplayer_overlay" style="display:none;"><div id="videoplayer_outer_container"><div id="videoplayer_container"><div id="videoplayer"></div></div></div></div>').appendTo('body');
			var playerView = videoViewer.UI.playerTemplate
								.replace(/%type%/g, escapeHTML(videoViewer.mime))
								.replace(/%src%/g, escapeHTML(videoViewer.location))
			;
			$(playerView).prependTo('#videoplayer');
			// add event to overlay
			$("#videoplayer_overlay").on("click", function(e) {
				if (e.target != this) {
					return;
				} else {
					videoViewer.hidePlayer();
				}
			});
			// show elements
			$('#videoplayer_overlay').fadeIn('fast');
			// initialize player
			var vjsPlayer = videojs("my_video_1");
			// append close button to video element
			$("#my_video_1").append('<a class="icon-view-close" id="box-close" href="#"></a>');
			// add event to close button
			$('#box-close').click(videoViewer.hidePlayer);
			// autoplay
			vjsPlayer.play();
		},
		hide : function() {
			$('#videoplayer_overlay').fadeOut('fast', function() {
				$('#videoplayer_overlay').remove();
			});
		}
	},
	mime : null,
	file : null,
	location : null,
	player : null,
	mimeTypes : [
		'video/mp4',
		'video/webm',
		'video/x-flv',
		'video/ogg',
		'video/quicktime'
	],
	onView : function(file, data) {
		videoViewer.file = file;
		videoViewer.dir = data.dir;
		videoViewer.location = data.fileList.getDownloadUrl(file, videoViewer.dir);
		videoViewer.mime = data.$file.attr('data-mime');
		videoViewer.showPlayer();
	},
	showPlayer : function() {
		videoViewer.UI.show();
	},
	hidePlayer : function() {
		videoViewer.player = false;
		delete videoViewer.player;
		videoViewer.UI.hide();
		// force close socket
		$('video').each(function() {
		    $($(this)[0]).attr('src', '');
		});
	},
	log : function(message){
		console.log(message);
	}
};

$(document).ready(function(){

	// add event to ESC key
	$(document).keyup(function(e) {
		if (e.keyCode === 27) {
			videoViewer.hidePlayer();
		}
	});

	if (typeof FileActions !== 'undefined') {
		for (var i = 0; i < videoViewer.mimeTypes.length; ++i) {
			var mime = videoViewer.mimeTypes[i];
			OCA.Files.fileActions.register(mime, 'View', OC.PERMISSION_READ, '', videoViewer.onView);
			OCA.Files.fileActions.setDefault(mime, 'View');
		}
	}

});
