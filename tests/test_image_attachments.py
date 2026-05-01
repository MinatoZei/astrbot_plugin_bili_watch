import sys
import types
import unittest
from pathlib import Path


PLUGIN_ROOT = Path(__file__).resolve().parents[1]
WORKSPACE_ROOT = PLUGIN_ROOT.parent
if str(WORKSPACE_ROOT) not in sys.path:
    sys.path.insert(0, str(WORKSPACE_ROOT))


class FakePlain:
    def __init__(self, text):
        self.text = text


class FakeImage:
    def __init__(self, url=None):
        self.url = url

    @classmethod
    def fromURL(cls, url):
        return cls(url=url)


class FakeAtAll:
    pass


class FakeMessageEventResult:
    def __init__(self, chain=None):
        self.chain = chain or []
        self.t2i = None

    def use_t2i(self, enabled):
        self.t2i = enabled
        return self


class FakeMessageChain:
    def __init__(self):
        self.chain = []

    def message(self, text):
        self.chain.append(FakePlain(text))
        return self

    def file_image(self, path):
        self.chain.append(FakeImage(url=path))
        return self


class FakeLogger:
    def info(self, *args, **kwargs):
        pass

    def warning(self, *args, **kwargs):
        pass

    def error(self, *args, **kwargs):
        pass


class FakeStarTools:
    @staticmethod
    def get_data_dir(plugin_name):
        return str(PLUGIN_ROOT / ".test-data" / plugin_name)


def _install_dependency_stubs():
    astrbot = types.ModuleType("astrbot")
    api = types.ModuleType("astrbot.api")
    api.logger = FakeLogger()
    api_event = types.ModuleType("astrbot.api.event")
    api_event.MessageEventResult = FakeMessageEventResult
    api_event.MessageChain = FakeMessageChain
    api_message_components = types.ModuleType("astrbot.api.message_components")
    api_message_components.AtAll = FakeAtAll
    api_message_components.Image = FakeImage
    api_message_components.Plain = FakePlain
    api_star = types.ModuleType("astrbot.api.star")
    api_star.Context = object
    api_star.StarTools = FakeStarTools
    sys.modules.setdefault("astrbot", astrbot)
    sys.modules["astrbot.api"] = api
    sys.modules["astrbot.api.event"] = api_event
    sys.modules["astrbot.api.message_components"] = api_message_components
    sys.modules["astrbot.api.star"] = api_star

    bilibili_api = types.ModuleType("bilibili_api")
    bilibili_api.Credential = object
    bilibili_api.comment = types.SimpleNamespace()
    bilibili_api.request_settings = types.SimpleNamespace(set_proxy=lambda proxy: None)
    bilibili_api.user = types.SimpleNamespace(User=object)
    network = types.ModuleType("bilibili_api.utils.network")
    network.Api = object
    sys.modules["bilibili_api"] = bilibili_api
    sys.modules.setdefault("bilibili_api.utils", types.ModuleType("bilibili_api.utils"))
    sys.modules["bilibili_api.utils.network"] = network


_install_dependency_stubs()

from plugin.services.comment_listener import CommentListener
from plugin.services.listener import DynamicListener


def _forward_video_item():
    return {
        "id_str": "992178062899544081",
        "type": "DYNAMIC_TYPE_FORWARD",
        "modules": {
            "module_author": {"name": "转发作者"},
            "module_dynamic": {"desc": {"text": "不赖"}},
        },
        "orig": {
            "type": "DYNAMIC_TYPE_AV",
            "modules": {
                "module_author": {"name": "原作者"},
                "module_dynamic": {
                    "major": {
                        "archive": {
                            "title": "原视频",
                            "cover": "http://i0.hdslb.com/bfs/archive/cover.jpg",
                            "bvid": "BV1xx411c7mD",
                        }
                    }
                },
            },
        },
    }


def _unwrap_chain(sent_payload):
    return getattr(sent_payload, "chain", [])


class FakeContext:
    def __init__(self):
        self.sent = []

    async def send_message(self, sub_user, payload):
        self.sent.append((sub_user, payload))


class ImageAttachmentTests(unittest.TestCase):
    def test_forward_video_origin_keeps_cover_image(self):
        forward = _forward_video_item()
        listener = DynamicListener(None, None, None, {"forward_image_limit": 1})

        result = listener._handle_forward_dynamic(forward, forward["id_str"], "563787", [], [])

        self.assertTrue(result.has_payload())
        self.assertEqual(result.payload.forward.image_urls, ["http://i0.hdslb.com/bfs/archive/cover.jpg"])

    def test_forward_image_limit_zero_removes_origin_images(self):
        forward = _forward_video_item()
        listener = DynamicListener(None, None, None, {"forward_image_limit": 0})

        result = listener._handle_forward_dynamic(forward, forward["id_str"], "563787", [], [])

        self.assertTrue(result.has_payload())
        self.assertEqual(result.payload.forward.image_urls, [])

    def test_comment_picture_urls_are_extracted_from_img_src_and_url(self):
        reply = {
            "content": {
                "pictures": [
                    {"img_src": "https://i0.hdslb.com/bfs/comment/a.jpg"},
                    {"url": "https://i0.hdslb.com/bfs/comment/b.jpg"},
                    {"img_src": ""},
                    {},
                ]
            }
        }

        self.assertEqual(
            CommentListener._extract_comment_image_urls(reply),
            [
                "https://i0.hdslb.com/bfs/comment/a.jpg",
                "https://i0.hdslb.com/bfs/comment/b.jpg",
            ],
        )


class CommentPushImageTests(unittest.IsolatedAsyncioTestCase):
    async def test_level_one_comment_push_appends_reply_images(self):
        context = FakeContext()
        listener = CommentListener(context, None, None, {})
        item = {
            "id_str": "10001",
            "modules": {"module_author": {"name": "测试UP"}},
        }
        reply = {
            "rpid": 20001,
            "ctime": 1700000000,
            "content": {
                "message": "这条评论带图",
                "pictures": [{"img_src": "https://i0.hdslb.com/bfs/comment/pic.jpg"}],
            },
        }

        await listener._push("test:GroupMessage:1", 12345, 1, reply, None, "10001", 11, item)

        chain = _unwrap_chain(context.sent[0][1])
        images = [part for part in chain if isinstance(part, FakeImage)]
        self.assertEqual([image.url for image in images], ["https://i0.hdslb.com/bfs/comment/pic.jpg"])

    async def test_subreply_push_appends_only_subreply_images(self):
        context = FakeContext()
        listener = CommentListener(context, None, None, {})
        item = {
            "id_str": "10001",
            "modules": {"module_author": {"name": "测试UP"}},
        }
        parent = {
            "rpid": 30001,
            "member": {"uname": "粉丝"},
            "content": {
                "message": "父评论",
                "pictures": [{"img_src": "https://i0.hdslb.com/bfs/comment/parent.jpg"}],
            },
        }
        reply = {
            "rpid": 30002,
            "ctime": 1700000000,
            "content": {
                "message": "楼中楼带图",
                "pictures": [{"img_src": "https://i0.hdslb.com/bfs/comment/sub.jpg"}],
            },
        }

        await listener._push("test:GroupMessage:1", 12345, 2, reply, parent, "10001", 11, item)

        chain = _unwrap_chain(context.sent[0][1])
        images = [part for part in chain if isinstance(part, FakeImage)]
        self.assertEqual([image.url for image in images], ["https://i0.hdslb.com/bfs/comment/sub.jpg"])


if __name__ == "__main__":
    unittest.main()
