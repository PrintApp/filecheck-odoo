from odoo import Command
from odoo.tests import TransactionCase

JOB_A = 'job_01jf8n2q4vxw9k3m5p7r9t1v3x'
JOB_B = 'job_01jf8n2q4vxw9k3m5p7r9t1v9z'


def job_runs(job_id=JOB_A, status='done', outcome='pass', url='https://files.example/fixed.pdf?sig=1'):
    """Shape of GET /jobs/{id}?expand=runs (fixtures/job.runs.json)."""
    return {
        'id': job_id,
        'status': status,
        'outcome': outcome,
        'runs': [{
            'id': 'task_01jf8n2r5wxy0l4n6q8s0u2w4y',
            'name': 'business-card.pdf',
            'outcome': outcome,
            'status': status,
            'hasOutput': True,
            'proofs': [{'url': 'https://files.example/proof-1.png?sig=2'}],
            'downloadUrl': url if status == 'done' else None,
        }],
    }


class FilecheckCase(TransactionCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.website = cls.env.ref('website.default_website')
        cls.website.with_context(filecheck_skip_sync=True).write({
            'filecheck_publishable_key': 'pk_01jf8n2q4vxw9k3m5p7r9t1v3x',
            'filecheck_secret_key': 'sk_0123456789abcdef0123456789abcdef',
            'filecheck_artwork_timing': 'checkout',
        })
        Resource = cls.env['filecheck.resource']
        cls.workflow = Resource.create({
            'name': 'Business cards', 'filecheck_id': 'wf_cards',
            'kind': 'workflow', 'website_id': cls.website.id})
        cls.workflow_later = Resource.create({
            'name': 'Posters', 'filecheck_id': 'wf_posters',
            'kind': 'workflow', 'website_id': cls.website.id})
        cls.website.filecheck_default_workflow_id = cls.workflow

        Product = cls.env['product.product']
        cls.cards = Product.create({
            'name': 'Business Cards', 'list_price': 20, 'sale_ok': True,
            'website_published': True, 'default_code': 'CARDS',
            'filecheck_mode': 'default'})
        cls.poster = Product.create({
            'name': 'Poster', 'list_price': 30, 'sale_ok': True,
            'website_published': True,
            'filecheck_mode': 'custom', 'filecheck_workflow_id': cls.workflow_later.id,
            'filecheck_artwork_timing': 'after_order'})
        cls.mug = Product.create({
            'name': 'Plain Mug', 'list_price': 10, 'sale_ok': True,
            'website_published': True})
        cls.partner = cls.env['res.partner'].create({
            'name': 'Ada Lovelace', 'email': 'ada@example.com',
            'street': '1 Print Street', 'city': 'London'})

    def make_order(self, confirm=True):
        order = self.env['sale.order'].create({
            'partner_id': self.partner.id,
            'website_id': self.website.id,
            'order_line': [
                Command.create({'product_id': self.cards.id, 'product_uom_qty': 2,
                                'filecheck_job_id': JOB_A}),
                Command.create({'product_id': self.poster.id, 'product_uom_qty': 1}),
                Command.create({'product_id': self.mug.id, 'product_uom_qty': 1}),
            ],
        })
        if confirm:
            order.action_confirm()
        return order

    def line_of(self, order, product):
        return order.order_line.filtered(lambda l: l.product_id == product)
