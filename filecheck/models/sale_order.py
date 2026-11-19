import logging
from datetime import timedelta

from odoo import _, api, fields, models
from odoo.exceptions import UserError
from odoo.http import request

from ..const import ADMIN_ORDER, ORDER_STATUS, SESSION_KEY
from ..lib.client import FilecheckError

_logger = logging.getLogger(__name__)

# Orders still waiting for artwork are re-asked this often for new uploads,
# for this long after the order date (the upload page itself expires too).
POLL_INTERVAL = timedelta(minutes=30)
POLL_WINDOW = timedelta(days=60)


class SaleOrder(models.Model):
    _inherit = 'sale.order'

    filecheck_synced = fields.Boolean(string="Sent to Filecheck", copy=False)
    filecheck_awaits_artwork = fields.Boolean(string="Awaits Artwork", copy=False)
    filecheck_upload_url = fields.Char(string="Artwork Upload Link", copy=False)
    filecheck_upload_status = fields.Char(
        string="Artwork Upload Status", copy=False,
        help="awaiting, partial or complete: how many lines have their file.")
    filecheck_upload_email_sent = fields.Boolean(copy=False)
    filecheck_next_poll = fields.Datetime(copy=False)
    filecheck_has_lines = fields.Boolean(compute='_compute_filecheck_has_lines')

    @api.depends('order_line.filecheck_job_id', 'order_line.filecheck_awaits_artwork')
    def _compute_filecheck_has_lines(self):
        for order in self:
            order.filecheck_has_lines = any(
                line.filecheck_job_id or line.filecheck_awaits_artwork
                for line in order.order_line)

    # ------------------------------------------------------------------
    # Cart: the checked job from the product page goes onto its own line
    # ------------------------------------------------------------------

    def _filecheck_pending(self, product_id):
        """The ready job the customer uploaded for this product, if any."""
        if not request or not getattr(request, 'session', None):
            return None
        try:
            product = self.env['product.product'].browse(int(product_id)).exists()
        except (TypeError, ValueError):
            return None
        if not product:
            return None
        entry = (request.session.get(SESSION_KEY) or {}).get(str(product.product_tmpl_id.id))
        if isinstance(entry, dict) and entry.get('ready') and entry.get('jobId'):
            return entry
        return None

    def _cart_find_product_line(self, product_id, line_id=None, **kwargs):
        lines = super()._cart_find_product_line(product_id, line_id=line_id, **kwargs)
        if line_id:
            return lines
        # One line per uploaded file: a line only absorbs an add-to-cart that
        # carries the same job (or, for plain lines, no job at all).
        pending = self._filecheck_pending(product_id)
        job_id = pending['jobId'] if pending else False
        return lines.filtered(lambda line: (line.filecheck_job_id or False) == job_id)

    def _cart_update(self, product_id, line_id=None, add_qty=0, set_qty=0, **kwargs):
        # No server-side refusal here: the Filecheck element fails open (a store
        # must always be able to take orders), and a line bought without a
        # checked file is collected after checkout instead (see
        # _filecheck_await_workflow).
        values = super()._cart_update(
            product_id, line_id=line_id, add_qty=add_qty, set_qty=set_qty, **kwargs)
        try:
            if values.get('quantity') and values.get('line_id'):
                line = self.env['sale.order.line'].browse(values['line_id']).exists()
                pending = line and not line.filecheck_job_id and self._filecheck_pending(product_id)
                if pending:
                    files = [f for f in (pending.get('files') or []) if isinstance(f, str)]
                    vals = {'filecheck_job_id': pending['jobId'], 'filecheck_files': files}
                    if files:
                        vals['name'] = '%s\n%s' % (
                            line.name, _("Artwork: %s", ', '.join(files)))
                    line.write(vals)
        except Exception:
            _logger.exception("Filecheck: failed to attach the job to the cart line")
        return values

    # ------------------------------------------------------------------
    # Order lifecycle
    # ------------------------------------------------------------------

    def action_confirm(self):
        res = super().action_confirm()
        try:
            self._filecheck_enqueue()
        except Exception:
            _logger.exception("Filecheck: failed to queue the order sync")
        return res

    def _action_cancel(self):
        res = super()._action_cancel()
        try:
            self.filtered('filecheck_synced')._filecheck_enqueue()
        except Exception:
            _logger.exception("Filecheck: failed to queue the cancellation sync")
        return res

    def _filecheck_enqueue(self):
        """Queue a (re)send of these orders; the cron delivers it with retries."""
        Sync = self.env['filecheck.sync'].sudo()
        queued = False
        for order in self:
            if not order.filecheck_synced and not order._filecheck_line_items()[0]:
                continue
            if not Sync.search_count([('order_id', '=', order.id), ('state', '=', 'pending')]):
                Sync.create({'order_id': order.id})
            queued = True
        if queued:
            Sync._filecheck_trigger()

    # ------------------------------------------------------------------
    # Sync with Filecheck (POST /orders/{id})
    # ------------------------------------------------------------------

    def _filecheck_website(self):
        self.ensure_one()
        return self.env['website'].sudo()._filecheck_for_order(self)

    def _filecheck_collects_later(self):
        self.ensure_one()
        return self.state in ('sale', 'done')

    def _filecheck_await_workflow(self, line, website):
        """The workflow a line bought without a file is collected on after
        checkout, or '' (it has a file, or its product has no Filecheck
        workflow). Products that collect at checkout are included too: such a
        line exists only when the upload could not run (the element failed
        open, the shop grid's quick add, an order keyed in by a sales rep),
        and the customer then uploads it on the order's upload page."""
        if line.filecheck_job_id or not line._filecheck_is_product_line():
            return ''
        config = line.product_id.product_tmpl_id.sudo()._filecheck_config(website)
        return config['workflow_id'] if config else ''

    def _filecheck_line_items(self, website=None):
        """(items payload, lines awaiting artwork) for this order."""
        self.ensure_one()
        website = website or self._filecheck_website()
        if not website:
            return [], self.env['sale.order.line']
        collect_later = self._filecheck_collects_later()
        items, awaiting, printapp = [], self.env['sale.order.line'], []
        for line in self.order_line:
            if not line._filecheck_is_product_line():
                continue
            item = {
                'itemId': str(line.id),
                'productId': str(line.product_id.product_tmpl_id.id),
                'name': line.product_id.display_name,
                'quantity': line.product_uom_qty,
                'sku': line.product_id.default_code or '',
                'total': line.price_total,
                'jobId': line.filecheck_job_id or None,
            }
            if not line.filecheck_job_id:
                workflow = collect_later and self._filecheck_await_workflow(line, website)
                if workflow:
                    item.update(awaitArtwork=True, workflowId=workflow)
                    awaiting |= line
                else:
                    # Lines designed in Print.App ride along, so the Filecheck
                    # order page lists the whole order. Filecheck builds the
                    # file link from the (validated) project id.
                    project = line['printapp_project_id'] if 'printapp_project_id' in line._fields else False
                    if project:
                        item['printapp'] = {'projectId': project}
                        printapp.append(item)
                    continue
            items.append(item)
        if items:
            items.extend(printapp)
        return items, awaiting

    def _filecheck_payload(self, website=None):
        self.ensure_one()
        items, awaiting = self._filecheck_line_items(website)
        if not items:
            return None, awaiting
        partner = self.partner_id
        shipping = self.partner_shipping_id or partner
        payload = {
            'orderId': str(self.id),
            # What the customer sees on their emails and types on the upload page.
            'orderName': self.name,
            'status': ORDER_STATUS.get(self.state, self.state),
            'currency': self.currency_id.name,
            'total': self.amount_total,
            'customer': {
                'id': str(partner.commercial_partner_id.id),
                'name': partner.name or '',
                'email': partner.email or '',
                'phone': partner.phone or getattr(partner, 'mobile', '') or '',
            },
            'items': items,
        }
        if shipping.street:
            payload['shippingAddress'] = {
                'name': shipping.name or '',
                'address1': shipping.street or '',
                'address2': shipping.street2 or '',
                'city': shipping.city or '',
                'state': shipping.state_id.name or '',
                'postcode': shipping.zip or '',
                'country': shipping.country_id.name or '',
                'company': shipping.commercial_company_name or '',
            }
        return payload, awaiting

    def _filecheck_push(self):
        """Send this order to Filecheck. Returns False when it has nothing for
        Filecheck; raises FilecheckError when the API call fails."""
        self.ensure_one()
        order = self.sudo()
        website = order._filecheck_website()
        if not website:
            return False
        payload, awaiting = order._filecheck_payload(website)
        if not payload:
            return False
        client = website._filecheck_client()
        client.sync_order(order.id, payload)

        vals = {'filecheck_synced': True}
        if awaiting:
            awaiting.write({'filecheck_awaits_artwork': True})
            vals['filecheck_awaits_artwork'] = True
        order.write(vals)
        pending_files = order.order_line.filtered(
            lambda l: l.filecheck_job_id and not l.filecheck_files_state)
        if pending_files and website.filecheck_save_files:
            pending_files.write({'filecheck_files_state': 'pending'})

        if order.filecheck_awaits_artwork and order._filecheck_collects_later():
            order._filecheck_refresh_upload_link(client)
            if order.filecheck_upload_status != 'complete':
                order._filecheck_send_upload_email(website)
        # A successful push answers every queued resend of this order.
        self.env['filecheck.sync'].sudo().search(
            [('order_id', '=', order.id), ('state', '=', 'pending')]
        ).write({'state': 'done', 'last_error': False})
        return True

    def _filecheck_refresh_upload_link(self, client=None):
        """Re-read the upload page record: its link and status, and the jobs
        customers created there, which land on their order lines."""
        self.ensure_one()
        order = self.sudo()
        client = client or order._filecheck_website()._filecheck_client()
        link = client.get_upload_link(order.id)
        vals = {'filecheck_next_poll': fields.Datetime.now() + POLL_INTERVAL}
        if link:
            vals['filecheck_upload_url'] = link['url']
            vals['filecheck_upload_status'] = str(link.get('status') or '')
            by_line = {str(line.id): line for line in order.order_line}
            for item in link.get('items') or []:
                line = by_line.get(str(item.get('itemId')))
                job_id = item.get('jobId')
                if line and job_id and not line.filecheck_job_id:
                    line.write({
                        'filecheck_job_id': str(job_id),
                        'filecheck_awaits_artwork': False,
                        'filecheck_files_state': 'pending'
                        if order._filecheck_website().filecheck_save_files else False,
                    })
        order.write(vals)
        return link

    def _filecheck_send_upload_email(self, website):
        """Email the upload link once per order. The claim is a single UPDATE
        so a cron pass and a page view syncing at once cannot both send it."""
        self.ensure_one()
        if not website.filecheck_upload_email or not self.filecheck_upload_url \
                or not self.partner_id.email:
            return
        self.env.cr.execute(
            "UPDATE sale_order SET filecheck_upload_email_sent = TRUE "
            "WHERE id = %s AND filecheck_upload_email_sent IS NOT TRUE RETURNING id",
            (self.id,))
        if not self.env.cr.fetchone():
            return
        self.invalidate_recordset(['filecheck_upload_email_sent'])
        template = self.env.ref('filecheck.mail_template_upload_artwork', raise_if_not_found=False)
        if not template:
            return
        template.sudo().send_mail(self.id, email_layout_xmlid='mail.mail_notification_light')
        self.sudo().message_post(
            body=_("Filecheck: the artwork upload link was emailed to %s.", self.partner_id.email),
            message_type='notification', subtype_xmlid='mail.mt_note')

    def _filecheck_upload_link_for_display(self):
        """Upload link for the confirmation and portal order pages, or ''. An
        order that waits for artwork but has not been sent to Filecheck yet
        (the cron has not run since payment) is sent now, once."""
        self.ensure_one()
        order = self.sudo()
        if not order._filecheck_collects_later():
            return ''
        if not order.filecheck_synced and not order.filecheck_awaits_artwork:
            if not order._filecheck_line_items()[1]:
                return ''
            try:
                with self.env.cr.savepoint():
                    order._filecheck_push()
            except Exception as exc:  # noqa: BLE001 - never break the customer's page
                _logger.warning("Filecheck: sync for %s failed: %s", order.name, exc)
                return ''
        if order.filecheck_awaits_artwork and order.filecheck_upload_url \
                and order.filecheck_upload_status != 'complete':
            return order.filecheck_upload_url
        return ''

    # ------------------------------------------------------------------
    # Backend
    # ------------------------------------------------------------------

    def action_filecheck_sync(self):
        """'Sync with Filecheck' button: send the order now and fetch its files."""
        self.ensure_one()
        try:
            pushed = self._filecheck_push()
            if self.filecheck_awaits_artwork and self.filecheck_synced:
                self._filecheck_refresh_upload_link()
            self.order_line.filtered(
                lambda l: l.filecheck_files_state in ('pending', 'error')
            )._filecheck_collect_files(force=True)
        except FilecheckError as exc:
            raise UserError(_("Filecheck: %s", exc)) from exc
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'type': 'success' if pushed else 'info',
                'message': _("Order synced with Filecheck.") if pushed
                else _("Nothing on this order is handled by Filecheck."),
                'next': {'type': 'ir.actions.client', 'tag': 'soft_reload'},
            },
        }

    def filecheck_order_details(self):
        """Data for the order form's Filecheck panel: per line, the job's
        files with live (short-lived) links, and the files saved on the order."""
        self.ensure_one()
        order = self.sudo()
        website = order._filecheck_website()
        result = {
            'uploadUrl': order.filecheck_upload_url or '',
            'uploadStatus': order.filecheck_upload_status or '',
            'error': '' if website else _("No website has Filecheck keys configured."),
            'lines': [],
        }
        client = None
        if website:
            try:
                client = website._filecheck_client()
            except FilecheckError as exc:
                result['error'] = str(exc)
        for line in order.order_line.filtered(
                lambda l: l.filecheck_job_id or l.filecheck_awaits_artwork):
            entry = {
                'id': line.id,
                'name': line.name_short if 'name_short' in line._fields else line.product_id.display_name,
                'jobId': line.filecheck_job_id or '',
                'awaiting': bool(line.filecheck_awaits_artwork and not line.filecheck_job_id),
                'filesState': line.filecheck_files_state or '',
                'filesError': line.filecheck_files_error or '',
                'adminUrl': ADMIN_ORDER.format(order=order.id, job=line.filecheck_job_id)
                if line.filecheck_job_id else '',
                'saved': [{'name': a.name, 'url': f'/web/content/{a.id}?download=true'}
                          for a in line.filecheck_attachment_ids],
                'status': '', 'outcome': '', 'files': [], 'error': '',
            }
            if line.filecheck_job_id and client:
                try:
                    job = client.get_job(line.filecheck_job_id)
                    entry['status'] = job.get('status') or ''
                    entry['outcome'] = job.get('outcome') or ''
                    for run in job.get('runs') or []:
                        entry['files'].append({
                            'name': run.get('name') or run.get('id') or '',
                            'status': run.get('status') or '',
                            'outcome': run.get('outcome') or '',
                            'downloadUrl': run.get('downloadUrl') or '',
                            'proofs': [p['url'] for p in (run.get('proofs') or [])
                                       if isinstance(p, dict) and p.get('url')][:6],
                        })
                except FilecheckError as exc:
                    entry['error'] = str(exc)
            result['lines'].append(entry)
        return result

    # ------------------------------------------------------------------
    # Cron
    # ------------------------------------------------------------------

    @api.model
    def _filecheck_poll_awaiting(self, limit=20):
        """Orders still waiting for artwork: pick up the files customers have
        uploaded since on the Filecheck upload page."""
        now = fields.Datetime.now()
        orders = self.sudo().search([
            ('filecheck_awaits_artwork', '=', True),
            ('filecheck_upload_status', '!=', 'complete'),
            ('state', 'in', ('sale', 'done')),
            ('date_order', '>=', now - POLL_WINDOW),
            '|', ('filecheck_next_poll', '=', False), ('filecheck_next_poll', '<=', now),
        ], limit=limit, order='id')
        for order in orders:
            try:
                with self.env.cr.savepoint():
                    order._filecheck_refresh_upload_link()
            except Exception as exc:
                _logger.warning("Filecheck: upload link refresh for %s failed: %s", order.name, exc)
                order.filecheck_next_poll = now + POLL_INTERVAL
            self.env['filecheck.sync']._filecheck_commit()
