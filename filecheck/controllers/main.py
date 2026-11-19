from odoo import http
from odoo.http import request

from ..const import JOB_ID_RE, SESSION_KEY

MAX_FILES = 20
MAX_FILE_NAME = 200


class FilecheckController(http.Controller):
    """Storefront endpoint the product-page script calls on every Filecheck
    status change, so add-to-cart (an Odoo JSON call that posts none of the
    page's own inputs) finds the customer's checked job in the session.

    `type='json'` routes only accept a JSON-RPC envelope with an
    application/json content type, which a cross-site form cannot produce, so
    this is CSRF-safe without a token. (Odoo 19 renames the type 'jsonrpc'.)
    """

    @staticmethod
    def _clean_files(files):
        names = []
        for name in files if isinstance(files, list) else []:
            if isinstance(name, str) and name.strip():
                names.append(name.strip()[:MAX_FILE_NAME])
            if len(names) >= MAX_FILES:
                break
        return names

    @http.route('/filecheck/save_job', type='json', auth='public',
                methods=['POST'], website=True, sitemap=False)
    def save_job(self, product_id=None, job_id=None, ready=False, files=None, **kwargs):
        try:
            tmpl = request.env['product.template'].sudo().browse(int(product_id)).exists()
        except (TypeError, ValueError):
            tmpl = None
        if not tmpl or not tmpl.sale_ok or not tmpl._filecheck_config(request.website.sudo()):
            return {'success': False}

        jobs = dict(request.session.get(SESSION_KEY) or {})
        key = str(tmpl.id)
        if not job_id:
            jobs.pop(key, None)
        elif isinstance(job_id, str) and JOB_ID_RE.match(job_id):
            jobs[key] = {
                'jobId': job_id,
                'ready': bool(ready),
                'files': self._clean_files(files),
            }
        else:
            return {'success': False}
        request.session[SESSION_KEY] = jobs
        return {'success': True}
