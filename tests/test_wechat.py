import base64
import copy
import http.client
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import urllib.error
from urllib.parse import parse_qs, urlsplit

from studio import wechat
from studio.adapters import DeliveryError


PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jRZkAAAAASUVORK5CYII=")
TOKEN, UPLOAD, MATERIAL, ADD, GET, COUNT = (
    "/cgi-bin/stable_token", "/cgi-bin/media/uploadimg", "/cgi-bin/material/add_material",
    "/cgi-bin/draft/add", "/cgi-bin/draft/get", "/cgi-bin/draft/count")


class Response:
    def __init__(self, value):
        self.raw = io.BytesIO(value if isinstance(value, bytes) else json.dumps(value, ensure_ascii=False).encode("utf-8"))

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self, size):
        return self.raw.read(size)


class FakeWeChat:
    """An in-memory API double. No real sockets, credentials, files or proxies."""
    def __init__(self):
        self.calls, self.overrides, self.articles = [], {}, []
        self.uploads = 0
        self.tokens = 0

    def open(self, req, timeout):
        endpoint = urlsplit(req.full_url).path
        assert urlsplit(req.full_url).netloc == "api.weixin.qq.com"
        payload = json.loads(req.data.decode("utf-8")) if req.data and req.get_header("Content-type", "").startswith("application/json") else None
        self.calls.append({"path": endpoint, "request": req, "payload": payload, "timeout": timeout})
        if endpoint in self.overrides:
            value = self.overrides[endpoint]
            if callable(value):
                value = value(req, payload)
            elif isinstance(value, list):
                value = value.pop(0)
            if isinstance(value, Exception):
                raise value
            if value is not None:
                return Response(value)
        if endpoint == TOKEN:
            self.tokens += 1
            return Response({"access_token": f"mock-token-{self.tokens}", "expires_in": 7200})
        if endpoint == UPLOAD:
            self.uploads += 1
            return Response({"url": f"https://mmbiz.qpic.cn/mmbiz_png/image-{self.uploads}/0?wx_fmt=png&from=app"})
        if endpoint == MATERIAL:
            return Response({"media_id": "cover-media-id", "url": "https://mmbiz.qpic.cn/mmbiz_png/cover/0"})
        if endpoint == ADD:
            self.articles = copy.deepcopy(payload["articles"])
            return Response({"media_id": "draft-media-id"})
        if endpoint == GET:
            return Response({"news_item": copy.deepcopy(self.articles)})
        if endpoint == COUNT:
            return Response({"total_count": 7})
        raise AssertionError(f"Unexpected endpoint: {endpoint}")

    def paths(self):
        return [call["path"] for call in self.calls]


class WeChatTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.data_dir = Path(self.tmp.name) / "data"
        self.media = self.data_dir / "media"
        self.media.mkdir(parents=True)
        self.images = []
        for index in range(5):
            name = f"{index:032x}.png"
            (self.media / name).write_bytes(PNG)
            self.images.append("/media/" + name)
        self.cover = "/media/cover.png"
        (self.media / "cover.png").write_bytes(PNG)
        self.post = {"title": "公众号联调：记忆与项目接续", "body": "## 中文 & 标记\n\n这是**真实文字**，保留 `code`。\n\n" +
                     "\n\n".join(f"![机制图 {i}]({src})" for i, src in enumerate(self.images)),
                     "author": "测试作者", "summary": "五张图与一个封面。", "cover": self.cover}
        self.config = {"WECHAT_APP_ID": "test-app-id", "WECHAT_APP_SECRET": "test-secret-not-a-real-key",
                       "WECHAT_PROXY": "http://127.0.0.1:19090"}
        self.fake = FakeWeChat()
        self.builder = self.enterContext(patch("studio.network.build_wechat_opener", return_value=self.fake))
        with wechat._TOKEN_LOCK:
            wechat._TOKENS.clear()

    def send(self, post=None):
        return wechat.send_draft(post or self.post, {"mode": "api"}, self.config, self.data_dir)

    def test_five_images_cover_draft_and_readback_utf8(self):
        result = self.send()
        self.assertEqual(result["status"], "draft")
        self.assertTrue(result["verified"])
        self.assertEqual(result["image_count"], 5)
        self.assertEqual(result["external_id"], "draft-media-id")
        self.assertEqual(result["url"], "")
        self.assertEqual(self.fake.paths(), [TOKEN] + [UPLOAD] * 5 + [MATERIAL, ADD, GET])
        self.assertEqual(self.builder.call_count, 1)
        self.builder.assert_called_with(self.config)
        create = next(call for call in self.fake.calls if call["path"] == ADD)
        self.assertIn("公众号联调".encode("utf-8"), create["request"].data)
        self.assertNotIn(b"\\u516c", create["request"].data)
        article = create["payload"]["articles"][0]
        self.assertEqual(article["article_type"], "news")
        self.assertEqual(article["thumb_media_id"], "cover-media-id")
        self.assertEqual(article["show_cover_pic"], 1)
        self.assertNotIn("paperplane.invalid", article["content"])
        self.assertNotIn("/media/", article["content"])
        self.assertEqual(article["content"].count("<img "), 5)
        self.assertIn("&amp;from=app", article["content"])
        self.assertEqual(self.post["body"].count("/media/"), 5)
        material = next(call for call in self.fake.calls if call["path"] == MATERIAL)
        self.assertEqual(parse_qs(urlsplit(material["request"].full_url).query)["type"], ["image"])
        for call in self.fake.calls:
            if call["path"] in (UPLOAD, MATERIAL):
                self.assertIn(b'name="media"; filename="image.png"', call["request"].data)
                self.assertIn(PNG, call["request"].data)
                self.assertNotIn(str(self.media).encode(), call["request"].data)

    def test_preflight_checks_all_local_images_without_network(self):
        report = wechat.validate_post(self.post, self.data_dir)
        self.assertEqual(report, {"ok": True, "image_count": 5, "unique_image_count": 5, "cover": True})
        self.builder.assert_not_called()
        (self.media / Path(self.images[-1]).name).unlink()
        with self.assertRaises(DeliveryError) as caught:
            self.send()
        self.assertFalse(caught.exception.uncertain)
        self.builder.assert_not_called()

    def test_repeated_image_uploaded_once_and_nested_media_paths_work(self):
        folder = self.media / "机制图"
        folder.mkdir()
        path = folder / "图 01.png"
        path.write_bytes(PNG)
        self.post["body"] = "真实正文\n\n![一](</media/机制图/图 01.png>)\n\n![二](<" + str(path) + ">)"
        result = self.send()
        self.assertEqual(result["image_count"], 2)
        self.assertEqual(self.fake.paths().count(UPLOAD), 1)
        self.assertEqual(self.fake.articles[0]["content"].count("image-1/0"), 2)

    def test_invalid_remote_traversal_and_malformed_images_block_before_token(self):
        sources = ["https://example.com/a.png", "http://mmbiz.qpic.cn/a.png", "//example.com/a.png",
                   "/media/../cover.png", "/media/%2e%2e/cover.png", "/media/%2fetc/passwd",
                   "/media/cover.png?token=secret", "/media/cover.png#x", "data:image/png;base64,eA==",
                   "file:///C:/private.png", str(Path(self.tmp.name) / "outside.png"), "relative.png"]
        for src in sources:
            with self.subTest(src=src), self.assertRaises(DeliveryError):
                self.send({**self.post, "body": f"正文\n\n![图片]({src})"})
        for body in ("正文 ![图片][ref]", "正文 ![未闭合](/media/cover.png", '<img src="/media/cover.png">'):
            with self.subTest(body=body), self.assertRaises(DeliveryError):
                self.send({**self.post, "body": body})
        self.builder.assert_not_called()

    def test_symlink_cannot_escape_media_root(self):
        outside = Path(self.tmp.name) / "outside.png"
        outside.write_bytes(PNG)
        link = self.media / "link.png"
        try:
            link.symlink_to(outside)
        except OSError:
            self.skipTest("Symlinks require Windows developer mode/privilege")
        with self.assertRaises(DeliveryError):
            self.send({**self.post, "cover": "/media/link.png"})
        self.builder.assert_not_called()

    def test_unsupported_and_oversized_images_and_cover_block_before_token(self):
        for filename, content in (("bad.png", b"not an image"), ("gif.png", b"GIF89a"),
                                  ("large.png", PNG + b"x" * wechat.MAX_INLINE_BYTES)):
            (self.media / filename).write_bytes(content)
            with self.subTest(filename=filename), self.assertRaises(DeliveryError):
                self.send({**self.post, "body": f"正文 ![图](/media/{filename})"})
        for cover in ("", "https://example.com/cover.png", "/media/missing.png"):
            with self.subTest(cover=cover), self.assertRaises(DeliveryError):
                self.send({**self.post, "cover": cover})
        self.builder.assert_not_called()

    def test_local_image_above_old_one_mb_limit_is_sent_to_api(self):
        large = self.media / "large-cover.png"
        large.write_bytes(PNG + b"\x00" * 1_700_000)
        result = self.send({**self.post, "body": "正文\n\n![封面](/media/large-cover.png)", "cover": "/media/large-cover.png"})
        self.assertEqual(result["status"], "draft")
        self.assertEqual(self.fake.paths().count(UPLOAD), 1)
        self.assertEqual(self.fake.paths().count(MATERIAL), 1)
        self.assertEqual(self.fake.articles[0]["show_cover_pic"], 0)

    def test_code_examples_are_not_treated_as_images(self):
        self.post["body"] = "真实正文。`![示例](https://example.com/x.png)`\n\n```markdown\n![示例](https://example.com/x.png)\n```"
        result = self.send()
        self.assertEqual(result["image_count"], 0)
        self.assertNotIn(UPLOAD, self.fake.paths())

    def test_empty_or_invalid_article_metadata_blocks_before_token(self):
        for values in ({"title": ""}, {"title": "文" * 65}, {"body": ""}, {"body": "---\na: b\n---\n正文"},
                       {"author": "作" * 17}, {"summary": "摘" * 121}, {"title": None}):
            with self.subTest(values=values), self.assertRaises(DeliveryError):
                self.send({**self.post, **values})
        self.builder.assert_not_called()

    def test_missing_credentials_do_not_contact_network(self):
        for config in ({}, {"WECHAT_APP_ID": "test"}, {"WECHAT_APP_SECRET": "test"}):
            with self.subTest(config=config), self.assertRaises(DeliveryError):
                wechat.send_draft(self.post, {}, config, self.data_dir)
        self.builder.assert_not_called()

    def test_token_cache_expiry_and_credential_changes(self):
        with patch("studio.wechat.time.monotonic", return_value=100):
            self.assertTrue(wechat.test_connection(self.config)["ok"])
        with patch("studio.wechat.time.monotonic", return_value=6900):
            self.assertTrue(wechat.test_connection(self.config)["ok"])
        self.assertEqual(self.fake.paths().count(TOKEN), 1)
        with patch("studio.wechat.time.monotonic", return_value=7001):
            wechat.test_connection(self.config)
        self.assertEqual(self.fake.paths().count(TOKEN), 2)
        wechat.test_connection({**self.config, "WECHAT_APP_SECRET": "changed-test-secret"})
        self.assertEqual(self.fake.paths().count(TOKEN), 3)
        self.assertNotIn(self.config["WECHAT_APP_SECRET"], repr(wechat._TOKENS))

    def test_readonly_connection_does_not_claim_write_permission(self):
        result = wechat.test_connection(self.config)
        self.assertEqual(result["draft_count"], 7)
        self.assertEqual(self.fake.paths(), [TOKEN, COUNT])
        self.assertEqual(self.fake.calls[-1]["request"].get_method(), "GET")
        self.assertNotIn("写入权限", result["note"])

    def test_explicit_expired_token_retries_once_but_never_force_refreshes(self):
        self.fake.overrides[ADD] = [{"errcode": 42001, "errmsg": "token expired"}, None]
        result = self.send()
        self.assertEqual(result["status"], "draft")
        self.assertEqual(self.fake.paths().count(ADD), 2)
        self.assertEqual(self.fake.paths().count(TOKEN), 2)
        for call in self.fake.calls:
            if call["path"] == TOKEN:
                self.assertIs(call["payload"]["force_refresh"], False)

    def test_failed_third_inline_upload_never_uploads_cover_or_creates_draft(self):
        self.fake.overrides[UPLOAD] = [None, None, {"errcode": 40005, "errmsg": "bad media"}]
        with self.assertRaises(DeliveryError) as caught:
            self.send()
        self.assertFalse(caught.exception.uncertain)
        self.assertEqual(self.fake.paths(), [TOKEN, UPLOAD, UPLOAD, UPLOAD])

    def test_upload_timeout_and_invalid_receipts_stop_before_draft(self):
        cases = [(UPLOAD, TimeoutError("secret endpoint")), (UPLOAD, {"url": "https://evil.example/image.png"}),
                 (UPLOAD, {}), (MATERIAL, TimeoutError("unknown upload result")), (MATERIAL, {})]
        for endpoint, response in cases:
            with self.subTest(endpoint=endpoint, response=response):
                self.fake.calls.clear()
                self.fake.overrides = {endpoint: response}
                with self.assertRaises(DeliveryError) as caught:
                    self.send()
                self.assertFalse(caught.exception.uncertain)
                self.assertNotIn(ADD, self.fake.paths())
                self.assertNotIn("secret endpoint", str(caught.exception))

    def test_draft_add_unknown_result_is_uncertain_without_automatic_replay(self):
        cases = [TimeoutError("network secret"), urllib.error.URLError("network secret"),
                 http.client.IncompleteRead(b"{"), b"not-json", b"{\xff}", b"[" * 5000 + b"]" * 5000, [],
                 {}, {"errcode": -1}, {"errcode": "0"}, {"media_id": None}]
        for response in cases:
            with self.subTest(response=response):
                self.fake.calls.clear()
                # Lists normally mean a response sequence to FakeWeChat.
                self.fake.overrides = {ADD: (lambda req, payload, value=response: value)}
                with self.assertRaises(DeliveryError) as caught:
                    self.send()
                self.assertTrue(caught.exception.uncertain)
                self.assertEqual(self.fake.paths().count(ADD), 1)
                self.assertNotIn(GET, self.fake.paths())
                self.assertNotIn("network secret", str(caught.exception))

    def test_http_failure_distinguishes_rejected_and_uncertain_draft(self):
        for status, uncertain in ((400, False), (401, False), (403, False), (429, False), (408, True), (500, True), (502, True)):
            with self.subTest(status=status):
                self.fake.calls.clear()
                self.fake.overrides = {ADD: urllib.error.HTTPError("https://api.weixin.qq.com/?access_token=SECRET", status, "SECRET", {}, None)}
                with self.assertRaises(DeliveryError) as caught:
                    self.send()
                self.assertEqual(caught.exception.uncertain, uncertain)
                self.assertNotIn("SECRET", str(caught.exception))
                self.assertEqual(self.fake.paths().count(ADD), 1)

    def test_explicit_rejection_is_failed_and_does_not_expose_raw_errmsg(self):
        self.fake.overrides[ADD] = {"errcode": 48001, "errmsg": "access_token=SECRET raw details"}
        with self.assertRaises(DeliveryError) as caught:
            self.send()
        self.assertFalse(caught.exception.uncertain)
        self.assertIn("48001", str(caught.exception))
        self.assertNotIn("SECRET", str(caught.exception))

    def test_readback_failure_keeps_created_media_id(self):
        for response in (TimeoutError("read timeout"), {"errcode": 40007}, b"malformed", {"news_item": []}):
            with self.subTest(response=response):
                self.fake.calls.clear()
                self.fake.overrides = {GET: response}
                result = self.send()
                self.assertEqual(result["status"], "uncertain")
                self.assertFalse(result["verified"])
                self.assertEqual(result["external_id"], "draft-media-id")
                self.assertEqual(self.fake.paths().count(ADD), 1)
                self.assertIn("勿重新发送", result["note"])

    def test_unexpected_local_readback_error_does_not_discard_receipt(self):
        self.fake.overrides[GET] = RuntimeError("unexpected bug with SECRET")
        result = self.send()
        self.assertEqual(result["status"], "uncertain")
        self.assertEqual(result["external_id"], "draft-media-id")
        self.assertNotIn("SECRET", result["note"])
        self.assertEqual(self.fake.paths().count(ADD), 1)

    def test_readback_detects_changed_title_body_images_and_cover(self):
        mutations = [lambda a: a.update(title="另一篇文章"), lambda a: a.update(author="别人"),
                     lambda a: a.update(digest="不同摘要"), lambda a: a.update(content=a["content"].replace("真实文字", "篡改文字")),
                     lambda a: a.update(content=a["content"].replace("<img ", "<span ", 1)),
                     lambda a: a.update(content=a["content"].replace("image-1/0", "image-other/0")),
                     lambda a: a.update(thumb_media_id="different-cover")]
        for mutate in mutations:
            self.fake.uploads = 0
            def readback(req, payload):
                article = copy.deepcopy(self.fake.articles[0])
                mutate(article)
                return {"news_item": [article]}
            self.fake.overrides[GET] = readback
            result = self.send()
            self.assertEqual(result["status"], "uncertain")
            self.assertEqual(result["external_id"], "draft-media-id")

    def test_readback_preserves_program_indentation_and_english_word_spaces(self):
        for body, original, changed in (("```python\nif ready:\n    run()\ncleanup()\n```", "<br>cleanup()", "<br>&nbsp;&nbsp;&nbsp;&nbsp;cleanup()"),
                                        ("Read the source", "the source", "thesource")):
            with self.subTest(body=body):
                self.post["body"] = body
                def readback(req, payload):
                    article = copy.deepcopy(self.fake.articles[0])
                    article["content"] = article["content"].replace(original, changed)
                    return {"news_item": [article]}
                self.fake.overrides[GET] = readback
                self.assertEqual(self.send()["status"], "uncertain")

    def test_readback_allows_harmless_html_attribute_changes(self):
        def readback(req, payload):
            article = copy.deepcopy(self.fake.articles[0])
            article["content"] = article["content"].replace("<p ", '<p data-layout="wechat" ').replace("https://mmbiz", "http://mmbiz")
            return {"news_item": [article]}
        self.fake.overrides[GET] = readback
        self.assertEqual(self.send()["status"], "draft")

    def test_readback_accepts_observed_wechat_0_to_640_rendition_rewrite(self):
        def upload(req, payload):
            self.fake.uploads += 1
            return {"url": f"http://mmbiz.qpic.cn/mmbiz_png/realAsset{self.fake.uploads}/0?from=appmsg"}

        def readback(req, payload):
            article = copy.deepcopy(self.fake.articles[0])
            article["content"] = article["content"].replace("http://mmbiz", "https://mmbiz").replace("/0?from=appmsg", "/640?from=appmsg")
            return {"news_item": [article]}

        self.fake.overrides.update({UPLOAD: upload, GET: readback})
        result = self.send()
        self.assertEqual(result["status"], "draft")
        self.assertTrue(result["verified"])
        self.assertTrue(all("/0?from=appmsg" in src for src in result["verification"]["image_urls"]))
        rechecked = wechat.verify_draft("draft-media-id", self.post, self.config, self.data_dir, expected_receipt=result["verification"])
        self.assertEqual(rechecked["status"], "draft")
        identity = wechat._cdn_identity("https://mmbiz.qpic.cn/mmbiz_png/realAsset1/640?from=appmsg")
        for src in ("https://mmbiz.qpic.cn/mmbiz_png/otherAsset/640?from=appmsg",
                    "https://mmbiz.qpic.cn/mmbiz_png/realAsset1/1080?from=appmsg",
                    "https://mmbiz.qpic.cn/mmbiz_png/realAsset1/640?from=different"):
            self.assertNotEqual(identity, wechat._cdn_identity(src))

    def test_inline_typesetting_preserves_text_and_code(self):
        markdown = "## 已批准的小标题\n\n正文包含 **强调** 和 *普通强调*。\n\n*图 1｜既有图片说明。*\n\n> 引用内容\n\n```python\nif ready:\n    run()\ncleanup()\n```"
        plain = wechat.render(markdown)
        styled = wechat._inline_styles(plain)
        self.assertEqual(wechat._HTMLShape(plain).text, wechat._HTMLShape(styled).text)
        self.assertIn("font-size:16px;line-height:1.8", styled)
        self.assertIn("font-size:13px", styled)
        self.assertIn("color:#143E35", styled)
        self.assertEqual(styled.count("已批准的小标题"), 1)
        self.assertIn("<br>&nbsp;&nbsp;&nbsp;&nbsp;run()<br>cleanup()", styled)

    def test_editor_newline_conversion_cannot_insert_blank_list_items(self):
        self.post['body'] = '1. first\n2. second\n3. third\n4. fourth'
        def readback(req, payload):
            article = copy.deepcopy(self.fake.articles[0])
            # Reproduce the editor interpreting source newlines as empty rows.
            article['content'] = article['content'].replace('\n', '<li><br></li>')
            return {'news_item': [article]}
        self.fake.overrides[GET] = readback
        self.assertEqual(self.send()['status'], 'draft')
        self.assertEqual(wechat._HTMLShape(self.fake.articles[0]['content']).structure, ['ol'] + ['li'] * 4)

    def test_external_reference_survives_anchor_filtering(self):
        self.post['body'] = '[源码](https://github.com/example/repo?a=1&b=2)'
        result = self.send()
        content = self.fake.articles[0]['content']
        self.assertEqual(result['status'], 'draft')
        self.assertNotIn('<a ', content)
        self.assertIn('https://github.com/example/repo?a=1&amp;b=2', content)
        self.assertIn('复制到浏览器打开', content)

    def test_readback_detects_lost_links_and_extra_blank_items(self):
        self.post['body'] = '[微信文章](https://mp.weixin.qq.com/s/example)\n\n1. first\n2. second'
        for old, new in (('href="https://mp.weixin.qq.com/s/example"', 'href=""'),
                         ('</ol>', '<li><br></li></ol>')):
            with self.subTest(old=old):
                def readback(req, payload):
                    article = copy.deepcopy(self.fake.articles[0])
                    article['content'] = article['content'].replace(old, new)
                    return {'news_item': [article]}
                self.fake.overrides[GET] = readback
                self.assertEqual(self.send()['status'], 'uncertain')

    def test_recheck_known_draft_is_readonly_and_preserves_media_id_on_failure(self):
        receipt = self.send()["verification"]
        self.fake.calls.clear()
        result = wechat.verify_draft("draft-media-id", self.post, self.config, self.data_dir, expected_receipt=receipt)
        self.assertEqual(result["status"], "draft")
        self.assertEqual(self.fake.paths(), [GET])
        self.assertEqual(result["verification"], receipt)
        self.fake.calls.clear()
        self.fake.overrides[GET] = TimeoutError("read failed")
        result = wechat.verify_draft("draft-media-id", self.post, self.config, self.data_dir, expected_receipt=receipt)
        self.assertEqual(result["external_id"], "draft-media-id")
        self.assertEqual(result["status"], "uncertain")
        self.assertEqual(self.fake.paths(), [GET])

    def test_recheck_without_receipt_cannot_claim_full_verification(self):
        self.send()
        self.fake.calls.clear()
        result = wechat.verify_draft("draft-media-id", self.post, self.config, self.data_dir)
        self.assertEqual(result["status"], "uncertain")
        self.assertFalse(result["verified"])
        self.assertIn("缺少原始图片上传回执", result["note"])
        self.assertEqual(self.fake.paths(), [GET])

    def test_recheck_cannot_clear_an_image_mismatch_with_original_receipt(self):
        def readback(req, payload):
            article = copy.deepcopy(self.fake.articles[0])
            article["thumb_media_id"] = "wrong-cover"
            article["content"] = article["content"].replace("image-1/0", "another-image/0")
            return {"news_item": [article]}
        self.fake.overrides[GET] = readback
        result = self.send()
        self.assertEqual(result["status"], "uncertain")
        self.assertEqual(result["verification"]["thumb_media_id"], "cover-media-id")
        self.fake.calls.clear()
        post = {**self.post, "wechat_receipt": result["verification"]}
        result = wechat.verify_draft("draft-media-id", post, self.config, self.data_dir)
        self.assertEqual(result["status"], "uncertain")
        self.assertFalse(result["verified"])
        self.assertEqual(self.fake.paths(), [GET])

    def test_invalid_draft_id_and_bad_count_are_rejected(self):
        for media_id in ("", "\nsecret", None, "x" * 513):
            with self.subTest(media_id=media_id), self.assertRaises(DeliveryError):
                wechat.verify_draft(media_id, self.post, self.config, self.data_dir)
        self.builder.assert_not_called()
        for count in (None, True, -1, "7"):
            self.fake.overrides[COUNT] = {"total_count": count}
            with self.subTest(count=count), self.assertRaises(DeliveryError):
                wechat.test_connection(self.config)

    def test_no_publish_or_mass_send_endpoint_exists_even_with_publish_config(self):
        result = wechat.send_draft(self.post, {"publish": True, "mode": "api"}, self.config, self.data_dir)
        self.assertEqual(result["status"], "draft")
        self.assertTrue(set(self.fake.paths()) <= {TOKEN, UPLOAD, MATERIAL, ADD, GET, COUNT})
        for path in ("/cgi-bin/freepublish/submit", "/cgi-bin/message/mass/sendall", "/cgi-bin/draft/update"):
            with self.subTest(path=path), self.assertRaises(DeliveryError):
                wechat.WeChatClient(self.config)._request(path, {})

    def test_explicit_maintenance_update_still_cannot_publish_or_delete(self):
        self.fake.overrides['/cgi-bin/draft/update']={'errcode':0}
        client=wechat.WeChatClient(self.config,allow_draft_update=True)
        self.assertEqual(client.api('/cgi-bin/draft/update',{'media_id':'known','index':0,'articles':{}})['errcode'],0)
        for path in ('/cgi-bin/freepublish/submit','/cgi-bin/message/mass/sendall','/cgi-bin/draft/delete'):
            with self.subTest(path=path),self.assertRaises(DeliveryError):client.api(path,{})


if __name__ == "__main__":
    unittest.main()
