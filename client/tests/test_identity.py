"""The backend's identity: what the audit records, and what the cache key covers.

Two failures motivated these checks. Both were found the first time the client was
pointed at the real kev-4B instead of the stub (docs/design-handoff.md sections 11.1
and 11.7):

* the client read ``/v1/models`` in a shape kev does not send, so every audit row
  silently lost its checkpoint temperature -- and a row without the temperature
  cannot explain, later, why a threshold was cleared;
* the cache key covered (url, payload, model) but not *which service* answered, so
  one URL serving a different backend was served the previous backend's answers.
  Exit code 0, no warning: the failure that looks like success.

``KEV_MODELS`` below is copied verbatim from a live kev-4B server. That matters: a
fixture hand-written to the shape the client already expects can never fail, which
is exactly how both of these survived a green test suite.
"""

import http.server
import json
import pathlib
import sys
import threading
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from bixian import cache, client  # noqa: E402

KEV_MODELS = json.dumps({"models": [{
    "id": "kev-latest",
    "aliases": ["jev-latest"],
    "run": "C:\\models\\kev-4b-local",
    "base": "C:\\models\\base",
    "lora": 16,
    "device": "cuda",
    "temperature": 2.1435469250725863,
    "prefix_cache": {"size": 4, "min_state_tokens": 384, "hits": 0, "misses": 0, "cached_states": 0},
}]})


class _ModelsHandler(http.server.BaseHTTPRequestHandler):
    body = b"{}"

    def do_GET(self):
        self.send_response(200)
        self.send_header("content-type", "application/json")     # no charset, like kev
        self.send_header("content-length", str(len(self.body)))
        self.end_headers()
        self.wfile.write(self.body)

    def log_message(self, *args):
        pass


class TestModelInfo(unittest.TestCase):
    def _info(self, body):
        handler = type("_Handler", (_ModelsHandler,), {"body": body.encode("utf-8")})
        httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        try:
            return client.model_info("http://127.0.0.1:%d" % httpd.server_address[1], timeout=5)
        finally:
            httpd.shutdown()
            httpd.server_close()

    def test_kevs_own_shape_yields_the_checkpoint_temperature(self):
        info = self._info(KEV_MODELS)
        self.assertEqual(info["model"], "kev-latest")
        self.assertEqual(info["run"], "C:\\models\\kev-4b-local")
        self.assertEqual(info["temperature"], 2.1435469250725863)

    def test_a_gateway_shaped_answer_is_still_read(self):
        info = self._info(json.dumps({"model": "kev-latest", "checkpoint": {"run": "x-v1", "temperature": 2.2}}))
        self.assertEqual((info["model"], info["temperature"], info["run"]), ("kev-latest", 2.2, "x-v1"))

    def test_an_openai_style_listing_still_yields_the_id(self):
        info = self._info(json.dumps({"object": "list", "data": [{"id": "kev-latest", "object": "model"}]}))
        self.assertEqual(info["model"], "kev-latest")
        self.assertNotIn("temperature", info, "no temperature may be invented when none was reported")

    def test_an_unreadable_answer_yields_nothing_rather_than_a_wrong_value(self):
        self.assertEqual(self._info(json.dumps({"object": "list"})), {})


class TestCacheKey(unittest.TestCase):
    payload = {"state": "a window", "questions": {"q": {"type": "noul", "instructions": "continue?"}}}

    def key(self, backend):
        return cache.make_key("http://127.0.0.1:8009", self.payload, "kev-latest", backend)

    def test_the_same_backend_is_the_same_key(self):
        self.assertEqual(self.key("kev-4b-local"), self.key("kev-4b-local"))

    def test_a_different_backend_behind_the_same_url_is_a_different_key(self):
        self.assertNotEqual(self.key("kev-stub"), self.key("kev-4b-local"))

    def test_an_unknown_backend_does_not_collide_with_a_known_one(self):
        self.assertNotEqual(self.key(None), self.key("kev-4b-local"))

    def test_the_payload_and_model_still_separate_entries(self):
        self.assertNotEqual(self.key("r"), cache.make_key("http://127.0.0.1:8009", {"state": "other"}, "kev-latest", "r"))
        self.assertNotEqual(self.key("r"), cache.make_key("http://127.0.0.1:8009", self.payload, "kev-9b", "r"))


if __name__ == "__main__":
    unittest.main()
