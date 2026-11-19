import json

from odoo.tests import HttpCase, tagged

from .common import JOB_A, JOB_B, FilecheckCase


@tagged('post_install', '-at_install')
class TestStorefront(FilecheckCase, HttpCase):

    def rpc(self, url, **params):
        res = self.url_open(url, data=json.dumps({
            'jsonrpc': '2.0', 'method': 'call', 'id': 1, 'params': params,
        }), headers={'Content-Type': 'application/json'})
        self.assertEqual(res.status_code, 200)
        return res.json()

    def cart(self):
        return self.env['sale.order'].sudo().search(
            [('website_id', '=', self.website.id)], order='id desc', limit=1)

    def add(self, product, qty=1):
        return self.rpc('/shop/cart/update_json', product_id=product.id, add_qty=qty)

    def test_product_page_renders_widget(self):
        res = self.url_open(self.cards.product_tmpl_id.website_url)
        self.assertEqual(res.status_code, 200)
        self.assertIn('id="fc-slot-%s"' % self.cards.product_tmpl_id.id, res.text)
        self.assertIn('https://cdn.filecheck.io/element/pk_01jf8n2q4vxw9k3m5p7r9t1v3x/filecheck.js', res.text)
        self.assertIn('"workflowId": "wf_cards"', res.text)
        self.assertNotIn('sk_0123456789abcdef', res.text)

        res = self.url_open(self.mug.product_tmpl_id.website_url)
        self.assertNotIn('filecheck-params', res.text)

    def test_save_job_validation(self):
        tmpl = self.cards.product_tmpl_id.id
        self.assertFalse(self.rpc('/filecheck/save_job', product_id=tmpl, job_id='nope')['result']['success'])
        self.assertFalse(self.rpc('/filecheck/save_job', product_id=self.mug.product_tmpl_id.id,
                                  job_id=JOB_A, ready=True)['result']['success'])
        self.assertTrue(self.rpc('/filecheck/save_job', product_id=tmpl, job_id=JOB_A,
                                 ready=True, files=['card.pdf'])['result']['success'])

    def test_checked_file_follows_the_cart_line(self):
        tmpl = self.cards.product_tmpl_id.id
        # Uploaded but not (yet) passing: the job is not attached.
        self.rpc('/filecheck/save_job', product_id=tmpl, job_id=JOB_A, ready=False)
        self.rpc('/filecheck/save_job', product_id=tmpl, job_id=JOB_A, ready=True, files=['card.pdf'])
        res = self.add(self.cards)
        self.assertNotIn('error', res)
        line = self.cart().order_line.filtered(lambda l: l.product_id == self.cards)
        self.assertEqual(line.filecheck_job_id, JOB_A)
        self.assertEqual(line.filecheck_files, ['card.pdf'])
        self.assertIn('Artwork: card.pdf', line.name)

        # Same file again: same line. Another file: a line of its own.
        self.add(self.cards)
        self.assertEqual(line.product_uom_qty, 2)
        self.rpc('/filecheck/save_job', product_id=tmpl, job_id=JOB_B, ready=True, files=['other.pdf'])
        self.add(self.cards)
        lines = self.cart().order_line.filtered(lambda l: l.product_id == self.cards)
        self.assertEqual(sorted(lines.mapped('filecheck_job_id')), sorted([JOB_A, JOB_B]))

    def test_not_ready_job_is_not_attached(self):
        tmpl = self.cards.product_tmpl_id.id
        self.rpc('/filecheck/save_job', product_id=tmpl, job_id=JOB_A, ready=False)
        self.assertNotIn('error', self.add(self.cards))
        self.assertFalse(self.cart().order_line.filecheck_job_id)

    def test_without_a_file_artwork_is_collected_after_checkout(self):
        # The element fails open (and the shop grid adds without the page),
        # so the cart never refuses: the line waits for artwork instead.
        for product in (self.cards, self.poster, self.mug):
            self.assertNotIn('error', self.add(product))
        order = self.cart()
        self.assertFalse(any(order.order_line.mapped('filecheck_job_id')))
        order.action_confirm()
        _payload, awaiting = order._filecheck_payload()
        self.assertEqual(awaiting.mapped('product_id'), self.cards | self.poster)
