from odoo import fields, models


class FilecheckResource(models.Model):
    """Local copy of the account's workflows and connectors, so products and
    settings pick them from a normal dropdown. Refreshed from the API by
    Settings > Filecheck > Sync; the Filecheck dashboard stays the source of
    truth. Records that disappear upstream are archived, never deleted, so
    products pointing at them keep their value."""
    _name = 'filecheck.resource'
    _description = 'Filecheck Workflow or Connector'
    _order = 'kind, name, id'

    name = fields.Char(required=True)
    filecheck_id = fields.Char(string="Filecheck ID", required=True, index=True)
    kind = fields.Selection(
        [('workflow', 'Workflow'), ('connector', 'Connector')],
        required=True, index=True)
    website_id = fields.Many2one(
        'website', required=True, ondelete='cascade', index=True)
    active = fields.Boolean(default=True)

    _sql_constraints = [
        ('filecheck_resource_unique', 'unique(website_id, kind, filecheck_id)',
         'A Filecheck workflow or connector is listed once per website.'),
    ]

    def _compute_display_name(self):
        multi = len(self.env['website'].sudo().search(
            [('filecheck_secret_key', '!=', False)])) > 1
        for record in self:
            record.display_name = (
                f'{record.name} ({record.website_id.name})' if multi else record.name)
