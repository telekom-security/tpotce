/*
 * Copyright (c) 2015
 *
 * This file is licensed under the Affero General Public License version 3
 * or later.
 *
 * See the COPYING-README file.
 *
 */

(function() {
	if(!OC.Share) {
		OC.Share = {};
	}

	var TEMPLATE_BASE =
		'<div class="resharerInfoView subView"></div>' +
		'{{#if isSharingAllowed}}' +
		'{{#if isLinkSharingAllowed}}' +
		'<ul class="subTabHeaders">' +
		'    <li class="subTabHeader selected subtab-localshare">{{localSharesLabel}}</li>' +
		'    <li class="subTabHeader subtab-publicshare">{{publicSharesLabel}}</li>' +
		'</ul>' +
		'{{/if}}' +
		'<div class="tabsContainer">' +
		// TODO: this really should be a separate view class
		'    <div class="localShareView tab" style="padding-left:0;padding-right:0;">' +
		'        <label for="shareWith-{{cid}}" class="hidden-visually">{{shareLabel}}</label>' +
		'        <div class="oneline">' +
		'            <input id="shareWith-{{cid}}" class="shareWithField" type="text" placeholder="{{sharePlaceholder}}" />' +
		'            <span class="shareWithLoading icon-loading-small hidden"></span>'+
		'{{{remoteShareInfo}}}' +
		'        </div>' +
		'        <div class="shareeListView subView"></div>' +
		'    </div>' +
		'    <div class="linkShareView subView tab hidden" style="padding-left:0;padding-right:0;"></div>' +
		'</div>' +
		'{{else}}' +
		'<div class="noSharingPlaceholder">{{noSharingPlaceholder}}</div>' +
		'{{/if}}' +
		'<div class="loading hidden" style="height: 50px"></div>';

	var TEMPLATE_REMOTE_SHARE_INFO =
		'<a target="_blank" class="icon icon-info shareWithRemoteInfo hasTooltip" href="{{docLink}}" ' +
		'title="{{tooltip}}"></a>';

	var TEMPLATE_AUTOCOMPLETE_ITEM =
		'<li class="{{shareTypeClass}}">' +
			'<a>' +
				'<div class="share-autocomplete-item">' +
					'{{#if showAvatar}}' +
					'<div class="avatardiv"></div>' +
					'{{/if}}' +
					'<div class="autocomplete-item-text">' +
						'<span class="autocomplete-item-displayname">{{displayName}}</span>' +
						'{{#if additionalInfo}}' +
						'<span class="autocomplete-item-additional-info">({{additionalInfo}})</span>' +
						'{{/if}}' +
					'</div>' +
				'</div>' +
			'</a>' +
		'</li>';

	/**
	 * @class OCA.Share.ShareDialogView
	 * @member {OC.Share.ShareItemModel} model
	 * @member {jQuery} $el
	 * @memberof OCA.Sharing
	 * @classdesc
	 *
	 * Represents the GUI of the share dialogue
	 *
	 */
	var ShareDialogView = OC.Backbone.View.extend({
		/** @type {Object} **/
		_templates: {},

		/** @type {string} **/
		tagName: 'div',

		/** @type {OC.Share.ShareConfigModel} **/
		configModel: undefined,

		/** @type {object} **/
		resharerInfoView: undefined,

		/** @type {object} **/
		linkShareView: undefined,

		/** @type {object} **/
		shareeListView: undefined,

		events: {
			'input .shareWithField': 'onShareWithFieldChanged',
			'click .subTabHeader': '_onClickTabHeader'
		},

		initialize: function(options) {
			var view = this;

			this.model.on('fetchError', function() {
				OC.Notification.showTemporary(t('core', 'Share details could not be loaded for this item.'));
			});

			if(!_.isUndefined(options.configModel)) {
				this.configModel = options.configModel;
			} else {
				throw 'missing OC.Share.ShareConfigModel';
			}

			this.configModel.on('change:isRemoteShareAllowed', function() {
				view.render();
			});
			this.model.on('change:permissions', function() {
				view.render();
			});

			this.model.on('request', this._onRequest, this);
			this.model.on('sync', this._onEndRequest, this);

			var subViewOptions = {
				model: this.model,
				configModel: this.configModel
			};

			var subViews = {
				resharerInfoView: 'ShareDialogResharerInfoView',
				shareeListView: 'ShareDialogShareeListView'
			};

			for(var name in subViews) {
				var className = subViews[name];
				this[name] = _.isUndefined(options[name])
					? new OC.Share[className](subViewOptions)
					: options[name];
			}

			this.linkShareView = null;

			_.bindAll(this,
				'autocompleteHandler',
				'_onSelectRecipient',
				'onShareWithFieldChanged'
			);

			OC.Plugins.attach('OC.Share.ShareDialogView', this);
		},

		_onClickTabHeader: function(ev) {
			var $target = $(ev.target);
			this.$('.subTabHeaders .subTabHeader.selected').removeClass('selected');

			$target.addClass('selected');

			this.$('.localShareView').toggleClass('hidden', !$target.hasClass('subtab-localshare'));

			var $linkShareView = this.$('.linkShareView');
			if ($linkShareView.length) {
				$linkShareView.toggleClass('hidden', !$target.hasClass('subtab-publicshare'));

				if (!this.linkShareView) {
					this.linkShareView = new OC.Share.ShareDialogLinkListView({
						collection: this.model.getLinkSharesCollection(),
						// pass in the legacy stuff...
						itemModel: this.model
					});
					this.linkShareView.render();
					$linkShareView.append(this.linkShareView.$el);
				}
			}
		},

		onShareWithFieldChanged: function() {
			var $el = this.$el.find('.shareWithField');
			if ($el.val().length < 2) {
				$el.removeClass('error').tooltip('hide');
			}
		},

		autocompleteHandler: function (search, response) {
			var view = this;
			var $loading = this.$el.find('.shareWithLoading');
			$loading.removeClass('hidden');
			$loading.addClass('inlineblock');
			$.get(
				OC.linkToOCS('apps/files_sharing/api/v1') + 'sharees',
				{
					format: 'json',
					search: search.term.trim(),
					perPage: 200,
					itemType: view.model.get('itemType')
				},
				function (result) {
					$loading.addClass('hidden');
					$loading.removeClass('inlineblock');
					if (result.ocs.meta.statuscode == 100) {
						var users   = result.ocs.data.exact.users.concat(result.ocs.data.users);
						var groups  = result.ocs.data.exact.groups.concat(result.ocs.data.groups);
						var remotes = result.ocs.data.exact.remotes.concat(result.ocs.data.remotes);

						var usersLength;
						var groupsLength;
						var remotesLength;

						var i, j;

						//Filter out the current user
						usersLength = users.length;
						for (i = 0 ; i < usersLength; i++) {
							if (users[i].value.shareWith === OC.currentUser) {
								users.splice(i, 1);
								break;
							}
						}

						// Filter out the owner of the share
						if (view.model.hasReshare()) {
							usersLength = users.length;
							for (i = 0 ; i < usersLength; i++) {
								if (users[i].value.shareWith === view.model.getReshareOwner()) {
									users.splice(i, 1);
									break;
								}
							}
						}

						var shares = view.model.get('shares');
						var sharesLength = shares.length;

						// Now filter out all sharees that are already shared with
						for (i = 0; i < sharesLength; i++) {
							var share = shares[i];

							if (share.share_type === OC.Share.SHARE_TYPE_USER) {
								usersLength = users.length;
								for (j = 0; j < usersLength; j++) {
									if (users[j].value.shareWith === share.share_with) {
										users.splice(j, 1);
										break;
									}
								}
							} else if (share.share_type === OC.Share.SHARE_TYPE_GROUP) {
								groupsLength = groups.length;
								for (j = 0; j < groupsLength; j++) {
									if (groups[j].value.shareWith === share.share_with) {
										groups.splice(j, 1);
										break;
									}
								}
							} else if (share.share_type === OC.Share.SHARE_TYPE_REMOTE) {
								remotesLength = remotes.length;
								for (j = 0; j < remotesLength; j++) {
									if (remotes[j].value.shareWith === share.share_with) {
										remotes.splice(j, 1);
										break;
									}
								}
							}
						}

						var suggestions = users.concat(groups).concat(remotes);


						if (suggestions.length > 0) {
							suggestions.sort(function (a, b) {
								return OC.Util.naturalSortCompare(a.label, b.label);
							});
							$('.shareWithField').removeClass('error')
								.tooltip('hide')
								.autocomplete("option", "autoFocus", true);
							response(suggestions, result);
						} else {
							var title = t('core', 'No users or groups found for {search}', {search: $('.shareWithField').val()});
							if (!view.configModel.get('allowGroupSharing')) {
								title = t('core', 'No users found for {search}', {search: $('.shareWithField').val()});
							}
							var suggestStarts = OC.getCapabilities().files_sharing.search_min_length;
							if (suggestStarts > $('.shareWithField').val().length) {
								title = title + '. ' + n(
									'core',
									'Please enter at least {chars} character for suggestions',
									'Please enter at least {chars} characters for suggestions',
									suggestStarts,
									{chars: suggestStarts}
								);
							}
							$('.shareWithField').addClass('error')
								.attr('data-original-title', title)
								.tooltip('hide')
								.tooltip({
									placement: 'bottom',
									trigger: 'manual'
								})
								.tooltip('fixTitle')
								.tooltip('show');
							response(undefined, result);
						}
					} else {
						response(undefined, result);
					}
				}
			).fail(function() {
				$loading.addClass('hidden');
				$loading.removeClass('inlineblock');
				OC.Notification.show(t('core', 'An error occurred. Please try again'));
				window.setTimeout(OC.Notification.hide, 5000);
			});
		},

		autocompleteRenderItem: function(ul, item) {

			var text = item.label;
			if (item.value.shareType === OC.Share.SHARE_TYPE_GROUP) {
				text = t('core', '{sharee} (group)', {
					sharee: text
				});
			} else if (item.value.shareType === OC.Share.SHARE_TYPE_REMOTE) {
				if (item.value.server) {
					text = t('core', '{sharee} (at {server})', {
						sharee: text,
						server: item.value.server
					});
				} else {
					text = t('core', '{sharee} (federated)', {
						sharee: text
					});
				}
			}

			var template = this._getAutocompleteItemTemplate();
			var $el = $(template({
				showAvatar: this.configModel.areAvatarsEnabled(),
				displayName: text,
				additionalInfo: item.value.shareWithAdditionalInfo,
				shareTypeClass: (item.value.shareType === OC.Share.SHARE_TYPE_GROUP) ? 'group' : 'user'
			}));

			if(this.configModel.areAvatarsEnabled()) {
				var $avatar = $el.find('.avatardiv');
				if (item.value.shareType === OC.Share.SHARE_TYPE_USER) {
					$avatar.avatar(item.value.shareWith, 32, undefined, undefined, undefined, item.label);
				} else {
					$avatar.imageplaceholder(text, undefined, 32);
				}
			}

			return $el.appendTo(ul);
		},

		_onSelectRecipient: function(e, s) {
			e.preventDefault();
			$(e.target).attr('disabled', true)
				.val(s.item.label);
			var $loading = this.$el.find('.shareWithLoading');
			$loading.removeClass('hidden')
				.addClass('inlineblock');

			this.model.addShare(s.item.value, {success: function() {
				$(e.target).val('')
					.attr('disabled', false);
				$loading.addClass('hidden')
					.removeClass('inlineblock');
			}, error: function(obj, msg) {
				OC.Notification.showTemporary(msg);
				$(e.target).attr('disabled', false)
					.autocomplete('search', $(e.target).val());
				$loading.addClass('hidden')
					.removeClass('inlineblock');
			}});
		},

		_toggleLoading: function(state) {
			this._loading = state;
			this.$el.find('.localShareView, .noSharingPlaceholder').toggleClass('hidden', state);
			this.$el.find('.loading').toggleClass('hidden', !state);
		},

		_onRequest: function() {
			// only show the loading spinner for the first request (for now)
			if (!this._loadingOnce) {
				this._toggleLoading(true);
			}
		},

		_onEndRequest: function() {
			var self = this;
			this._toggleLoading(false);
			if (!this._loadingOnce) {
				this._loadingOnce = true;
				// the first time, focus on the share field after the spinner disappeared
				if (!bowser.msie) {
					_.defer(function () {
						self.$('.shareWithField').focus();
					});
				}
			}
		},

		render: function() {
			var baseTemplate = this._getTemplate('base', TEMPLATE_BASE);

			this.$el.html(baseTemplate({
				cid: this.cid,
				shareLabel: t('core', 'Share'),
				sharePlaceholder: this._renderSharePlaceholderPart(),
				remoteShareInfo: this._renderRemoteShareInfoPart(),
				isSharingAllowed: this.model.sharePermissionPossible(),
				isLinkSharingAllowed: this.configModel.isShareWithLinkAllowed(),
				localSharesLabel: t('core', 'User and Groups'),
				publicSharesLabel: t('core', 'Public Links'),
				noSharingPlaceholder: t('core', 'Resharing is not allowed')
			}));

			var $shareField = this.$el.find('.shareWithField');
			if ($shareField.length) {
				$shareField.autocomplete({
					minLength: 1,
					delay: 750,
					focus: function(event) {
						event.preventDefault();
					},
					source: this.autocompleteHandler,
					select: this._onSelectRecipient
				}).data('ui-autocomplete')._renderItem = _.bind(this.autocompleteRenderItem, this);
			}

			this.resharerInfoView.$el = this.$el.find('.resharerInfoView');
			this.resharerInfoView.render();

			var resharingAllowed = this.model.sharePermissionPossible();
			if (!resharingAllowed || !this.configModel.isShareWithLinkAllowed()) {
				this.$('.tabHeaders, .linkShareView').remove();
			}

			this.shareeListView.$el = this.$el.find('.shareeListView');
			this.shareeListView.render();

			this.$el.find('.hasTooltip').tooltip();

			return this;
		},

		_renderRemoteShareInfoPart: function() {
			var remoteShareInfo = '';
			if(this.configModel.get('isRemoteShareAllowed')) {
				var infoTemplate = this._getRemoteShareInfoTemplate();
				remoteShareInfo = infoTemplate({
					docLink: this.configModel.getFederatedShareDocLink(),
					tooltip: t('core', 'Share with people on other ownClouds using the syntax username@example.com/owncloud')
				});
			}

			return remoteShareInfo;
		},

		_renderSharePlaceholderPart: function () {
			var sharePlaceholder = t('core', 'Share with users…');

			if (this.configModel.get('allowGroupSharing')) {
				if (this.configModel.get('isRemoteShareAllowed')) {
					sharePlaceholder = t('core', 'Share with users, groups or remote users…');
				} else {
					sharePlaceholder = t('core', 'Share with users or groups…')
				}
			} else if (this.configModel.get('isRemoteShareAllowed')) {
					sharePlaceholder = t('core', 'Share with users or remote users…');
			}

			return sharePlaceholder;
		},

		/**
		 *
		 * @param {string} key - an identifier for the template
		 * @param {string} template - the HTML to be compiled by Handlebars
		 * @returns {Function} from Handlebars
		 * @private
		 */
		_getTemplate: function (key, template) {
			if (!this._templates[key]) {
				this._templates[key] = Handlebars.compile(template);
			}
			return this._templates[key];
		},

		/**
		 * returns the info template for remote sharing
		 *
		 * @returns {Function}
		 * @private
		 */
		_getRemoteShareInfoTemplate: function() {
			return this._getTemplate('remoteShareInfo', TEMPLATE_REMOTE_SHARE_INFO);
		},

		/**
		 * Returns the autocomplete item template
		 *
		 * @returns {Function}
		 * @private
		 */
		_getAutocompleteItemTemplate: function() {
			return this._getTemplate('autocompleteItem', TEMPLATE_AUTOCOMPLETE_ITEM);
		}
	});

	OC.Share.ShareDialogView = ShareDialogView;

})();
