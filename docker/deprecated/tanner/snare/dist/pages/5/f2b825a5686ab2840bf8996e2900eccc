/*
 * Copyright (c) 2017
 *
 * This file is licensed under the Affero General Public License version 3
 * or later.
 *
 * See the COPYING-README file.
 *
 */

(function() {
	/**
	 * @class OC.Share.ShareModel
	 * @classdesc
	 *
	 * Represents a single share.
	 */
	var ShareModel = OC.Backbone.Model.extend({

		url: function() {
			var params = {
				format: 'json'
			};
			var idPart = '';
			// note: unsaved new models have no ids at first but need a POST URL
			if (!_.isUndefined(this.id)) {
				idPart = '/' + encodeURIComponent(this.id);
			}
			return OC.linkToOCS('apps/files_sharing/api/v1', 2) +
				'shares' +
				idPart +
				'?' + OC.buildQueryString(params);
		},

		parse: function(data) {
			/* jshint camelcase: false */
			if (data.ocs && data.ocs.data) {
				// parse out of the ocs response
				data = data.ocs.data;
			}

			// parse the non-camel to camel
			if (data.share_type === OC.Share.SHARE_TYPE_LINK) {
				// store password in a separate attribute as we don't
				// want to re-save it like this
				data.encryptedPassword = data.share_with;
				delete data.share_with;
				delete data.file_target;
				delete data.mail_send;
				delete data.share_with_displayname;
			}

			data.itemSource = data.item_source;
			delete data.item_source;
			data.itemType = data.item_type;
			delete data.item_type;
			data.shareType = data.share_type;
			delete data.share_type;

			// convert the inconsistency... (read as expiration, saved as expireDate...)
			if (data.expiration && !data.expireDate) {
				data.expireDate = data.expiration;
			}
			if (_.isUndefined(data.expireDate)) {
				data.expireDate = null;
			}

			// remove unrelated/unneeded attributes,
			delete data.expiration;
			delete data.url;

			// these can be read from the parent object or fileinfo
			delete data.displayname_file_owner;
			delete data.displayname_owner;
			delete data.file_parent;
			delete data.path;
			delete data.storage;
			delete data.storage_id;
			delete data.uid_file_owner;
			delete data.uid_owner;
			delete data.mimetype;
			delete data.parent;

			return data;
		},

		canRead: function() {
			return (this.get('permissions') & OC.PERMISSION_READ) > 0;
		},

		canUpdate: function() {
			return (this.get('permissions') & OC.PERMISSION_UPDATE) > 0;
		},

		canDelete: function() {
			return (this.get('permissions') & OC.PERMISSION_DELETE) > 0;
		},

		canCreate: function() {
			return (this.get('permissions') & OC.PERMISSION_CREATE) > 0;
		},

		canShare: function() {
			return (this.get('permissions') & OC.PERMISSION_SHARE) > 0;
		},

		/**
		 * Returns the absolute link share
		 *
		 * @return {String} link
		 */
		getLink: function() {
			var base = OC.getProtocol() + '://' + OC.getHost();
			return base + OC.generateUrl('/s/') + this.get('token');
		}
	});

	OC.Share = OC.Share || {};
	OC.Share.ShareModel = ShareModel;
})();
