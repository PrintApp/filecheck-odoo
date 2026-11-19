from odoo import fields, models

from ..const import PRESENTATIONS, TIMINGS


class ProductTemplate(models.Model):
    _inherit = 'product.template'

    filecheck_mode = fields.Selection(
        [('off', 'Off'),
         ('default', "Website's default workflow"),
         ('custom', 'A specific workflow')],
        string="Filecheck", default='off', required=True,
        help="Ask customers for their print file on this product, checked by "
             "a Filecheck workflow.")
    filecheck_workflow_id = fields.Many2one(
        'filecheck.resource', string="Filecheck Workflow",
        domain="[('kind', '=', 'workflow')]", ondelete='set null')
    filecheck_connector_id = fields.Many2one(
        'filecheck.resource', string="Filecheck Connector",
        domain="[('kind', '=', 'connector')]", ondelete='set null',
        help="Optional: fill quantity or size inputs on the product page from "
             "the uploaded file (page count, dimensions).")
    filecheck_presentation = fields.Selection(
        PRESENTATIONS, string="Filecheck Presentation",
        help="Leave empty to use the website setting.")
    filecheck_artwork_timing = fields.Selection(
        TIMINGS, string="Collect Artwork",
        help="When the customer provides the file. Leave empty to use the "
             "website setting.")

    def _filecheck_config(self, website):
        """The resolved Filecheck settings of this product on `website`, or
        False when the product does not collect files there."""
        self.ensure_one()
        if self.filecheck_mode == 'off' or not website:
            return False
        if self.filecheck_mode == 'custom':
            workflow = self.filecheck_workflow_id
        else:
            workflow = website.filecheck_default_workflow_id
        if not workflow:
            return False
        return {
            'workflow_id': workflow.filecheck_id,
            'connector_id': self.filecheck_connector_id.filecheck_id or '',
            'presentation': self.filecheck_presentation or website.filecheck_presentation or 'inline',
            'timing': self.filecheck_artwork_timing or website.filecheck_artwork_timing or 'checkout',
        }
