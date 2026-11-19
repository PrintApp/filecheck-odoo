# Filecheck for Odoo

Odoo eCommerce (`website_sale`) integration for [Filecheck](https://filecheck.io),
the print file preflight service. Supports **Odoo 17 and 18** from a single codebase,
structured like the [Print.App addon](https://github.com/PrintApp/print-app-odoo).

Customers upload their artwork on the product page. Filecheck checks it against
the product's workflow and fixes what it can before Add to Cart unlocks. The checked
job follows the cart line onto the sale order. Confirmed orders are sent to Filecheck,
and the print-ready files are saved on the order. Products can also collect their
artwork **after checkout** through a signed upload link.

## How it works

All the checking runs on Filecheck. The addon is a thin adapter, the Odoo
counterpart of the WooCommerce / PrestaShop / OpenCart plugins:

- **Product page.** Before `#o_wsale_cta_wrapper` the addon renders `#fc-slot-{id}`,
  a JSON params block and `https://cdn.filecheck.io/element/{pk}/filecheck.js`.
  `static/src/js/filecheck_product.js` calls `Filecheck.mount()`, which mounts the
  upload widget, gates the purchase buttons on `canProceed` and shows the proof gallery.
  Odoo's Add to Cart and Buy Now are `<a>` elements, which `mount()` can only mark
  `.filecheck-gated`. The script therefore stops clicks on gated buttons in the
  capture phase, before website_sale's own handlers run.
- **Cart.** Odoo's add-to-cart is a JSON call that doesn't post the page's own
  inputs. So every status change is saved to the session through
  `/filecheck/save_job` (a CSRF-safe `type='json'` route that checks the job id
  format), and the buttons stay gated until that save lands. The
  `_cart_update` override copies the ready job onto the line (`filecheck_job_id`,
  plus `Artwork: file.pdf` in the line description). `_cart_find_product_line`
  keeps one line per uploaded file.
- **No refusals at the cart.** The Filecheck element *fails open*: when it can't
  reach Filecheck it tells the customer "you can continue with your order", so a
  store can always take orders. A Filecheck product bought without a checked file
  (element unavailable, the shop grid's quick add, an order keyed in by a sales
  rep) is sent as *waiting for artwork*, and the customer gets the upload link.
  Whatever the timing, no line ships without a file.
- **Order sync.** `action_confirm` queues a `filecheck.sync` row and triggers the
  cron, so it runs right away without delaying the request. The row sends
  `POST /orders/{id}` with lines carrying their `jobId`, and lines without one carry
  `awaitArtwork` + `workflowId`. Failed sends are retried with backoff (5/15/60/360
  min, 8 attempts, then a chatter note). Cancelling a synced order re-sends its
  status. Lines designed with the Print.App addon ride along as `printapp`, so the
  Filecheck order page lists the whole order.
- **Artwork after checkout.** For orders with lines waiting for artwork, the addon
  fetches `GET /orders/{id}/upload-link`. It then shows the link on the shop
  confirmation page and the portal order page, and emails it once (*Filecheck:
  Upload your artwork* mail template, editable, `noupdate`). The cron re-reads the
  link every 30 minutes for 60 days and puts the jobs customers create there onto
  their order lines.
- **Print files.** Once a job has finished, the cron downloads each run's
  `downloadUrl` (the corrected file, else the upload) and attaches it to the sale
  order, with a chatter note. These URLs are presigned and expire, so they are
  fetched fresh and never stored. The size limit is 512 MB, set by the
  `filecheck.max_file_mb` System Parameter.
- **Backend.** The settings block (*Website → Configuration → Settings → Filecheck*)
  holds the keys, default workflow, artwork timing, presentation, and the email and
  save-files switches. Each product has a **Filecheck** tab (off / website default
  / specific workflow, timing, presentation, connector). Each order has a
  **Filecheck** tab: an OWL panel with per-line job status, outcome badges, proofs,
  live download links, the saved files and a *Sync with Filecheck* button.
- Workflows and connectors are cached as `filecheck.resource` records, so they can
  be picked from normal dropdowns. *Test connection and sync workflows* refreshes
  them. Records that disappear upstream are archived, never deleted.

Keys are stored **per website**, like Print.App's. Orders without a website
(entered in the backend) use the first website that has Filecheck keys. The secret
key field is limited to `base.group_system` and never reaches the storefront.

## Install

1. Copy (or symlink) `filecheck/` into your addons path and install **Filecheck
   Print File Preflight**. It depends on `website_sale`.
2. In *Website → Configuration → Settings → Filecheck*, paste the publishable and
   secret keys from the Filecheck dashboard, then click **Test connection and sync
   workflows**. Pick a default workflow.
3. Open a product, go to the **Filecheck** tab, and choose *Website's default
   workflow* or a specific one. Products are opt-in (default *Off*).

## Development

```bash
cd docker
docker compose up odoo17     # http://localhost:8071
docker compose up odoo18     # http://localhost:8072
```

Tests (Filecheck API mocked, plus an HTTP test through `/filecheck/save_job` and
the real `/shop/cart/update_json`):

```bash
docker compose run --rm odoo18 odoo -d fc_test -i filecheck --test-tags /filecheck --stop-after-init
```

From Git Bash, set `MSYS_NO_PATHCONV=1` first, or `/filecheck` is rewritten into a
Windows path. For a staging API, set the System Parameter `filecheck.api_url`.
`docker/seed_demo.py` configures a demo database with placeholder keys.

## Notes and limitations

- Odoo Online (SaaS) can't install third-party modules. Self-hosted and Odoo.sh can.
- Odoo 19 is not supported yet. It renames `type='json'` routes to `jsonrpc` and
  reworks the cart API (`_cart_update`).
- Order emails don't embed the upload link: Odoo mail template bodies can't be
  inherited without overwriting merchant edits. The confirmation page, the portal
  order page (linked from the standard email) and the dedicated upload email carry it.
- Guest sessions: the pre-cart job lives in the Odoo session (garbage-collected
  after about 7 days of inactivity). Once in the cart, it's on the order line.
