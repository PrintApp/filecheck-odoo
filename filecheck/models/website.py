import json
import logging

from markupsafe import Markup

from odoo import _, fields, models
from odoo.exceptions import UserError
from odoo.http import request

from ..const import (
    API_BASE, API_URL_PARAM, CART_BUTTON_SELECTOR, CDN_ELEMENT, PRESENTATIONS,
    SESSION_KEY, TIMINGS,
)
from ..lib.client import FilecheckClient, FilecheckError

_logger = logging.getLogger(__name__)


class Website(models.Model):
    _inherit = 'website'

    filecheck_publishable_key = fields.Char(
        string="Filecheck Publishable Key",
        help="pk_... key from the Filecheck dashboard. Loaded on product pages.")
    filecheck_secret_key = fields.Char(
        string="Filecheck Secret Key",
        groups='base.group_system',
        help="sk_... key from the Filecheck dashboard, for server-to-server "
             "calls. Never sent to the storefront.")
    filecheck_agent_id = fields.Char(
        string="Filecheck Agent ID",
        help="Optional agt_... id when one Filecheck account powers several stores.")
    filecheck_default_workflow_id = fields.Many2one(
        'filecheck.resource', string="Filecheck Default Workflow",
        domain="[('website_id', '=', id), ('kind', '=', 'workflow')]",
        ondelete='set null')
    filecheck_presentation = fields.Selection(
        PRESENTATIONS, string="Filecheck Presentation", default='inline')
    filecheck_artwork_timing = fields.Selection(
        TIMINGS, string="Filecheck Artwork Timing", default='checkout')
    filecheck_upload_email = fields.Boolean(
        string="Email the Artwork Upload Link", default=True,
        help="When an order waits for artwork, email the customer the link "
             "to upload it (template: Filecheck: Upload your artwork).")
    filecheck_save_files = fields.Boolean(
        string="Save Print Files on Orders", default=True,
        help="Download each checked (and fixed) file and attach it to the "
             "sale order, so it stays available after Filecheck's links expire.")

    # ------------------------------------------------------------------
    # API access
    # ------------------------------------------------------------------

    def _filecheck_api_base(self):
        return (self.env['ir.config_parameter'].sudo().get_param(API_URL_PARAM)
                or API_BASE)

    def _filecheck_client(self):
        """A client for this website's Filecheck account. Raises FilecheckError
        when no secret key is set."""
        self.ensure_one()
        return FilecheckClient(self.sudo().filecheck_secret_key, self._filecheck_api_base())

    def _filecheck_is_configured(self):
        self.ensure_one()
        site = self.sudo()
        return bool(site.filecheck_publishable_key and site.filecheck_secret_key)

    def _filecheck_for_order(self, order):
        """The website whose account an order belongs to: its own, else (orders
        keyed in by a sales rep) the first website with Filecheck keys."""
        site = order.website_id.sudo()
        if site and site.filecheck_secret_key:
            return site
        return self.sudo().search([('filecheck_secret_key', '!=', False)], limit=1)

    def _filecheck_sync_library(self):
        """Refresh the cached workflows and connectors. Returns {kind: count}."""
        self.ensure_one()
        client = self._filecheck_client()
        Resource = self.env['filecheck.resource'].sudo().with_context(active_test=False)
        counts = {}
        for kind, items in (('workflow', client.list_workflows()),
                            ('connector', client.list_connectors())):
            existing = {r.filecheck_id: r for r in Resource.search(
                [('website_id', '=', self.id), ('kind', '=', kind)])}
            seen = set()
            for item in items:
                fid = str(item['id'])
                seen.add(fid)
                name = item.get('title') or item.get('name') or fid
                if fid in existing:
                    existing[fid].write({'name': name, 'active': True})
                else:
                    Resource.create({'name': name, 'filecheck_id': fid,
                                     'kind': kind, 'website_id': self.id})
            for fid, record in existing.items():
                if fid not in seen and record.active:
                    record.active = False
            counts[kind] = len(seen)
        return counts

    def write(self, vals):
        res = super().write(vals)
        if vals.get('filecheck_secret_key') and not self.env.context.get('filecheck_skip_sync'):
            for site in self:
                try:
                    site._filecheck_sync_library()
                except FilecheckError as exc:
                    _logger.info("Filecheck: library sync after key change failed: %s", exc)
        return res

    # ------------------------------------------------------------------
    # Storefront
    # ------------------------------------------------------------------

    def _filecheck_product_params(self, product_tmpl):
        """Everything the storefront script needs on a product page, or False
        when Filecheck is off for this product. `json` is Markup-wrapped JSON
        ready for a <script type="application/json"> block."""
        self.ensure_one()
        site = self.sudo()
        if not request or not product_tmpl or not site.filecheck_publishable_key:
            return False
        config = product_tmpl.sudo()._filecheck_config(site)
        if not config:
            return False

        pending = (request.session.get(SESSION_KEY) or {}).get(str(product_tmpl.id)) or {}
        lang = (request.env.lang or 'en_US').replace('_', '-')
        params = {
            'publishableKey': site.filecheck_publishable_key,
            'agentId': site.filecheck_agent_id or None,
            'workflowId': config['workflow_id'],
            'connectorId': config['connector_id'] or None,
            'presentation': config['presentation'],
            'artworkTiming': config['timing'],
            'locale': lang,
            'productId': product_tmpl.id,
            # Resume the customer's upload after a page reload.
            'jobId': pending.get('jobId') or None,
            'cartButtonSelector': CART_BUTTON_SELECTOR,
            'endpoint': '/filecheck/save_job',
        }
        return {
            # <-escape so a value can never close the script element early
            'json': Markup(json.dumps(params).replace('<', '\\u003c')),
            'cdn_url': CDN_ELEMENT.format(pk=site.filecheck_publishable_key),
        }

    def _filecheck_check_keys(self):
        """Settings button: verify the secret key and refresh the library."""
        self.ensure_one()
        if not self.sudo().filecheck_secret_key:
            raise UserError(_("Enter the Filecheck secret key first."))
        try:
            return self._filecheck_sync_library()
        except FilecheckError as exc:
            raise UserError(str(exc)) from exc
