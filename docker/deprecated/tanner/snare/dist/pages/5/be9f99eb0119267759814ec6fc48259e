/*
 * Copyright (c) 2015
 *
 * This file is licensed under the Affero General Public License version 3
 * or later.
 *
 * See the COPYING-README file.
 *
 */

/* global moment */

(function() {
	if (!OC.Share) {
		OC.Share = {};
	}

	var TEMPLATE =
		'<div id="linkPass-{{cid}}" class="public-link-modal--item">' +
			'<label class="public-link-modal--label" for="expirationDate-{{cid}}" value="{{expirationDate}}">{{expirationLabel}} {{#if isExpirationEnforced}}<span class="required-indicator">*</span>{{/if}}</label>' +
			'<input class="public-link-modal--input datepicker expirationDate" id="linkPassText-{{cid}}" type="text" placeholder="{{expirationDatePlaceholder}}" value="{{expirationValue}}" />' +
			'<span class="error-message hidden"></span>' +
			'{{#if isExpirationEnforced}}' +
			// originally the expire message was shown when a default date was set, however it never had text
			'<em id="defaultExpireMessage" class="defaultExpireMessage">{{defaultExpireMessage}}</em>' +
			'{{/if}}' +
		'</div>'
		;

	/**
	 * @class OCA.Share.ShareDialogExpirationView
	 * @member {OC.Share.ShareItemModel} model
	 * @member {jQuery} $el
	 * @memberof OCA.Sharing
	 * @classdesc
	 *
	 * Represents the expiration part in the GUI of the share dialogue
	 *
	 */
	var ShareDialogExpirationView = OC.Backbone.View.extend({
		/** @type {string} **/
		id: 'shareDialogExpirationView',

		/** @type {OC.Share.ShareConfigModel} **/
		configModel: undefined,

		/** @type {Function} **/
		_template: undefined,

		className: 'shareDialogExpirationView',

		events: {
			'change .expirationCheckbox': '_onToggleExpiration',
			'change .datepicker': '_onChangeExpirationDate'
		},

		initialize: function(options) {
			if(!_.isUndefined(options.itemModel)) {
				this.itemModel = options.itemModel;
				this.configModel = this.itemModel.configModel;
			} else {
				throw 'missing OC.Share.ShareItemModel';
			}

			var view = this;
			this.configModel.on('change:isDefaultExpireDateEnforced', function() {
				view.render();
			});
		},

		_onToggleExpiration: function(event) {
			var $checkbox = $(event.target);
			var state = $checkbox.prop('checked');
			// TODO: slide animation
			this.$el.find('.expirationDateContainer').toggleClass('hidden', !state);
			if (!state) {
				// discard expiration date
				this.model.unset('expireDate');
			}
		},

		_onChangeExpirationDate: function(event) {
			var $target = $(event.target);
			$target.tooltip('hide');
			$target.removeClass('error');

			var expiration = moment($target.val(), 'DD-MM-YYYY').format('YYYY-MM-DD');
			this.model.set('expireDate', expiration);
		},

		render: function() {
			var defaultExpireMessage = '';
			var defaultExpireDays = this.configModel.get('defaultExpireDate');
			var isExpirationEnforced = this.configModel.get('isDefaultExpireDateEnforced');

			if(    (this.itemModel.isFolder() || this.itemModel.isFile())
				&& isExpirationEnforced) {
				defaultExpireMessage = t(
					'core',
					'The public link will expire no later than {days} days after it is created',
					{'days': defaultExpireDays }
				);
			}

			var isExpirationSet = !!this.model.get('expireDate') || isExpirationEnforced;

			var expiration;
			if (isExpirationSet) {
				expiration = moment(this.model.get('expireDate'), 'YYYY-MM-DD').format('DD-MM-YYYY');
			}

			this.$el.html(this.template({
				cid: this.cid,
				setExpirationLabel: t('core', 'Set expiration date'),
				expirationLabel: t('core', 'Expiration'),
				expirationDatePlaceholder: t('core', 'Choose an expiration date'),
				defaultExpireMessage: defaultExpireMessage,
				isExpirationSet: isExpirationSet,
				isExpirationEnforced: isExpirationEnforced,
				disableCheckbox: isExpirationEnforced && isExpirationSet,
				expirationValue: expiration
			}));

			// what if there is another date picker on that page?
			var minDate = new Date();
			var maxDate = null;
			// min date should always be the next day
			minDate.setDate(minDate.getDate()+1);

			if(isExpirationSet) {
				if(isExpirationEnforced) {
					// TODO: hack: backend returns string instead of integer
					var shareTime = this.model.get('stime');
					if (_.isNumber(shareTime)) {
						shareTime = new Date(shareTime * 1000);
					}
					if (!shareTime) {
						shareTime = new Date(); // now
					}
					shareTime = OC.Util.stripTime(shareTime).getTime();
					maxDate = new Date(shareTime + defaultExpireDays * 24 * 3600 * 1000);
				}
			}
			$.datepicker.setDefaults({
				minDate: minDate,
				maxDate: maxDate
			});

			this.$('.datepicker').datepicker({dateFormat : 'dd-mm-yy'});

			this.$field = this.$('.expirationDate');

			this.delegateEvents();

			return this;
		},

		getValue: function() {
			return this.$field.val().trim();
		},

		validate: function() {
			this.$field.removeClass('error');
			this.$field.next('.error-message').addClass('hidden');
			if (this.configModel.get('isDefaultExpireDateEnforced') && !this.getValue()) {
				this.$field.addClass('error');
				this.$field.next('.error-message').removeClass('hidden').text(t('core', 'Expiration date is required'));
				return false;
			}
			return true;
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

	OC.Share.ShareDialogExpirationView = ShareDialogExpirationView;

})();
