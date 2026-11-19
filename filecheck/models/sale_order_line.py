import logging
import mimetypes
import os
import re
from datetime import timedelta

from odoo import _, api, fields, models
from odoo.fields import Command

from ..lib.client import FilecheckError, is_terminal

_logger = logging.getLogger(__name__)

# ir.config_parameter: largest print file saved on an order, in MB.
MAX_MB_PARAM = 'filecheck.max_file_mb'
DEFAULT_MAX_MB = 512
FILES_BACKOFF_MINUTES = [5, 10, 30, 60, 180, 360]
FILES_MAX_ATTEMPTS = 24


class SaleOrderLine(models.Model):
    _inherit = 'sale.order.line'

    filecheck_job_id = fields.Char(
        string="Filecheck Job", copy=False, index='btree_not_null')
    filecheck_files = fields.Json(
        string="Filecheck Files", copy=False,
        help="File names the customer uploaded, as reported by the storefront.")
    filecheck_awaits_artwork = fields.Boolean(
        string="Awaits Artwork", copy=False,
        help="Bought without a file; the customer uploads it after checkout.")
    filecheck_files_state = fields.Selection(
        [('pending', 'Waiting for Filecheck'),
         ('done', 'Saved on the order'),
         ('error', 'Could not be saved')],
        string="Print Files", copy=False)
    filecheck_files_attempts = fields.Integer(copy=False)
    filecheck_files_next_try = fields.Datetime(copy=False)
    filecheck_files_error = fields.Text(copy=False)
    filecheck_attachment_ids = fields.Many2many(
        'ir.attachment', 'filecheck_sale_line_attachment_rel', 'line_id', 'attachment_id',
        string="Filecheck Print Files", copy=False)

    def _filecheck_is_product_line(self):
        self.ensure_one()
        return bool(self.product_id) and not self.display_type \
            and not getattr(self, 'is_delivery', False) \
            and not getattr(self, 'is_reward_line', False)

    # ------------------------------------------------------------------
    # Print files: download each job's files onto the order
    # ------------------------------------------------------------------

    @api.model
    def _filecheck_collect_due(self, limit=10):
        now = fields.Datetime.now()
        lines = self.sudo().search([
            ('filecheck_files_state', '=', 'pending'),
            ('filecheck_job_id', '!=', False),
            ('order_id.state', 'in', ('sale', 'done')),
            '|', ('filecheck_files_next_try', '=', False),
            ('filecheck_files_next_try', '<=', now),
        ], limit=limit, order='id')
        for line in lines:
            line._filecheck_collect_files()
            self.env['filecheck.sync']._filecheck_commit()

    def _filecheck_max_bytes(self):
        param = self.env['ir.config_parameter'].sudo().get_param(MAX_MB_PARAM)
        try:
            megabytes = int(param) if param else DEFAULT_MAX_MB
        except ValueError:
            megabytes = DEFAULT_MAX_MB
        return megabytes * (1 << 20)

    def _filecheck_collect_files(self, force=False):
        """Save each run's file (the corrected one, else the upload) as an
        attachment of the sale order, once the job has finished."""
        for line in self.sudo():
            if not line.filecheck_job_id or (line.filecheck_files_state == 'done' and not force):
                continue
            website = line.order_id._filecheck_website()
            if not website or not website.filecheck_save_files:
                line.filecheck_files_state = False
                continue
            try:
                with self.env.cr.savepoint():
                    finished = line._filecheck_save_runs(website)
                if finished:
                    line.write({'filecheck_files_state': 'done', 'filecheck_files_error': False})
                    continue
                error = False  # still processing: not a failure
            except Exception as exc:  # noqa: BLE001 - retried with backoff
                error = str(exc) or repr(exc)
                _logger.warning("Filecheck: saving files of %s failed: %s",
                                line.filecheck_job_id, error)
            attempts = line.filecheck_files_attempts + 1
            delay = FILES_BACKOFF_MINUTES[min(attempts, len(FILES_BACKOFF_MINUTES)) - 1]
            line.write({
                'filecheck_files_attempts': attempts,
                'filecheck_files_error': error,
                'filecheck_files_state': 'error' if attempts >= FILES_MAX_ATTEMPTS else 'pending',
                'filecheck_files_next_try': fields.Datetime.now() + timedelta(minutes=delay),
            })

    def _filecheck_save_runs(self, website):
        """Returns True when the job is finished and every file it has is
        saved; False while it is still processing."""
        self.ensure_one()
        client = website._filecheck_client()
        job = client.get_job(self.filecheck_job_id)
        if not is_terminal(job):
            return False
        order = self.order_id
        Attachment = self.env['ir.attachment'].sudo()
        saved = {a.description for a in self.filecheck_attachment_ids}
        new = Attachment
        for run in job.get('runs') or []:
            url, run_id = run.get('downloadUrl'), run.get('id')
            tag = f'filecheck:{self.filecheck_job_id}:{run_id}'
            if not url or not run_id or tag in saved:
                continue
            data = client.download(url, self._filecheck_max_bytes())
            filename = _safe_filename(run.get('name') or f'{run_id}.pdf')
            new |= Attachment.create({
                'name': f'{order.name}-{self.id}-{filename}',
                'raw': data,
                'res_model': 'sale.order',
                'res_id': order.id,
                'description': tag,
                'mimetype': mimetypes.guess_type(filename)[0] or 'application/octet-stream',
            })
        if new:
            self.filecheck_attachment_ids = [Command.link(a.id) for a in new]
            outcome = job.get('outcome') or job.get('status')
            order.message_post(
                body=_("Filecheck: print files for %(line)s (%(outcome)s).",
                       line=self.product_id.display_name, outcome=outcome),
                attachment_ids=new.ids,
                message_type='notification', subtype_xmlid='mail.mt_note')
        elif not self.filecheck_attachment_ids and job.get('status') == 'error':
            raise FilecheckError("The Filecheck job ended in error and has no file to save.")
        return True


def _safe_filename(name):
    name = os.path.basename(str(name)).strip() or 'file'
    name = re.sub(r'[^\w.\- ]+', '_', name)
    return name[:120]
