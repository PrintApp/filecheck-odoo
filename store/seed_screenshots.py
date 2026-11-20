# Demo data for the Apps Store screenshots (not shipped). Run with the mock API up:
#   docker compose run --rm -T -v ../store:/mnt/store odoo18 odoo shell -d demo18 --no-http < ../store/seed_screenshots.py
# The publishable key is the public one the filecheck.io website embeds; the
# secret key is a placeholder, and every server call goes to store/mock_api.py.
import base64

from odoo import Command

env['ir.config_parameter'].sudo().set_param('filecheck.api_url', 'http://host.docker.internal:8765')
site = env.ref('website.default_website')
site.with_context(filecheck_skip_sync=True).write({
    'filecheck_publishable_key': 'pk_01KTV8TNWFERRB4G2KFK58SQDW',
    'filecheck_secret_key': 'sk_00000000000000000000000000000000',
    'filecheck_artwork_timing': 'checkout',
    'filecheck_presentation': 'inline',
})
env['filecheck.resource'].search([('website_id', '=', site.id)]).unlink()
site._filecheck_sync_library()
Res = env['filecheck.resource']
cards_wf = Res.search([('filecheck_id', '=', 'wf_01KVN318C37QWSAFTD19VXS2X1')])
poster_wf = Res.search([('filecheck_id', '=', 'wf_01KVJNSZE5VR63MJ4CAWMFYC9Y')])
site.filecheck_default_workflow_id = cards_wf

env['product.template'].search([('filecheck_mode', '!=', 'off')]).write({'filecheck_mode': 'off'})
with open('/mnt/store/img/business-cards.jpg', 'rb') as f:
    image = base64.b64encode(f.read())
Tmpl = env['product.template']
cards = Tmpl.search([('name', '=', 'Premium Business Cards')]) or Tmpl.create({'name': 'Premium Business Cards'})
cards.write({
    'list_price': 24.0, 'sale_ok': True, 'is_published': True, 'image_1920': image,
    'description_ecommerce': '<p>85 x 55 mm, 400 gsm silk, full colour on both sides. '
                             'Upload your artwork below: we check it instantly so your '
                             'cards print exactly as designed.</p>',
    'filecheck_mode': 'default', 'filecheck_artwork_timing': False,
})
poster = Tmpl.search([('name', '=', 'Large Format Poster')]) or Tmpl.create({'name': 'Large Format Poster'})
poster.write({
    'list_price': 39.0, 'sale_ok': True, 'is_published': True,
    'filecheck_mode': 'custom', 'filecheck_workflow_id': poster_wf.id,
    'filecheck_artwork_timing': 'after_order',
})

partner = env['res.partner'].search([('email', '=', 'jane@prima.example')], limit=1) or env['res.partner'].create({
    'name': 'Jane Doe', 'email': 'jane@prima.example', 'street': '12 Harbour Road',
    'city': 'Bristol', 'zip': 'BS1 4RN', 'country_id': env.ref('base.uk').id})
order = env['sale.order'].create({
    'partner_id': partner.id, 'website_id': site.id,
    'order_line': [
        Command.create({'product_id': cards.product_variant_id.id, 'product_uom_qty': 1,
                        'filecheck_job_id': 'job_01KZ7Q3M5XDEMO0BIZCARD00',
                        'filecheck_files': ['bizcard-front.pdf', 'bizcard-back.pdf']}),
        Command.create({'product_id': poster.product_variant_id.id, 'product_uom_qty': 2}),
    ]})
order.action_confirm()
order.action_filecheck_sync()
portal_url = order.get_portal_url()  # creates the access token: before the commit
env.cr.commit()
print('SEEDED', cards.id, cards.website_url, 'order', order.id, order.name, portal_url)
