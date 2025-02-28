/**
 * @package     Joomla.Site
 * @subpackage  Templates.protostar
 * @copyright   Copyright (C) 2005 - 2018 Open Source Matters, Inc. All rights reserved.
 * @license     GNU General Public License version 2 or later; see LICENSE.txt
 * @since       3.2
 */

jQuery(function($) {
	"use strict";

	$(document)
		.on('click', ".btn-group label:not(.active)", function() {
			var $label = $(this);
			var $input = $('#' + $label.attr('for'));

			if ($input.prop('checked')) {
				return;
			}

			$label.closest('.btn-group').find("label").removeClass('active btn-success btn-danger btn-primary');

			var btnClass = 'primary';


			if ($input.val() != '')
			{
				var reversed = $label.closest('.btn-group').hasClass('btn-group-reversed');
				btnClass = ($input.val() == 0 ? !reversed : reversed) ? 'danger' : 'success';
			}

			$label.addClass('active btn-' + btnClass);
			$input.prop('checked', true).trigger('change');
		})
		.on('click', '#back-top', function (e) {
			e.preventDefault();
			$("html, body").animate({scrollTop: 0}, 1000);
		})
		.on('subform-row-add', initButtonGroup)
		.on('subform-row-add', initTooltip);

	initButtonGroup();
	initTooltip();

	// Called once on domready, again when a subform row is added
	function initTooltip(event, container)
	{
		$(container || document).find('*[rel=tooltip]').tooltip();
	}

	// Called once on domready, again when a subform row is added
	function initButtonGroup(event, container)
	{
		var $container = $(container || document);

		// Turn radios into btn-group
		$container.find('.radio.btn-group label').addClass('btn');

		$container.find(".btn-group input:checked").each(function()
		{
			var $input  = $(this);
			var $label = $('label[for=' + $input.attr('id') + ']');
			var btnClass = 'primary';

			if ($input.val() != '')
			{
				var reversed = $input.parent().hasClass('btn-group-reversed');
				btnClass = ($input.val() == 0 ? !reversed : reversed) ? 'danger' : 'success';
			}

			$label.addClass('active btn-' + btnClass);
		});
	}
});
