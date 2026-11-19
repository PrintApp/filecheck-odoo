from unittest.mock import MagicMock

import requests

from odoo.tests import BaseCase, tagged

from ..lib.client import FilecheckClient, FilecheckError


def response(status, body=None, text=None):
    res = MagicMock()
    res.status_code = status
    if body is None:
        res.json.side_effect = ValueError('no json')
    else:
        res.json.return_value = body
    res.text = text or ''
    return res


@tagged('post_install', '-at_install')
class TestClient(BaseCase):

    def client(self, *responses):
        http = MagicMock()
        http.request.side_effect = list(responses)
        return FilecheckClient('sk_x', 'https://api.example/', session=http), http

    def test_requires_secret_key(self):
        with self.assertRaises(FilecheckError):
            FilecheckClient('', 'https://api.example')

    def test_bearer_auth_and_list_envelope(self):
        client, http = self.client(response(200, {'workflows': [
            {'id': 'wf_1', 'title': 'Cards'}, {'title': 'no id'}]}))
        self.assertEqual(client.list_workflows(), [{'id': 'wf_1', 'title': 'Cards'}])
        method, url = http.request.call_args.args
        self.assertEqual((method, url), ('GET', 'https://api.example/workflows/'))
        self.assertEqual(http.request.call_args.kwargs['headers']['Authorization'], 'Bearer sk_x')

    def test_gateway_forbidden_is_auth_error(self):
        # API Gateway: bare {"message": "Forbidden"}, no `error` key.
        client, _http = self.client(response(403, {'message': 'Forbidden'}))
        with self.assertRaises(FilecheckError) as ctx:
            client.list_connectors()
        self.assertTrue(ctx.exception.is_auth)
        self.assertIn('secret key', str(ctx.exception))

    def test_app_error_message(self):
        client, _http = self.client(response(400, {'error': True, 'message': 'Bad order'}))
        with self.assertRaisesRegex(FilecheckError, 'Bad order'):
            client.sync_order('42', {})

    def test_non_json_error(self):
        client, _http = self.client(response(502, None, '<html>'))
        with self.assertRaisesRegex(FilecheckError, 'HTTP 502'):
            client.get_job('job_x')

    def test_upload_link_404_is_none(self):
        client, http = self.client(response(404, {'error': True, 'message': 'none'}))
        self.assertIsNone(client.get_upload_link('S/1'))
        # order ids are path-escaped
        self.assertTrue(http.request.call_args.args[1].endswith('/orders/S%2F1/upload-link'))

    def test_network_error(self):
        http = MagicMock()
        http.request.side_effect = requests.ConnectionError('boom')
        client = FilecheckClient('sk_x', 'https://api.example', session=http)
        with self.assertRaisesRegex(FilecheckError, 'Could not reach'):
            client.get_job('job_x')
