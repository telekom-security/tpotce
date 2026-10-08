/*
 * Copyright (c) 2016
 *
 * This file is licensed under the Affero General Public License version 3
 * or later.
 *
 * See the COPYING-README file.
 *
 */

(function() {
	if (!OC.Share) {
		OC.Share = {};
	}

	var TEMPLATE =
		'<form id="emailPrivateLink" class="emailPrivateLinkForm">' +
		'  <span class="emailPrivateLinkForm--sending-indicator hidden">{{sending}}</span>' +
		'  <span class="emailPrivateLinkForm--sent-indicator hidden">{{sent}}</span>' +
		'  <label class="public-link-modal--label" for="emailPrivateLinkField-{{cid}}">{{mailLabel}}</label>' +
		'  <input class="emailPrivateLinkForm--emailField full-width" id="emailPrivateLinkField-{{cid}}" />' +
		'  <div class="emailPrivateLinkForm--elements hidden">' +
		'    {{#if userHasEmail}}' +
		'    <label class="public-link-modal--bccSelf">' +
		'      <input class="emailPrivateLinkForm--emailBccSelf" type="checkbox"> {{bccSelf}}' +
		'    </label>' +
		'    {{/if}}' +
		'    <label class="public-link-modal--label" for="emailBodyPrivateLinkField-{{cid}}">{{mailMessageLabel}}</label>' +
		'    <textarea class="public-link-modal--input emailPrivateLinkForm--emailBodyField" id="emailBodyPrivateLinkField-{{cid}}" rows="3" placeholder="{{mailBodyPlaceholder}}"></textarea>' +
		'  </div>' +
		'</form>';

	/**
	 * @class OCA.Share.ShareDialogMailView
	 * @member {OC.Share.ShareItemModel} model
	 * @member {jQuery} $el
	 * @memberof OCA.Sharing
	 * @classdesc
	 *
	 * Represents the GUI of the share dialogue
	 *
	 */
	var ShareDialogMailView = OC.Backbone.View.extend({
		/** @type {string} **/
		id: 'shareDialogMailView',

		events: {
			"keydown .emailPrivateLinkForm--emailBodyField" : "expandMailBody"
		},

		/** @type {array} **/
		_addresses: [],

		/** @type {Function} **/
		_template: undefined,

		initialize: function(options) {
			_.bindAll(this, 'render', '_afterRender');
			var _this = this;
			this.render = _.wrap(this.render, function(render) {
				render();
				_this._afterRender();
				return _this;
			});

			if (!_.isUndefined(options.itemModel)) {
				this.itemModel = options.itemModel;
			} else {
				throw 'missing OC.Share.ShareItemModel';
			}
		},

		toggleMailElements: function() {
			var $emailElements = this.$el.find('.emailPrivateLinkForm--elements');

			if (this._addresses.length > 0 && $emailElements.is(":hidden")) {
				$emailElements.slideDown();
			} else if (this._addresses.length === 0 && $emailElements.is(":visible")) {
				$emailElements.slideUp();
			}
		},

		expandMailBody: function(event) {
			var $emailBody = this.$el.find('.emailPrivateLinkForm--emailBodyField');
			$emailBody.css('minHeight', $emailBody[0].scrollHeight - 12);

			if (event.keyCode == 13) {
				event.stopPropagation();
			}
		},

		/**
		 * Send the link share information by email
		 *
		 * @param {string} recipientEmail recipient email address
		 */
		_sendEmailPrivateLink: function(mail) {
			var deferred           = $.Deferred();
			var itemType           = this.itemModel.get('itemType');
			var itemSource         = this.itemModel.get('itemSource');
			var $formSentIndicator = this.$el.find('.emailPrivateLinkForm--sent-indicator');

			var params = {
				action      : 'email',
				toAddress   : this._addresses.join(','),
				emailBody   : mail.body,
				bccSelf     : mail.bccSelf,
				link        : this.model.getLink(),
				itemType    : itemType,
				itemSource  : itemSource,
				file        : this.itemModel.getFileInfo().get('name'),
				expiration  : this.model.get('expireDate') || ''
			};

			$.post(
				OC.generateUrl('core/ajax/share.php'), params,
				function(result) {
					if (!result || result.status !== 'success') {
						deferred.reject({
							message: result.data.message
						});
					} else {
						$formSentIndicator.removeClass('hidden');
						setTimeout(function() {
							deferred.resolve();
							$formSentIndicator.addClass('hidden');
						}, 2000);
					}
			}).fail(function(error) {
				return deferred.reject(error);
			});

			return deferred.promise();
		},

		validateEmail: function(email) {
			if (email.length === 0)
				return true

			return email.match(/([\w\.\-_]+)?\w+@[\w-_]+(\.\w+){1,}$/);
		},

		sendEmails: function() {
			var $formItems         = this.$el.find('.emailPrivateLinkForm input, .emailPrivateLinkForm textarea');
			var $formSendIndicator = this.$el.find('.emailPrivateLinkForm--sending-indicator');
			var  mail = {
				 to      : this._addresses.join(','),
				 bccSelf : this.$el.find('.emailPrivateLinkForm--emailBccSelf').is(':checked'),
				 body    : this.$el.find('.emailPrivateLinkForm--emailBodyField').val()
			};

			var deferred = $.Deferred();

			if (mail.to !== '') {
				$formItems.prop('disabled', true);
				$formSendIndicator.removeClass('hidden');
				this._sendEmailPrivateLink(mail).done(function() {
					$formItems.prop('disabled', false);
					$formSendIndicator.addClass('hidden');
					deferred.resolve();
				}).fail(function(error) {
					OC.dialogs.info(error.message, t('core', 'An error occured while sending email'));
					$formSendIndicator.addClass('hidden');
					$formItems.prop('disabled', false);
					deferred.reject();
				});
			} else {
				deferred.resolve();
			}

			return deferred.promise();
		},

		render: function() {
			// make sure this is empty
			this._addresses = [];

			this.$el.html(this.template({
				cid                 : this.cid,
				userHasEmail        : !!OC.getCurrentUser().email,
				mailPlaceholder     : t('core', 'Email link to person'),
				bccSelf             : t('core', 'Send copy to self'),
				mailLabel           : t('core', 'Send link via email'),
				mailBodyPlaceholder : t('core', 'Add personal message'),
				sending             : t('core', 'Sending') + ' ...',
				sent                : t('core', 'E-Mail sent') + '!'
			}));

			this.delegateEvents();
			return this;

		},

		_afterRender: function () {
			var _this = this;

			this.$el.find('.emailPrivateLinkForm--emailField').select2({
				containerCssClass: 'emailPrivateLinkForm--dropDown',
				tags: true,
				tokenSeparators:[","],
				xhr: null,
				query: function(query) {
					// directly from search
					var data = [{
						"id": query.term,
						"text" : query.term,
						"disabled" : !_this.validateEmail(query.term)
					}];

					// return query data ASAP
					query.callback({results: data});

					if (query.term.length >= OC.getCapabilities().files_sharing.search_min_length) {
						if (this.xhr != null)
							this.xhr.abort();

						var xhr = $.get(OC.generateUrl('core/ajax/share.php'), {
							'fetch' : 'getShareWithEmail',
							'search': query.term
						}).done(function(result) {
							// enrich with share results
							ajaxData  = _.map(result.data, function(item) {
								return {
									'id'   : item.email,
									'text' : item.displayname + ' (' + item.email + ')'
								}
							});

							query.callback({results: data.concat(ajaxData)});
						})
						this.xhr = xhr;
					}
				}
			}).on("change", function(e) {
				if (e.added)
					_this._addAddress(e.added.id);

				if (e.removed)
					_this._removeAddress(e.removed.id);

				_this.toggleMailElements();
			});
		},

		_addAddress: function( email ) {
			this._addresses.push( email.toLowerCase() )
		},

		_removeAddress: function( email ) {
			this._addresses = _.without(this._addresses, email.toLowerCase() )
		},

		/**
		 * @returns {Function} from Handlebars
		 * @private
		 */
		template: function(data) {
			if (!this._template) {
				this._template = Handlebars.compile(TEMPLATE);
			}
			return this._template(data);
		}

	});

	OC.Share.ShareDialogMailView = ShareDialogMailView;

})();
