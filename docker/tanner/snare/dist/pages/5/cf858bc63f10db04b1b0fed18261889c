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

	var PASSWORD_PLACEHOLDER_STARS = '**********';
	var PASSWORD_PLACEHOLDER_MESSAGE = t('core', 'Choose a password');
	var TEMPLATE =
		'<div class="error-message-global hidden"></div>' +
		'<div class="public-link-modal">'+
			'<div class="public-link-modal--item">' +
				'<label class="public-link-modal--label">{{linkNameLabel}}</label>' +
				'<input class="public-link-modal--input" type="text" name="linkName" placeholder="{{namePlaceholder}}" value="{{name}}" maxlength="64" />' +
			'</div>' +
			'<div id="allowPublicRead-{{cid}}" class="public-link-modal--item">' +
				'<input type="radio" value="{{publicReadValue}}" name="publicPermissions" id="sharingDialogAllowPublicRead-{{cid}}" class="checkbox publicPermissions" {{#if publicReadSelected}}checked{{/if}} />' +
				'<label class="bold" for="sharingDialogAllowPublicRead-{{cid}}">{{publicReadLabel}}</label>' +
				'<p><em>{{publicReadDescription}}</em></p>' +
			'</div>' +
			'{{#if publicUploadPossible}}' +
			'<div id="allowPublicReadWrite-{{cid}}" class="public-link-modal--item">' +
				'<input type="radio" value="{{publicReadWriteValue}}" name="publicPermissions" id="sharingDialogAllowPublicReadWrite-{{cid}}" class="checkbox publicPermissions" {{#if publicReadWriteSelected}}checked{{/if}} />' +
				'<label class="bold" for="sharingDialogAllowPublicReadWrite-{{cid}}">{{publicReadWriteLabel}}</label>' +
				'<p><em>{{publicReadWriteDescription}}</em></p>' +
			'</div>' +
			'<div id="allowPublicUploadWrapper-{{cid}}" class="public-link-modal--item">' +
				'<input type="radio" value="{{publicUploadValue}}" name="publicPermissions" id="sharingDialogAllowPublicUpload-{{cid}}" class="checkbox publicPermissions" {{#if publicUploadSelected}}checked{{/if}} />' +
				'<label class="bold" for="sharingDialogAllowPublicUpload-{{cid}}">{{publicUploadLabel}}</label>' +
				'<p><em>{{publicUploadDescription}}</em></p>' +
			'</div>' +
			'{{/if}}' +
			'<div id="linkPass-{{cid}}" class="public-link-modal--item linkPass">' +
				'<label class="public-link-modal--label" for="linkPassText-{{cid}}">{{passwordLabel}}</label>' +
				'<input class="public-link-modal--input linkPassText" id="linkPassText-{{cid}}" type="password" placeholder="{{passwordPlaceholder}}" />' +
				'<span class="error-message hidden"></span>' +
			'</div>' +
			'<div class="expirationDateContainer"></div>' +
			'{{#if isMailEnabled}}' +
			'<div class="mailView"></div>' +
			'{{/if}}' +
		'</div>'
	;

	/**
	 * @class OCA.Share.ShareDialogLinkShareView
	 * @member {OC.Share.ShareItemModel} model
	 * @member {jQuery} $el
	 * @memberof OCA.Sharing
	 * @classdesc
	 *
	 * Represents the GUI of the share dialogue
	 *
	 */
	var ShareDialogLinkShareView = OC.Backbone.View.extend({
		/** @type {string} **/
		id: 'shareDialogLinkShare',

		className: 'shareDialogLinkShare',

		/** @type {OC.Share.ShareConfigModel} **/
		configModel: undefined,

		/** @type {OC.Share.ShareItemModel} **/
		itemModel: undefined,

		/** @type {Function} **/
		_template: undefined,

		initialize: function (options) {
			if (!_.isUndefined(options.itemModel)) {
				this.itemModel = options.itemModel;
				this.configModel = this.itemModel.configModel;
			} else {
				throw 'missing OC.Share.ShareItemModel';
			}

			this.expirationView = new OC.Share.ShareDialogExpirationView({
				model: this.model,
				itemModel: this.itemModel
			});

			OC.Plugins.attach('OCA.Share.ShareDialogLinkShareView', this);
		},

		/**
		 * Returns the selected permissions as read from the checkboxes or
		 * the absence thereof.
		 *
		 * @return {int} permissions
		 */
		_getPermissions: function() {
			var permissions = this.$('input[name="publicPermissions"]:checked').val();

			return (permissions) ? parseInt(permissions, 10) : OC.PERMISSION_READ;
		},

		_shouldRequirePassword: function() {
			// matching passwordMustBeEnforced from server side
			var permissions = this._getPermissions();
			var roEnforcement = permissions === OC.PERMISSION_READ && this.configModel.get('enforceLinkPasswordReadOnly');
			var woEnforcement = permissions === OC.PERMISSION_CREATE && this.configModel.get('enforceLinkPasswordWriteOnly');
			var rwEnforcement = (permissions !== OC.PERMISSION_READ && permissions !== OC.PERMISSION_CREATE) && this.configModel.get('enforceLinkPasswordReadWrite');
			if (roEnforcement || woEnforcement || rwEnforcement) {
				return true;
			} else {
				return false;
			}
		},

		_save: function () {
			var deferred = $.Deferred();
			var $el = this.$el;

			var $password = $el.find('.linkPassText'),
				$inputs = $el.find('.linkPassText, .expirationDate, .permission'), // all input fields combined
				$errorMessageGlobal = $el.find('.error-message-global'),
				$loading = $el.find('.loading'),
				password = $password.val(),
				expirationDate = this.expirationView.getValue();

			$el.find('.error-message').addClass('hidden');

			// remove errors (if present)
			// ***

			$inputs.removeClass('error');
			$errorMessageGlobal.addClass('hidden');

			// explicit attributes to be saved
			var attributes = {
				expireDate: expirationDate,
				permissions: this._getPermissions(),
				name: this.$('[name=linkName]').val(),
				shareType: this.model.get('shareType')
			};

			// Reset password for the time being
			if (this.model.get("resetPassword")) {
				attributes.password = '';
			}

			if (password) {
				// only set password explicitly if changed
				attributes.password = password;
			}

			var validates = true;
			validates &= this.expirationView.validate();

			if (!password
				&& this._shouldRequirePassword()
				&& (this.model.isNew() || !this.model.get('encryptedPassword'))
			) {
				$password.addClass('error');
				$password.next('.error-message').removeClass('hidden').text(t('core', 'Password required'));
				validates = false;
			}

			if (!validates) {
				deferred.reject(this.model);
			}

			if (this.model.isNew()) {
				// the API is inconsistent
				attributes.path = this.itemModel.getFileInfo().getFullPath();
			}

			var self = this;

			var done = function() {
				$loading.addClass('hidden');
				deferred.resolve(self.model);
				self.trigger('saved', self.model);
			};

			$loading.removeClass('hidden');

			// save it
			// ***
			this.model.save(attributes, {
				// explicit attributes for patch-like PUT to avoid
				// passing all attributes
				attrs: attributes,
				success: function() {
					if (self.mailView) {
						// also send out email first
						// do not resolve on errors
						self.mailView.sendEmails().then(done);
					} else {
						done();
					}
					self.model.unset("resetPassword");
				},
				error: function (model, xhr) {
					var msg = xhr.responseJSON.ocs.meta.message;
					// destroy old tooltips
					$loading.addClass('hidden');
					$errorMessageGlobal.removeClass('hidden').text(msg);
					deferred.reject(self.model);
				}
			});

			return deferred.promise();
		},

		_remove: function () {
			this.model.destroy();
		},

		_isPublicUploadPossible: function() {
			// TODO: in the future to read directly from the FileInfoModel
			return this.itemModel.isFolder() && this.itemModel.createPermissionPossible() && this.configModel.isPublicUploadEnabled();
		},

		render: function () {
			var isPasswordSet = !!this.model.get('encryptedPassword');

			// only show email field for new shares and if enabled globally
			var showEmailField = this.configModel.isMailPublicNotificationEnabled();

			this.$el.html(this.template({
				cid: this.cid,
				passwordPlaceholder: isPasswordSet ? PASSWORD_PLACEHOLDER_STARS : PASSWORD_PLACEHOLDER_MESSAGE,
				linkNameLabel: t('core', 'Link name'),
				namePlaceholder: t('core', 'Name'),
				name: this.model.get('name'),
				isPasswordSet: isPasswordSet,

				fileNameLabel              : t('core', 'Filename'),
				passwordLabel              : t('core', 'Password'),

				publicUploadPossible       : this._isPublicUploadPossible(),

				publicUploadLabel          : t('core', 'Upload only') + ' (File Drop)',
				publicUploadDescription    : t('core', 'Receive files from multiple recipients without revealing the contents of the folder.'),
				publicUploadValue          : OC.PERMISSION_CREATE,
				publicUploadSelected       : this.model.get('permissions') === OC.PERMISSION_CREATE,

				publicReadLabel            : t('core', 'Download / View'),
				publicReadDescription      : t('core', 'Recipients can view or download contents.'),
				publicReadValue            : OC.PERMISSION_READ,
				publicReadSelected         : this.model.get('permissions') === OC.PERMISSION_READ,

				publicReadWriteLabel       : t('core', 'Download / View / Upload'),
				publicReadWriteDescription : t('core', 'Recipients can view, download, edit, delete and upload contents.'),
				publicReadWriteValue       : OC.PERMISSION_READ | OC.PERMISSION_UPDATE | OC.PERMISSION_CREATE | OC.PERMISSION_DELETE,
				publicReadWriteSelected    : this.model.get('permissions') >= (OC.PERMISSION_READ | OC.PERMISSION_UPDATE | OC.PERMISSION_CREATE | OC.PERMISSION_DELETE),

				isMailEnabled: showEmailField
			}));

			this.$('.datepicker').datepicker({dateFormat : 'dd-mm-yy'});

			if (showEmailField) {
				this.mailView = new OC.Share.ShareDialogMailView({
					itemModel: this.itemModel,
					configModel: this.configModel,
					model: this.model
				});
				this.mailView.render();
				this.$('.mailView').append(this.mailView.$el);
			} else {
				this.mailView = null;
			}

			this.expirationView.render();
			this.$('.expirationDateContainer').append(this.expirationView.$el);

			this.delegateEvents();

			return this;
		},

		_onClickReset: function () {
			var $dialog              = $('.oc-dialog:visible'),
				$inputPassword       = $dialog.find('.linkPassText'),
				$buttonReset         = $dialog.find('.removePassword');

			this.model.set("resetPassword", true);

			$inputPassword
				.val('')
				.attr('placeholder', PASSWORD_PLACEHOLDER_MESSAGE);

			$buttonReset.remove();
		},

		_onClickSave: function() {
			var self = this;
			this._save().then(function() {
				self.$dialog.ocdialog('close');
			});
		},

		_onClickCancel: function() {
			this.$dialog.ocdialog('close');
			this.model.unset("resetPassword");
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
		},

		/**
		 * Display this view inside a popup window
		 */
		show: function() {
			var self = this;
			var title = t('core', 'Edit link share: {name}', {name: this.itemModel.getFileInfo().getFullPath()});
			var buttons = [{
				text: t('core', 'Cancel'),
				click: _.bind(this._onClickCancel, this)
			}];

			if (this.model.isNew()) {
				title = t('core', 'Create link share: {name}', {name: this.itemModel.getFileInfo().getFullPath()});
				buttons.push({
					text: t('core', 'Share'),
					click: _.bind(this._onClickSave, this),
					defaultButton: true
				})
			}
			else {
				buttons.push({
					text: t('core', 'Save'),
					click: _.bind(this._onClickSave, this),
					defaultButton: true
				})
			}

			if (this.model.get('encryptedPassword')) {
				buttons.push({
					classes: 'removePassword',
					text: t('core', 'Remove password'),
					click: _.bind(this._onClickReset, this),
					defaultButton: false
				});
			}

			// hack the dialogs
			OC.dialogs.message(
				'',
				title,
				'custom',
				buttons,
				null,
				true,
				'public-link-modal'
			).then(function adjustDialog() {
				var $dialogShell = $('.oc-dialog:visible');
				self.render();
				self.$dialog = $dialogShell.find('.oc-dialog-content');
				self.$dialog.html(self.$el);
				self.$el.find('input:first').focus();
			});
		}

});

	OC.Share.ShareDialogLinkShareView = ShareDialogLinkShareView;

})();
