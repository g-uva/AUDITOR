"""Isolated HTTP tests, independent of any running services or PyYAML."""
from http.server import BaseHTTPRequestHandler, HTTPServer
import importlib.util
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Thread
import unittest
from urllib.error import HTTPError

from cim import DeliveryError, send_batch, send_record

ROOT = Path(__file__).resolve().parents[1]
RECEIVER = ROOT.parents[2] / 'pear-prototype/pear_receiver/__main__.py'


class TransportTests(unittest.TestCase):
    def start(self, server):
        thread = Thread(target=server.serve_forever)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(thread.join)
        self.addCleanup(server.shutdown)
        return f'http://127.0.0.1:{server.server_port}'

    def test_acknowledgement_checks(self):
        for receipt in ({'status': 'received', 'record_id': 'wrong'}, {'record_id': 'test'}, [], None):
            with self.subTest(receipt=receipt):
                body = json.dumps(receipt).encode()
                class Handler(BaseHTTPRequestHandler):
                    def do_POST(self):
                        self.rfile.read(int(self.headers['Content-Length']))
                        self.send_response(202); self.end_headers(); self.wfile.write(body)
                    def log_message(self, *args): pass
                url = self.start(HTTPServer(('127.0.0.1', 0), Handler))
                with self.assertRaisesRegex(ValueError, 'acknowledge'):
                    send_record({'record_id': 'test'}, url)

    def test_real_receiver_draft_storage_and_non_idempotence(self):
        spec = importlib.util.spec_from_file_location('isolated_receiver', RECEIVER)
        receiver = importlib.util.module_from_spec(spec); spec.loader.exec_module(receiver)
        with TemporaryDirectory() as folder:
            output=Path(folder)/'received.jsonl'
            url=self.start(receiver.make_server('127.0.0.1',0,output))
            golden=json.loads((ROOT/'fixtures/pear-draft-golden.json').read_text())
            observations=list(golden['sidecar'].values())
            result=send_batch(observations,url)
            self.assertEqual(len(result['acknowledged']),2)
            self.assertEqual([json.loads(line) for line in output.read_text().splitlines()], golden['records'])
            send_batch(observations,url)
            self.assertEqual(len(output.read_text().splitlines()),4)
            output.unlink(); output.mkdir()
            with self.assertRaises(DeliveryError) as error: send_batch(observations,url)
            self.assertEqual(error.exception.delivered,[])
            self.assertIsInstance(error.exception.__cause__,HTTPError)
