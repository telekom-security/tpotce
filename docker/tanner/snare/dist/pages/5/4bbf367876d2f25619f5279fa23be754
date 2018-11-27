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
	 * @class OC.Share.SharesCollection
	 * @classdesc
	 *
	 * Represents a collection of shares
	 */
	var SharesCollection = OC.Backbone.Collection.extend({
		model: OC.Share.ShareModel,

		url: function() {
			var params = {
				format: 'json'
			};
			return OC.linkToOCS('apps/files_sharing/api/v1/shares', 2) + '?' + OC.buildQueryString(params);
		}
	});

	OC.Share = OC.Share || {};
	OC.Share.SharesCollection = SharesCollection;
})();
