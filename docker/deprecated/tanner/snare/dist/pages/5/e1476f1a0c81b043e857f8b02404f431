/*
 * Copyright (c) 2015
 *
 * This file is licensed under the Affero General Public License version 3
 * or later.
 *
 * See the COPYING-README file.
 *
 */

(function () {
	if (!OC.Share) {
		OC.Share = {};
	}

	var TEMPLATE =
		'<span class="icon-loading-small hidden"></span>' +
		'<ul class="link-shares">' +
		'{{#each shares}}' +
		'<li class="link-entry" data-id="{{id}}">' +
			'<span class="link-entry--icon icon-public-white"></span>' +
			'<span class="link-entry--title">{{linkTitle}}</span>' +
			'<div class="minify"><input id="linkText-{{../cid}}-{{id}}" class="linkText" type="text" readonly="readonly" value="{{link}}" /></div>' +
			'<div class="link-entry--icon-button clipboardButton" data-clipboard-target="#linkText-{{../cid}}-{{id}}" title="{{../copyToClipboardText}}">' +
			'	<span class="icon icon-clippy-dark"></span>' +
			'	<span class="hidden">{{../copyToClipboardText}}</span>' +
			'</div>' +
			'<div class="link-entry--icon-button editLink" title="{{../editLinkText}}">' +
			'	<span class="icon icon-settings-dark"></span>' +
			'	<span class="hidden">{{../editLinkText}}</span>' +
			'</div>' +
			'{{#if ../socialShareEnabled}}' +
			'<div class="link-entry--icon-button shareLink" title="{{../shareText}}">' +
			'	<span class="icon icon-share"></span>' +
			'	<span class="hidden">{{../shareText}}</span>' +
			'</div>' +
			'{{/if}}' +
			'<div class="link-entry--icon-button removeLink"  title="{{../removeLinkText}}">' +
			'	<span class="icon icon-delete"></span>' +
			'	<span class="hidden">{{../removeLinkText}}</span>' +
			'</div>' +
			'{{#if ../socialShareEnabled}}' +
			'<div class="socialShareContainer hidden"></div>' +
			'{{/if}}' +
		'</li>' +
		'{{/each}}' +
		'</ul>' +
		'{{#if noShares}}' +
		'<div class="empty-message">{{noSharesMessage}}</div>' +
		'{{/if}}' +
		'<div class="clear-both">' +
		'	<button class="addLink">{{addLinkText}}</button>' +
		'</div>' +
		'<div class="privacyWarningMessage">{{privacyWarningMessage}}</div>';

	/**
	 * @class OCA.Share.ShareDialogLinkListView
	 * @member {OC.Share.ShareItemModel} model
	 * @member {jQuery} $el
	 * @memberof OCA.Sharing
	 * @classdesc
	 *
	 * Represents the GUI of the share dialogue
	 *
	 */
	var ShareDialogLinkListView = OC.Backbone.View.extend({
		/** @type {string} **/
		id: 'shareDialogLinkList',

		className: 'shareDialogLinkList',

		/** @type {OCA.Files.FileInfoModel} **/
		fileInfoModel: undefined,

		/** @type {OC.Share.ShareConfigModel} **/
		configModel: undefined,

		/** @type {OC.Share.ShareItemModel} **/
		itemModel: undefined,

		/** @type {Function} **/
		_template: undefined,

		events: {
			'click .addLink': 'onAddButtonClick',
			'click .editLink': 'onEditButtonClick',
			'click .removeLink': 'onRemoveButtonClick',
			'click .shareLink': 'onShareButtonClick'
		},

		initialize: function (options) {
			var view = this;

			// if any item in the list changes, render the whole thing
			this.collection.on('change update delete', function() {
				view.render();
			});

			if (!_.isUndefined(options.itemModel)) {
				this.itemModel = options.itemModel;
				this.fileInfoModel = this.itemModel.getFileInfo();
				this.configModel = this.itemModel.configModel;
			} else {
				throw 'missing OC.Share.ShareItemModel';
			}
		},

		onRemoveButtonClick: function (ev) {
			var $target = $(ev.target);
			var linkId = $target.closest('.link-entry').attr('data-id');

			var linkModel = this.collection.get(linkId);
			var linkTitle = (linkModel.attributes.name) ? linkModel.attributes.name : linkModel.attributes.token;

			if (!linkModel) {
				return;
			}

			OCdialogs.confirm(
				t('core', 'Delete {link}', { link: linkTitle }),
				t('core', 'Remove link'),
				function(cb) {
					if (cb) {
						linkModel.destroy();
					}
				},
				true
			);

		},

		_generateName: function() {
			var index = 1;
			var baseName = t('core', '{fileName} link', {
				fileName: this.fileInfoModel.get('name')
			});
			var name = baseName;

			while (this.collection.findWhere({name: name})) {
				index++;
				name = baseName + ' (' + index + ')';
			}

			return name;
		},

		onAddButtonClick: function () {
			var newShare = new OC.Share.ShareModel({
				name: this._generateName(),
				password: '',
				permissions: OC.PERMISSION_READ,
				expireDate: this.configModel.getDefaultExpirationDateString(),
				shareType: OC.Share.SHARE_TYPE_LINK,
				itemType: this.itemModel.get('itemType'),
				itemSource: this.itemModel.get('itemSource')
			});
			this._showPopupForShare(newShare);
		},

		onEditButtonClick: function (ev) {
			var $target = $(ev.target);
			var linkId = $target.closest('.link-entry').attr('data-id');

			var linkModel = this.collection.get(linkId);
			if (!linkModel) {
				return;
			}

			this._showPopupForShare(linkModel);
		},

		onShareButtonClick: function (ev) {
			var $target = $(ev.target);
			var $li = $target.closest('.link-entry');
			var linkId = $li.attr('data-id');
			var $container = $li.find('.socialShareContainer');

			var linkModel = this.collection.get(linkId);
			if (!linkModel) {
				return;
			}

			if ($container.is(':empty')) {
				if (this.configModel.isSocialShareEnabled()) {
					var socialView = new OC.Share.ShareDialogLinkSocialView({
						model: linkModel,
						configModel: this.configModel
					});
					socialView.render();
					$container.append(socialView.$el);
					$container.removeClass('hidden');
				}
			} else {
				$container.toggleClass('hidden', !$container.hasClass('hidden'));
			}
		},

		_showPopupForShare: function(model) {
			var self = this;
			var popupView = new OC.Share.ShareDialogLinkShareView({
				model: model,
				configModel: this.configModel,
				// pass in legacy stuff...
				itemModel: this.itemModel
			});

			popupView.once('saved', function() {
				// add/update in collection on success
				self.collection.add(model);
			});
			popupView.show();
		},

		_formatItem: function(model) {
			return _.extend(model.toJSON(), {
				linkTitle: model.get('name') || model.get('token'),
				link: this._makeLink(model)
			});
		},

		_makeLink: function(model) {
			var link = '';
			if (!model.get('token')) {
				var preTokenLink = OC.getProtocol() + '://' + OC.getHost();
				var fullPath = this.fileInfoModel.get('path') + '/' +
					this.fileInfoModel.get('name');
				var location = '/' + OC.currentUser + '/files' + fullPath;
				var type = this.fileInfoModel.isDirectory() ? 'folder' : 'file';
				link = preTokenLink + OC.linkTo('', 'public.php') + '?service=files&' +
					type + '=' + encodeURIComponent(location);
			} else {
				link = model.getLink();
			}
			return link;
		},

		render: function () {
			this.$el.html(this.template({
				cid: this.cid,
				urlLabel: t('core', 'Link'),
				addLinkText: t('core', 'Create public link'),
				editLinkText: t('core', 'Edit'),
				removeLinkText: t('core', 'Remove'),
				copyToClipboardText: t('core', 'Copy to clipboard'),
				shareText: t('core', 'Social share'),
				socialShareEnabled: this.configModel.isSocialShareEnabled(),
				noShares: !this.collection.length,
				noSharesMessage: t('core', 'There are currently no link shares, you can create one'),
				privacyWarningMessage: t('core', 'Anyone with the link has access to the file/folder'),
				shares: this.collection.map(_.bind(this._formatItem, this))
			}));

			this.$el.find('[title]').tooltip();

			var clipboard = new Clipboard('#' + this.id + ' .clipboardButton');
			clipboard.on('success', function (e) {
				var $input = $(e.trigger);
				$input.tooltip({
					placement: 'bottom',
					trigger: 'manual',
					title: t('core', 'Copied!')
				});
				$input.tooltip('show');
				_.delay(function () {
					$input.tooltip('hide');
				}, 3000);
			});
			clipboard.on('error', function (e) {
				var $input = $(e.trigger);
				var actionMsg = '';
				if (bowser.ios) {
					actionMsg = t('core', 'Not supported!');
				} else if (bowser.mac) {
					actionMsg = t('core', 'Press ⌘-C to copy.');
				} else {
					actionMsg = t('core', 'Press Ctrl-C to copy.');
				}

				$input.tooltip({
					placement: 'bottom',
					trigger: 'manual',
					title: actionMsg
				});
				$input.tooltip('show');
				_.delay(function () {
					$input.tooltip('hide');
				}, 3000);
			});

			this.delegateEvents();

			return this;
		},

		/**
		 * @returns {Function} from Handlebars
		 * @private
		 */
		template: function (data) {
			if (!this._template) {
				this._template = Handlebars.compile(TEMPLATE);
			}
			return this._template(data);
		}

	});

	OC.Share.ShareDialogLinkListView = ShareDialogLinkListView;

})();
