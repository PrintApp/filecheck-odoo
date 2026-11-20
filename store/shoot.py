"""Apps Store screenshots and banner (not shipped).

Needs the demo store from seed_screenshots.py on :8072, store/mock_api.py, and
`pip install playwright pillow` (uses the installed Chrome, no browser download).

    python shoot.py [banner|product|order|portal|tab|settings ...]
"""
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

ODOO = 'http://localhost:8072'
HERE = Path(__file__).resolve().parent
OUT = HERE.parent / 'filecheck' / 'static' / 'description'
PRODUCT_URL = '/shop/premium-business-cards-40'
ORDER_ID = 71
PORTAL_URL = '/my/orders/71?access_token=2dda15c2-3b0a-4506-bb1d-a12e7892c7d9'
PRODUCT_ID = 40
VIEW = {'width': 1440, 'height': 900}


def login(page):
    page.goto(f'{ODOO}/web/login')
    page.fill('input[name=login]', 'admin')
    page.fill('input[name=password]', 'admin')
    page.click('form.oe_login_form button[type=submit]')
    page.wait_for_url('**/odoo**')


def backend_tab(page, url, tab):
    page.goto(f'{ODOO}{url}')
    page.locator('.o_form_view .o_notebook .nav-link:visible', has_text=tab).first.click()
    page.wait_for_timeout(1500)


def shoot(names):
    with sync_playwright() as p:
        browser = p.chromium.launch(channel='chrome')
        ctx = browser.new_context(viewport=VIEW, device_scale_factor=1)
        page = ctx.new_page()

        if 'banner' in names:
            page.set_viewport_size({'width': 1600, 'height': 900})
            page.goto((HERE / 'banner.html').as_uri())
            page.wait_for_timeout(800)
            page.screenshot(path=OUT / 'banner.png')
            page.set_viewport_size(VIEW)

        if 'product' in names:
            page.goto(f'{ODOO}{PRODUCT_URL}')
            page.wait_for_selector('[data-filecheck-element] iframe', timeout=20000)
            page.wait_for_timeout(6000)
            page.screenshot(path=OUT / 'screenshot_product_page.png')

        if 'portal' in names:
            page.goto(f'{ODOO}{PORTAL_URL}')
            page.wait_for_selector('.o_filecheck_upload')
            page.locator('.o_filecheck_upload').scroll_into_view_if_needed()
            page.evaluate('window.scrollBy(0, -260)')
            page.wait_for_timeout(500)
            page.screenshot(path=OUT / 'screenshot_upload_link.png')

        if {'order', 'tab', 'settings'} & set(names):
            login(page)
        if 'order' in names:
            backend_tab(page, f'/odoo/sale.order/{ORDER_ID}', 'Filecheck')
            page.wait_for_selector('.o_filecheck_order_panel img')
            page.wait_for_timeout(1000)
            page.locator('.o_notebook').scroll_into_view_if_needed()
            page.evaluate("document.querySelector('.o_content, .o_form_view .o_content')?.scrollBy(0, -120)")
            page.screenshot(path=OUT / 'screenshot_order.png')
        if 'tab' in names:
            backend_tab(page, f'/odoo/product.template/{PRODUCT_ID}', 'Filecheck')
            page.screenshot(path=OUT / 'screenshot_product_tab.png')
        if 'settings' in names:
            page.goto(f'{ODOO}/odoo/settings#website')
            page.wait_for_selector('#filecheck_settings, #filecheck_keys_setting')
            page.locator('#filecheck_keys_setting').scroll_into_view_if_needed()
            page.evaluate("""() => {
                const h = [...document.querySelectorAll('h2')].find(e => e.textContent.trim() === 'Filecheck');
                h && h.scrollIntoView({block: 'start'});
                document.querySelector('.o_setting_container, .settings')?.scrollBy?.(0, -10);
            }""")
            # Never show a real key in the listing: a dummy one, not saved.
            page.fill('#filecheck_publishable_key_0', 'pk_01JEXAMPLEKEY')
            page.locator('#filecheck_publishable_key_0').blur()
            page.wait_for_timeout(600)
            page.screenshot(path=OUT / 'screenshot_settings.png')
        browser.close()


if __name__ == '__main__':
    shoot(sys.argv[1:] or ['banner', 'product', 'portal', 'order', 'tab', 'settings'])
