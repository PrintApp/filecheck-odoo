"""Stand-in Filecheck API for the Apps Store screenshots (not shipped).

Serves just enough of the REST API, in the shapes the real one returns, for
the order screens to show realistic data without a real account:

    python mock_api.py            # http://0.0.0.0:8765
    System Parameter filecheck.api_url = http://host.docker.internal:8765

Proof images load in the browser (localhost); print files are downloaded by
Odoo inside Docker (host.docker.internal).
"""
import io
import json
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from PIL import Image, ImageDraw, ImageFont

PORT = 8765
BROWSER = f'http://localhost:{PORT}'
ODOO = f'http://host.docker.internal:{PORT}'

WORKFLOWS = [
    ('wf_01KVN318C37QWSAFTD19VXS2X1', 'Business Card'),
    ('wf_01KWBN24KKBN4BDPPRHANN3KNT', 'Flyer / Postcard'),
    ('wf_01KXNZ1E8Z4FYBTAVCNC22HSDF', 'Booklet / Catalog'),
    ('wf_01M7SJP78G2HESKHHJBA1E3XG2', 'Rigid Signs'),
    ('wf_01KVJNSZE5VR63MJ4CAWMFYC9Y', 'Large Format Poster'),
]
CONNECTORS = [('cntr_01KX0QTYFROMPAGES0000000', 'Quantity from page count')]

# The upload page link and the items waiting on it, keyed by order id.
UPLOAD_ITEMS = {}


def font(size, bold=False):
    for name in (('arialbd.ttf' if bold else 'arial.ttf'), 'DejaVuSans.ttf'):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default()


def proof_png(side):
    """A business card proof: 85 x 55 mm at ~6 px/mm, trim and bleed marked."""
    w, h = 560, 370
    img = Image.new('RGB', (w, h), 'white')
    d = ImageDraw.Draw(img)
    if side == 'front':
        d.polygon([(0, 0), (190, 0), (90, h), (0, h)], fill=(196, 255, 0))
        d.text((230, 120), 'Prima Print Co.', fill=(10, 10, 10), font=font(34, True))
        d.text((232, 172), 'Jane Doe, Director', fill=(60, 60, 60), font=font(22))
        d.text((232, 206), '+1 555 0100  |  prima.example', fill=(90, 90, 90), font=font(18))
    else:
        d.rectangle([0, 0, w, h], fill=(18, 18, 18))
        d.text((150, 150), 'prima.example', fill=(196, 255, 0), font=font(40, True))
    d.rectangle([12, 12, w - 13, h - 13], outline=(255, 40, 80), width=2)  # trim
    buf = io.BytesIO()
    img.save(buf, 'PNG')
    return buf.getvalue()


PDF = (b'%PDF-1.4\n1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n'
       b'2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj\n'
       b'3 0 obj<</Type/Page/Parent 2 0 R/MediaBox[0 0 252 168]>>endobj\n'
       b'trailer<</Root 1 0 R>>\n%%EOF\n')


def job(job_id):
    runs = []
    for side, outcome in (('front', 'pass'), ('back', 'fixed')):
        runs.append({
            'id': f'task_{side}_{job_id[-8:]}',
            'name': f'bizcard-{side}.pdf',
            'status': 'done',
            'outcome': outcome,
            'hasOutput': True,
            'proofs': [{'url': f'{BROWSER}/proof-{side}.png'}],
            'downloadUrl': f'{ODOO}/files/bizcard-{side}.pdf?X-Amz-Signature=demo',
        })
    return {'id': job_id, 'status': 'done', 'outcome': 'fixed', 'runs': runs}


class Handler(BaseHTTPRequestHandler):
    def send(self, status, body, ctype='application/json'):
        data = json.dumps(body).encode() if ctype == 'application/json' else body
        self.send_response(status)
        self.send_header('Content-Type', ctype)
        self.send_header('Content-Length', str(len(data)))
        self.send_header('Access-Control-Allow-Origin', '*')
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        path = self.path.split('?')[0]
        if path == '/workflows/':
            return self.send(200, {'workflows': [{'id': i, 'title': t} for i, t in WORKFLOWS]})
        if path == '/connectors/':
            return self.send(200, {'connectors': [{'id': i, 'name': n} for i, n in CONNECTORS]})
        m = re.fullmatch(r'/jobs/([^/]+)', path)
        if m:
            return self.send(200, job(m.group(1)))
        m = re.fullmatch(r'/orders/([^/]+)/upload-link', path)
        if m:
            items = UPLOAD_ITEMS.get(m.group(1))
            if not items:
                return self.send(404, {'error': True, 'message': 'No lines are waiting for artwork.'})
            return self.send(200, {
                'orderId': m.group(1), 'url': 'https://app.filecheck.io/orders/upload?t=demo',
                'status': 'awaiting', 'items': items})
        m = re.fullmatch(r'/proof-(front|back)\.png', path)
        if m:
            return self.send(200, proof_png(m.group(1)), 'image/png')
        if path.startswith('/files/'):
            return self.send(200, PDF, 'application/pdf')
        return self.send(404, {'error': True, 'message': 'Not found'})

    def do_POST(self):
        m = re.fullmatch(r'/orders/([^/]+)', self.path.split('?')[0])
        if not m:
            return self.send(404, {'error': True, 'message': 'Not found'})
        body = json.loads(self.rfile.read(int(self.headers.get('Content-Length') or 0)) or b'{}')
        waiting = [{'itemId': i['itemId'], 'name': i['name'], 'quantity': i['quantity'], 'jobId': None}
                   for i in body.get('items', []) if i.get('awaitArtwork')]
        if waiting:
            UPLOAD_ITEMS[m.group(1)] = waiting
        return self.send(200, {'success': True, 'affectedJobIds': []})

    def log_message(self, fmt, *args):
        pass


if __name__ == '__main__':
    print(f'mock Filecheck API on :{PORT}')
    ThreadingHTTPServer(('0.0.0.0', PORT), Handler).serve_forever()
