import logging
from datetime import timedelta

from odoo import _, api, fields, models, modules

_logger = logging.getLogger(__name__)

BACKOFF_MINUTES = [5, 15, 60, 360]
MAX_ATTEMPTS = 8


class FilecheckSync(models.Model):
    """Retried delivery of POST /orders/{id}: sends a confirmed (or
    cancelled) order to Filecheck, which links its jobs and opens the artwork
    upload page for lines bought without a file."""
    _name = 'filecheck.sync'
    _description = 'Filecheck Order Sync Queue'
    _order = 'id desc'

    order_id = fields.Many2one(
        'sale.order', required=True, ondelete='cascade', index=True)
    state = fields.Selection(
        [('pending', 'Pending'), ('done', 'Done'), ('failed', 'Failed')],
        default='pending', required=True, index=True)
    attempts = fields.Integer(default=0)
    last_error = fields.Text()
    next_attempt = fields.Datetime(default=fields.Datetime.now)

    def _process(self):
        for record in self:
            if record.state != 'pending':
                continue
            try:
                with self.env.cr.savepoint():
                    record.order_id._filecheck_push()
                if record.state == 'pending':  # nothing to send for this order
                    record.write({'state': 'done', 'last_error': False})
                continue
            except Exception as exc:  # noqa: BLE001 - retried below
                error = str(exc) or repr(exc)

            attempts = record.attempts + 1
            backoff = BACKOFF_MINUTES[min(attempts, len(BACKOFF_MINUTES)) - 1]
            failed = attempts >= MAX_ATTEMPTS
            record.write({
                'attempts': attempts,
                'last_error': error,
                'state': 'failed' if failed else 'pending',
                'next_attempt': fields.Datetime.now() + timedelta(minutes=backoff),
            })
            if failed:
                record.order_id.message_post(
                    body=_("Filecheck: the order could not be sent after %(n)s attempts "
                           "(%(error)s). Use Sync with Filecheck on the Filecheck tab to retry.",
                           n=attempts, error=error),
                    message_type='notification', subtype_xmlid='mail.mt_note')
            _logger.warning("Filecheck: order sync attempt %s failed for %s: %s",
                            attempts, record.order_id.name, error)

    # ------------------------------------------------------------------

    @api.model
    def _filecheck_trigger(self):
        """Run the cron as soon as a worker is free (no wait in the request)."""
        cron = self.env.ref('filecheck.ir_cron_filecheck_sync', raise_if_not_found=False)
        if cron:
            cron.sudo()._trigger()

    @api.model
    def _filecheck_commit(self):
        """Keep each order's progress (and its downloaded files) when a later
        one fails or the cron is killed. Never inside tests."""
        if not getattr(modules.module, 'current_test', None) and self.env.context.get('filecheck_cron'):
            self.env.cr.commit()

    @api.model
    def _cron_run(self):
        self = self.with_context(filecheck_cron=True)
        now = fields.Datetime.now()
        # 1. Orders to send.
        for record in self.search([('state', '=', 'pending'), ('next_attempt', '<=', now)],
                                  limit=50, order='id'):
            record._process()
            self._filecheck_commit()
        # 2. Artwork customers uploaded after checkout.
        self.env['sale.order']._filecheck_poll_awaiting()
        # 3. Print files to save on their orders.
        self.env['sale.order.line']._filecheck_collect_due()
