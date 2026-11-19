{
    'name': 'Filecheck Print File Preflight',
    'summary': 'Check, fix and collect customer print files on Odoo eCommerce orders',
    'description': """
Filecheck integration for Odoo eCommerce.

Customers upload their artwork on the product page; Filecheck checks it
against the product's workflow (and fixes what it can) before Add to Cart
unlocks. The checked file follows the cart line onto the sale order, the
print-ready files are saved on the order, and products can collect their
artwork after checkout through a signed upload link.
""",
    'version': '1.0.0',
    'category': 'Website/eCommerce',
    'author': 'Filecheck',
    'website': 'https://filecheck.io',
    'support': 'support@filecheck.io',
    'license': 'LGPL-3',
    'depends': ['website_sale'],
    'external_dependencies': {'python': ['requests']},
    'data': [
        'security/ir.model.access.csv',
        'data/ir_cron.xml',
        'data/mail_template.xml',
        'views/res_config_settings_views.xml',
        'views/product_views.xml',
        'views/sale_order_views.xml',
        'views/templates_product.xml',
        'views/templates_order.xml',
    ],
    'assets': {
        'web.assets_backend': [
            'filecheck/static/src/backend/**/*',
        ],
    },
    'post_init_hook': 'post_init_hook',
    'installable': True,
    'application': False,
}
