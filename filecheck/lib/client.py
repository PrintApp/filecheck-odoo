"""Filecheck REST client: the server-to-server calls the addon makes.

Deliberately free of Odoo imports so it can be unit-tested on its own.

API facts this relies on (verified against the API source, some contradict
the public docs):
- Auth is `Authorization: Bearer sk_...`; gateway auth failures come back as a
  bare `{"message": "Forbidden"}` with no `error` key, app errors as
  `{"error": true, "message": ...}`, with no error codes. Branch on status.
- No idempotency keys exist, so POSTs are never retried here. POST
  /orders/{id} is an upsert keyed by order id, so the addon's own outbox may
  safely send it again.
- `GET /jobs/{id}?expand=runs` returns `{id, status, outcome, runs[]}` with no
  `job` wrapper; run `downloadUrl`s are presigned and expire (~60 min), so
  they are fetched fresh every time and never stored.
"""
from urllib.parse import quote

import requests

DEFAULT_TIMEOUT = 15
TERMINAL_JOB_STATUSES = ('done', 'skipped', 'error')


class FilecheckError(Exception):
    def __init__(self, message, status=None):
        super().__init__(message)
        self.status = status

    @property
    def is_auth(self):
        return self.status in (401, 403)


class FilecheckClient:
    def __init__(self, secret_key, base_url, session=None, timeout=DEFAULT_TIMEOUT):
        if not secret_key:
            raise FilecheckError('The Filecheck secret key is not configured.')
        self.secret_key = secret_key.strip()
        self.base_url = (base_url or '').rstrip('/')
        self.http = session or requests
        self.timeout = timeout

    # ------------------------------------------------------------------

    def _request(self, method, path, json=None, allow_404=False):
        try:
            response = self.http.request(
                method,
                f'{self.base_url}{path}',
                json=json,
                headers={
                    'Authorization': f'Bearer {self.secret_key}',
                    'Accept': 'application/json',
                },
                timeout=self.timeout,
            )
        except requests.RequestException as exc:
            raise FilecheckError(f'Could not reach Filecheck: {exc}') from exc

        if allow_404 and response.status_code == 404:
            return None
        try:
            body = response.json()
        except ValueError:
            body = None
        if 200 <= response.status_code < 300:
            return body if body is not None else {}

        message = body.get('message') if isinstance(body, dict) else None
        if response.status_code in (401, 403):
            message = 'Filecheck refused the secret key. Check it in the Filecheck dashboard.'
        raise FilecheckError(
            message or f'Filecheck returned HTTP {response.status_code}',
            status=response.status_code,
        )

    @staticmethod
    def _list(body, key):
        items = body.get(key) if isinstance(body, dict) else body
        return [item for item in (items or []) if isinstance(item, dict) and item.get('id')]

    # ------------------------------------------------------------------

    def list_workflows(self):
        return self._list(self._request('GET', '/workflows/'), 'workflows')

    def list_connectors(self):
        return self._list(self._request('GET', '/connectors/'), 'connectors')

    def sync_order(self, order_id, payload):
        """Capture (or update) an order: links its jobs, and records the lines
        that wait for artwork after checkout."""
        return self._request('POST', f'/orders/{quote(str(order_id), safe="")}', json=payload)

    def get_upload_link(self, order_id):
        """`{orderId, orderName, url, status, items[{itemId, jobId, ...}]}`
        for an order with lines waiting for artwork; None when it has none."""
        body = self._request(
            'GET', f'/orders/{quote(str(order_id), safe="")}/upload-link',
            allow_404=True)
        if not isinstance(body, dict) or not body.get('url'):
            return None
        return body

    def get_job(self, job_id):
        """The job with its runs (one per file): id, name, status, outcome,
        hasOutput, downloadUrl (the corrected file, else the upload), proofs."""
        return self._request('GET', f'/jobs/{quote(str(job_id), safe="")}?expand=runs')

    def download(self, url, max_bytes):
        """Fetch a presigned file URL. No Authorization header: storage
        rejects a request that carries both a signature and a bearer token."""
        try:
            with self.http.get(url, stream=True, timeout=(10, 300)) as response:
                if response.status_code != 200:
                    raise FilecheckError(
                        f'File download returned HTTP {response.status_code}',
                        status=response.status_code)
                chunks, size = [], 0
                for chunk in response.iter_content(chunk_size=1 << 20):
                    size += len(chunk)
                    if size > max_bytes:
                        raise FilecheckError(
                            f'File is larger than the {max_bytes // (1 << 20)} MB limit')
                    chunks.append(chunk)
                return b''.join(chunks)
        except requests.RequestException as exc:
            raise FilecheckError(f'File download failed: {exc}') from exc


def is_terminal(job):
    return isinstance(job, dict) and job.get('status') in TERMINAL_JOB_STATUSES
