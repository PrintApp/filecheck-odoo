from contextlib import contextmanager
from unittest.mock import MagicMock, patch

from odoo import fields
from odoo.tests import tagged

from ..lib.client import FilecheckError
from .common import JOB_A, JOB_B, FilecheckCase, job_runs

CLIENT = 'odoo.addons.filecheck.models.website.FilecheckClient'
UPLOAD_URL = 'https://app.filecheck.io/o/abc?t=signed'


@contextmanager
def api(**methods):
    """Patch the Filecheck client; yields the mock instance."""
    instance = MagicMock()
    instance.sync_order.return_value = {'success': True}
    instance.get_upload_link.return_value = None
    for name, value in methods.items():
        target = getattr(instance, name)
        if isinstance(value, Exception) or callable(value) and not isinstance(value, dict):
            target.side_effect = value
        else:
            target.return_value = value
    with patch(CLIENT, return_value=instance):
        yield instance


@tagged('post_install', '-at_install')
class TestOrderSync(FilecheckCase):

    def test_payload_lines(self):
        order = self.make_order()
        payload, awaiting = order._filecheck_payload()
        items = {i['productId']: i for i in payload['items']}

        cards = items[str(self.cards.product_tmpl_id.id)]
        self.assertEqual(cards['jobId'], JOB_A)
        self.assertEqual(cards['quantity'], 2)
        self.assertEqual(cards['sku'], 'CARDS')
        self.assertNotIn('awaitArtwork', cards)

        poster = items[str(self.poster.product_tmpl_id.id)]
        self.assertIsNone(poster['jobId'])
        self.assertTrue(poster['awaitArtwork'])
        self.assertEqual(poster['workflowId'], 'wf_posters')
        self.assertEqual(awaiting, self.line_of(order, self.poster))

        # A product without Filecheck stays out of the payload.
        self.assertNotIn(str(self.mug.product_tmpl_id.id), items)
        self.assertEqual(payload['orderId'], str(order.id))
        self.assertEqual(payload['orderName'], order.name)
        self.assertEqual(payload['status'], 'processing')
        self.assertEqual(payload['customer']['email'], 'ada@example.com')
        self.assertEqual(payload['shippingAddress']['address1'], '1 Print Street')

    def test_quotation_does_not_await_artwork(self):
        order = self.make_order(confirm=False)
        payload, awaiting = order._filecheck_payload()
        self.assertFalse(awaiting)
        self.assertEqual([i['jobId'] for i in payload['items']], [JOB_A])

    def test_nothing_for_filecheck(self):
        order = self.env['sale.order'].create({
            'partner_id': self.partner.id, 'website_id': self.website.id,
            'order_line': [(0, 0, {'product_id': self.mug.id})]})
        order.action_confirm()
        self.assertFalse(self.env['filecheck.sync'].search([('order_id', '=', order.id)]))
        with api() as client:
            self.assertFalse(order._filecheck_push())
            client.sync_order.assert_not_called()

    def test_confirm_queues_and_cron_delivers(self):
        order = self.make_order()
        queued = self.env['filecheck.sync'].search([('order_id', '=', order.id)])
        self.assertEqual(queued.state, 'pending')

        link = {'url': UPLOAD_URL, 'status': 'awaiting',
                'items': [{'itemId': str(self.line_of(order, self.poster).id), 'jobId': None}]}
        with api(get_upload_link=link, get_job=job_runs(status='running')) as client:
            self.env['filecheck.sync']._cron_run()
            client.sync_order.assert_called_once()
            self.assertEqual(client.sync_order.call_args.args[0], order.id)

        self.assertEqual(queued.state, 'done')
        self.assertTrue(order.filecheck_synced)
        self.assertTrue(order.filecheck_awaits_artwork)
        self.assertEqual(order.filecheck_upload_url, UPLOAD_URL)
        self.assertTrue(self.line_of(order, self.poster).filecheck_awaits_artwork)
        self.assertEqual(self.line_of(order, self.cards).filecheck_files_state, 'pending')

        # The upload link is emailed exactly once.
        mails = self.env['mail.mail'].search([('model', '=', 'sale.order'), ('res_id', '=', order.id),
                                              ('subject', 'ilike', 'Upload your artwork')])
        self.assertEqual(len(mails), 1)
        self.assertIn(UPLOAD_URL, mails.body_html)
        with api(get_upload_link=link):
            order._filecheck_push()
        self.assertEqual(len(self.env['mail.mail'].search([
            ('model', '=', 'sale.order'), ('res_id', '=', order.id),
            ('subject', 'ilike', 'Upload your artwork')])), 1)

    def test_upload_email_can_be_turned_off(self):
        self.website.filecheck_upload_email = False
        order = self.make_order()
        with api(get_upload_link={'url': UPLOAD_URL, 'status': 'awaiting', 'items': []}):
            order._filecheck_push()
        self.assertFalse(order.filecheck_upload_email_sent)

    def test_retry_with_backoff(self):
        order = self.make_order()
        queued = self.env['filecheck.sync'].search([('order_id', '=', order.id)])
        with api(sync_order=FilecheckError('Filecheck returned HTTP 502', status=502)):
            queued._process()
        self.assertEqual(queued.state, 'pending')
        self.assertEqual(queued.attempts, 1)
        self.assertIn('502', queued.last_error)
        self.assertGreater(queued.next_attempt, fields.Datetime.now())
        self.assertFalse(order.filecheck_synced)

        queued.attempts = 7
        with api(sync_order=FilecheckError('down')):
            queued._process()
        self.assertEqual(queued.state, 'failed')

    def test_post_order_upload_lands_on_line(self):
        order = self.make_order()
        poster_line = self.line_of(order, self.poster)
        with api(get_upload_link={'url': UPLOAD_URL, 'status': 'awaiting', 'items': []}):
            order._filecheck_push()
        link = {'url': UPLOAD_URL, 'status': 'complete',
                'items': [{'itemId': str(poster_line.id), 'jobId': JOB_B}]}
        order.filecheck_next_poll = False
        with api(get_upload_link=link):
            self.env['sale.order']._filecheck_poll_awaiting()
        self.assertEqual(poster_line.filecheck_job_id, JOB_B)
        self.assertFalse(poster_line.filecheck_awaits_artwork)
        self.assertEqual(poster_line.filecheck_files_state, 'pending')
        self.assertEqual(order.filecheck_upload_status, 'complete')
        self.assertFalse(order._filecheck_upload_link_for_display())

    def test_print_files_saved_on_order(self):
        order = self.make_order()
        with api(get_upload_link=None):
            order._filecheck_push()
        line = self.line_of(order, self.cards)

        with api(get_job=job_runs(status='running')):
            self.env['sale.order.line']._filecheck_collect_due()
        self.assertEqual(line.filecheck_files_state, 'pending')
        self.assertFalse(line.filecheck_attachment_ids)
        self.assertTrue(line.filecheck_files_next_try)

        line.filecheck_files_next_try = False
        with api(get_job=job_runs(), download=b'%PDF-1.7 fixed') as client:
            self.env['sale.order.line']._filecheck_collect_due()
            client.download.assert_called_once()
            self.assertEqual(client.download.call_args.args[0], 'https://files.example/fixed.pdf?sig=1')
        self.assertEqual(line.filecheck_files_state, 'done')
        attachment = line.filecheck_attachment_ids
        self.assertEqual(len(attachment), 1)
        self.assertEqual((attachment.res_model, attachment.res_id), ('sale.order', order.id))
        self.assertEqual(attachment.raw, b'%PDF-1.7 fixed')
        self.assertTrue(attachment.name.endswith('business-card.pdf'))

        # Forced re-collect does not duplicate the saved run.
        with api(get_job=job_runs(), download=b'again') as client:
            line._filecheck_collect_files(force=True)
            client.download.assert_not_called()

    def test_cancel_resyncs_synced_orders(self):
        order = self.make_order()
        with api(get_upload_link=None):
            order._filecheck_push()
        order._action_cancel()
        queued = self.env['filecheck.sync'].search(
            [('order_id', '=', order.id), ('state', '=', 'pending')])
        self.assertEqual(len(queued), 1)
        payload, _awaiting = order._filecheck_payload()
        self.assertEqual(payload['status'], 'cancelled')
        self.assertFalse(any(i.get('awaitArtwork') for i in payload['items']))

    def test_order_panel_details(self):
        order = self.make_order()
        with api(get_job=job_runs(), get_upload_link={'url': UPLOAD_URL, 'status': 'awaiting', 'items': []}):
            order._filecheck_push()
            details = order.filecheck_order_details()
        self.assertEqual(details['uploadUrl'], UPLOAD_URL)
        lines = {l['jobId']: l for l in details['lines']}
        self.assertEqual(lines[JOB_A]['outcome'], 'pass')
        self.assertEqual(lines[JOB_A]['files'][0]['downloadUrl'], 'https://files.example/fixed.pdf?sig=1')
        self.assertIn(JOB_A, lines[JOB_A]['adminUrl'])
        self.assertTrue(lines['']['awaiting'])

    def test_library_sync_archives_missing(self):
        with api(list_workflows=[{'id': 'wf_cards', 'title': 'Cards v2'}, {'id': 'wf_new', 'title': 'New'}],
                 list_connectors=[{'id': 'cntr_1', 'name': 'Qty from pages'}]):
            counts = self.website._filecheck_sync_library()
        self.assertEqual(counts, {'workflow': 2, 'connector': 1})
        self.assertEqual(self.workflow.name, 'Cards v2')
        self.assertFalse(self.workflow_later.active)  # gone upstream: archived, not deleted
        self.assertTrue(self.workflow_later.exists())
