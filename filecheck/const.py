import re

API_BASE = 'https://api.filecheck.io'
CDN_ELEMENT = 'https://cdn.filecheck.io/element/{pk}/filecheck.js'
ADMIN_ORDER = 'https://admin.filecheck.io/orders/{order}/{job}'

# Developer override for the API base (staging): System Parameter.
API_URL_PARAM = 'filecheck.api_url'

# Session: { str(product.template id): {jobId, ready, files} } until add-to-cart.
SESSION_KEY = 'filecheck_jobs'

# Job ids are 'job_' + a ULID; anything else from the storefront is refused.
JOB_ID_RE = re.compile(r'^job_[0-9A-Za-z]{10,64}$')

# Odoo's product-page purchase buttons, on top of the Element's built-in list
# (which already covers #add_to_cart).
CART_BUTTON_SELECTOR = '#add_to_cart, .o_we_buy_now'

TIMINGS = [
    ('checkout', 'Before Add to Cart'),
    ('either', 'Before or after checkout'),
    ('after_order', 'After checkout'),
]
PRESENTATIONS = [
    ('inline', 'Inline on the page'),
    ('dialog', 'Button opening a dialog'),
]

# sale.order state -> the status Filecheck shows on the order.
ORDER_STATUS = {
    'draft': 'pending',
    'sent': 'pending',
    'sale': 'processing',
    'done': 'completed',
    'cancel': 'cancelled',
}
