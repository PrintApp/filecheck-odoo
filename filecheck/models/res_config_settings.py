from odoo import _, fields, models


class ResConfigSettings(models.TransientModel):
    _inherit = 'res.config.settings'

    filecheck_publishable_key = fields.Char(
        related='website_id.filecheck_publishable_key', readonly=False)
    filecheck_secret_key = fields.Char(
        related='website_id.filecheck_secret_key', readonly=False)
    filecheck_agent_id = fields.Char(
        related='website_id.filecheck_agent_id', readonly=False)
    filecheck_default_workflow_id = fields.Many2one(
        related='website_id.filecheck_default_workflow_id', readonly=False)
    filecheck_presentation = fields.Selection(
        related='website_id.filecheck_presentation', readonly=False)
    filecheck_artwork_timing = fields.Selection(
        related='website_id.filecheck_artwork_timing', readonly=False)
    filecheck_upload_email = fields.Boolean(
        related='website_id.filecheck_upload_email', readonly=False)
    filecheck_save_files = fields.Boolean(
        related='website_id.filecheck_save_files', readonly=False)

    def action_filecheck_sync(self):
        """Test the keys and refresh the workflow / connector lists."""
        self.ensure_one()
        counts = self.website_id._filecheck_check_keys()
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'type': 'success',
                'title': _("Connected to Filecheck"),
                'message': _("%(workflows)s workflows and %(connectors)s connectors synced.",
                             workflows=counts.get('workflow', 0),
                             connectors=counts.get('connector', 0)),
                # Reload so the default-workflow dropdown shows the new list.
                'next': {'type': 'ir.actions.client', 'tag': 'reload'},
            },
        }
