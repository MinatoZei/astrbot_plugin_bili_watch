import asyncio
import os
import tempfile
from typing import List, Tuple

from astrbot.api import AstrBotConfig, logger
from astrbot.api.all import *
from astrbot.api.event import AstrMessageEvent, MessageChain, MessageEventResult
from astrbot.api.event.filter import (
    PermissionType,
    command,
    permission_type,
)
from astrbot.core.star.filter.command import GreedyStr
from bilibili_api import login_v2

from .bili_client import BiliClient
from .core.constant import (
    LIVE_ATALL_OPTION,
    PLUGIN_NAME,
    RECENT_DYNAMIC_CACHE,
    VALID_FILTER_TYPES,
    VALID_SUB_OPTIONS,
)
from .core.data_manager import DataManager
from .core.models import SubscriptionRecord
from .core.utils import is_valid_umo
from .core.models import RenderPayload  # noqa: F401  (sub_test 里用作类型注释)
from .services.comment_listener import CommentListener
from .services.listener import DynamicListener
from .services.subscription_service import SubscriptionService


@register(PLUGIN_NAME, "based on Flartiny & Soulter", "", "", "")
class Main(Star):
    def __init__(self, context: Context, config: AstrBotConfig) -> None:
        super().__init__(context)
        self.cfg = config
        self.context = context

        self.proxy = (self.cfg.get("proxy", "") or "").strip()

        self.data_manager = DataManager(
            recent_dynamic_cache=self.cfg.get(
                "recent_dynamic_cache", RECENT_DYNAMIC_CACHE
            )
        )

        # 优先使用 DataManager 中的凭据
        saved_credential = self.data_manager.get_credential()
        if saved_credential:
            self.bili_client = BiliClient(
                credential_dict=saved_credential, proxy=self.proxy
            )
        else:
            self.bili_client = BiliClient(
                sessdata=self.cfg.get("sessdata"), proxy=self.proxy
            )

        self.dynamic_listener = DynamicListener(
            context=self.context,
            data_manager=self.data_manager,
            bili_client=self.bili_client,
            cfg=self.cfg,
        )
        self.comment_listener = CommentListener(
            context=self.context,
            data_manager=self.data_manager,
            bili_client=self.bili_client,
            cfg=self.cfg,
        )
        self.subscription_service = SubscriptionService(
            data_manager=self.data_manager,
            bili_client=self.bili_client,
            parse_dynamics=self.dynamic_listener._parse_and_filter_dynamics,
        )
        self._start_tasks()

    def _start_tasks(self):
        """启动或重启后台任务。"""
        for attr in ("dynamic_listener_task", "comment_listener_task"):
            t = getattr(self, attr, None)
            if t and not t.done():
                t.cancel()

        self.dynamic_listener_task = asyncio.create_task(self.dynamic_listener.start())
        self.comment_listener_task = asyncio.create_task(self.comment_listener.start())

    @staticmethod
    def _parse_sub_args(input_text: GreedyStr) -> tuple[List[str], List[str], bool]:
        args = input_text.strip().split(" ") if input_text.strip() else []
        filter_types: List[str] = []
        filter_regex: List[str] = []
        live_atall = False

        for arg in args:
            if arg in VALID_SUB_OPTIONS:
                if arg == LIVE_ATALL_OPTION:
                    live_atall = True
                continue
            if arg in VALID_FILTER_TYPES:
                filter_types.append(arg)
            else:
                filter_regex.append(arg)

        return filter_types, filter_regex, live_atall

    async def _apply_subscription(
        self,
        sub_user: str,
        uid_int: int,
        filter_types: List[str],
        filter_regex: List[str],
        live_atall: bool,
    ) -> Tuple[bool, str]:
        result = await self.subscription_service.add_or_update(
            sub_user, uid_int, filter_types, filter_regex, live_atall
        )
        if result.updated:
            option_desc = "开启" if live_atall else "关闭"
            return True, f"该动态已订阅，已更新过滤条件。直播@全体: {option_desc}"
        return False, ""

    @command("biliw_login")
    @permission_type(PermissionType.ADMIN)
    async def bili_login(self, event: AstrMessageEvent):
        """扫码登录 Bilibili。"""
        if event.get_group_id():
            return MessageEventResult().message(
                "仅支持管理员在私聊中使用'/biliw_login'指令。"
            )

        login_obj = login_v2.QrCodeLogin()
        await login_obj.generate_qrcode()

        # 获取二维码图片路径
        qr_path = os.path.join(tempfile.gettempdir(), "qrcode.png")

        await event.send(
            MessageChain()
            .message("请使用 Bilibili App 扫描下方二维码登录：")
            .file_image(qr_path)
        )

        # 轮询状态
        try:
            while True:
                state = await login_obj.check_state()
                if state == login_v2.QrCodeLoginEvents.DONE:
                    credential = login_obj.get_credential()
                    # 保存凭据
                    self.bili_client.credential = credential
                    cred_dict = self.bili_client.get_credential_dict()
                    if cred_dict is not None:
                        await self.data_manager.set_credential(cred_dict)
                        self._start_tasks()
                        await event.send(MessageChain().message("✅ 登录成功！"))
                    else:
                        await event.send(
                            MessageChain().message("❌ 登录失败：无法获取凭据。")
                        )
                    break
                elif state == login_v2.QrCodeLoginEvents.TIMEOUT:
                    await event.send(
                        MessageChain().message("❌ 登录超时，请重新执行 /biliw_login。")
                    )
                    break

                await asyncio.sleep(2)
        except Exception as e:
            logger.error(f"登录过程中发生错误: {e}")
            await event.send(MessageChain().message(f"❌ 登录失败: {str(e)}"))

    @command("biliw_logout")
    @permission_type(PermissionType.ADMIN)
    async def bili_logout(self, event: AstrMessageEvent):
        """登出 Bilibili，清除凭据。"""
        self.bili_client.credential = None
        await self.data_manager.clear_credential()
        self.bili_client = BiliClient(
            sessdata=self.cfg.get("sessdata"), proxy=self.proxy
        )
        self.dynamic_listener.bili_client = self.bili_client
        self._start_tasks()
        return MessageEventResult().message("✅ 已登出 Bilibili，凭据已清除。")

    @command("biliw_sub", alias={"监控订阅"})
    async def dynamic_sub(self, event: AstrMessageEvent, uid: str, input: GreedyStr):
        filter_types, filter_regex, live_atall = self._parse_sub_args(input)

        sub_user = event.unified_msg_origin
        if not uid.isdigit():
            return MessageEventResult().message("UID 格式错误")
        uid_int = int(uid)

        updated, update_msg = await self._apply_subscription(
            sub_user, uid_int, filter_types, filter_regex, live_atall
        )
        if updated:
            return MessageEventResult().message(update_msg)

        try:
            usr_info, msg = await self.bili_client.get_user_info(int(uid))
        except Exception as e:
            logger.error(f"获取用户信息失败: {e}")
            return MessageEventResult().message(
                f"✅ 订阅成功 UID={uid_int},但获取 UP主 信息失败 (´;ω;`)"
            )
        if not usr_info:
            return MessageEventResult().message(
                f"✅ 订阅成功 UID={uid_int},但获取 UP主 信息失败: {msg}"
            )

        name = str(usr_info.get("name", "Unknown"))
        filter_lines = []
        if filter_types:
            filter_lines.append(f"过滤类型: {', '.join(filter_types)}")
        if filter_regex:
            filter_lines.append(f"过滤正则: {filter_regex}")
        filter_lines.append(f"直播开播@全体: {'开启' if live_atall else '关闭'}")
        filter_desc = "\n".join(filter_lines)

        text = (
            f"📣 订阅成功!\n"
            f"UP 主: {name} (UID={uid_int})\n"
            f"{filter_desc}\n"
            f"https://space.bilibili.com/{uid_int}"
        )
        return MessageEventResult().message(text)

    @command("biliw_sub_list", alias={"监控列表"})
    async def sub_list(self, event: AstrMessageEvent):
        """查看 bilibili 动态监控列表"""
        sub_user = event.unified_msg_origin
        ret = """订阅列表：\n"""
        subs = self.data_manager.get_subscriptions_by_user(sub_user)

        if not subs:
            return MessageEventResult().message("无订阅")
        else:
            for idx, uid_sub_data in enumerate(subs):
                uid = uid_sub_data.uid
                info, _ = await self.bili_client.get_user_info(int(uid))
                if not info:
                    ret += f"{idx + 1}. {uid} - 无法获取 UP 主信息\n"
                else:
                    name = info["name"]
                    ret += f"{idx + 1}. {uid} - {name}\n"
                filters = []
                if uid_sub_data.filter_types:
                    filters.append(f"过滤类型: {', '.join(uid_sub_data.filter_types)}")
                if uid_sub_data.filter_regex:
                    filters.append(f"过滤正则: {uid_sub_data.filter_regex}")
                if uid_sub_data.live_atall:
                    filters.append("直播@全体: live_atall")
                if filters:
                    ret += f"   {'｜'.join(filters)}\n"
            return MessageEventResult().message(ret)

    @command("biliw_sub_del", alias={"取消监控"})
    async def sub_del(self, event: AstrMessageEvent, uid: str):
        """删除 bilibili 动态监控"""
        sub_user = event.unified_msg_origin
        if not uid or not uid.isdigit():
            return MessageEventResult().message("参数错误，请提供正确的UID。")

        uid2del = int(uid)

        if await self.data_manager.remove_subscription(sub_user, uid2del):
            return MessageEventResult().message("删除成功")
        else:
            return MessageEventResult().message("未找到指定的订阅")

    @permission_type(PermissionType.ADMIN)
    @command("biliw_global_del", alias={"监控全局删除"})
    async def global_sub_del(self, event: AstrMessageEvent, umo: str = ""):
        """管理员指令。通过 UMO 删除某一个群聊或者私聊的所有订阅。"""
        if not is_valid_umo(umo):
            return MessageEventResult().message(
                "通过 UMO 删除某一个群聊或者私聊的所有订阅。使用 /sid 指令查看当前会话的 UMO 或参考 WebUI-自定义规则。"
            )

        msg = await self.data_manager.remove_all_for_user(umo)
        return MessageEventResult().message(msg)

    @permission_type(PermissionType.ADMIN)
    @command("biliw_global_sub", alias={"监控全局订阅"})
    async def global_sub_add(
        self, event: AstrMessageEvent, umo: str, uid: str, input: GreedyStr
    ):
        """管理员指令。通过 UID 添加某一个用户的所有订阅。"""
        if not is_valid_umo(umo) or not uid.isdigit():
            return MessageEventResult().message(
                "请提供正确的UMO与UID。使用 /sid 指令查看当前会话的 UMO 或参考 WebUI-自定义规则。"
            )
        filter_types, filter_regex, live_atall = self._parse_sub_args(input)
        uid_int = int(uid)

        updated, update_msg = await self._apply_subscription(
            umo, uid_int, filter_types, filter_regex, live_atall
        )
        if updated:
            return MessageEventResult().message(update_msg)
        return MessageEventResult().message(
            f"订阅完成，已为{umo}添加订阅{uid_int}，详情见日志。"
        )

    @permission_type(PermissionType.ADMIN)
    @command("biliw_global_list", alias={"监控全局列表"})
    async def global_list(self, event: AstrMessageEvent):
        """管理员指令。查看所有订阅者"""
        ret = "订阅会话列表：\n"
        all_subs = self.data_manager.get_all_subscriptions()
        if not all_subs:
            return MessageEventResult().message("没有任何会话订阅过。")

        for sub_user in all_subs:
            ret += f"- {sub_user}\n"
            for sub in all_subs[sub_user]:
                uid = sub.uid
                ret += f"  - {uid}\n"
        return MessageEventResult().message(ret)

    @command("biliw_sub_test", alias={"监控测试"})
    async def sub_test(self, event: AstrMessageEvent, uid: str):
        """测试订阅功能。仅测试获取动态与渲染图片功能，不保存订阅信息。"""
        sub_user = event.unified_msg_origin
        try:
            uid_int = int(uid)
        except (TypeError, ValueError):
            return MessageEventResult().message("UID 必须是数字。")

        dyn = await self.bili_client.get_latest_dynamics(uid_int)
        if not dyn:
            return MessageEventResult().message("未获取到动态数据，请稍后重试。")

        result_list = self.dynamic_listener._parse_and_filter_dynamics(
            dyn,
            SubscriptionRecord(uid=uid_int),
        )

        render_data: RenderPayload | None = None
        # dyn_id = None
        for result in result_list or []:
            if result.has_payload():
                render_data = result.payload
                # dyn_id = result.dyn_id
                break

        if not render_data:
            return MessageEventResult().message(
                "没有可用于测试推送的动态（可能没有新动态、都被过滤掉，或动态类型暂不支持）。"
            )

        # 测试命令需要每次基于当前代码重新构造消息，避免命中同 dyn_id 的历史缓存。
        await self.dynamic_listener._handle_new_dynamic(sub_user, render_data, None)
        event.stop_event()

    @command("biliw_comment_test", alias={"监控评论测试"})
    async def bili_comment_test(self, event: AstrMessageEvent, uid: str):
        """强制扫一次 UP主 的评论(忽略 rpid 缓存,所有命中的 UP主自评全部推送)。"""
        try:
            uid_int = int(uid)
        except (TypeError, ValueError):
            return MessageEventResult().message("UID 必须是数字。")
        sub_user = event.unified_msg_origin

        # 临时构造一个 record(避免污染真实订阅)
        tmp_rec = SubscriptionRecord(uid=uid_int)
        # 拉动态
        dyn = await self.bili_client.get_latest_dynamics(uid_int)
        items = (dyn or {}).get("items") or []
        if not items:
            return MessageEventResult().message("没有可扫描的动态。")
        items = items[: self.comment_listener.scope_recent_n]

        await event.send(MessageChain().message(f"开始扫描 {len(items)} 条最近动态的评论..."))
        hits = 0
        _was_skip = self.comment_listener.skip_unchanged
        self.comment_listener.skip_unchanged = False
        try:
            for item in items:
                try:
                    await self.comment_listener._scan_one_dynamic(
                        sub_user, tmp_rec, item, uid_int
                    )
                except Exception as e:
                    logger.error(f"comment_test 异常: {e}")
                hits += len(tmp_rec.notified_rpids)
        finally:
            self.comment_listener.skip_unchanged = _was_skip
        return MessageEventResult().message(f"扫描完成,UP 主自评 {hits} 条已尝试推送(若无推送可能命中持久化去重)。")

    @command("biliw_status", alias={"监控状态"})
    async def bili_status(self, event: AstrMessageEvent):
        """查看插件状态:凭据/订阅总数/上次扫描/冷却剩余。"""
        cred_ok = self.bili_client.credential is not None
        try:
            valid = await self.bili_client.credential.check_valid() if cred_ok else False
        except Exception:
            valid = False
        all_subs = self.data_manager.get_all_subscriptions() or {}
        sub_users = len(all_subs)
        sub_total = sum(len(v) for v in all_subs.values())
        cooldown_left = max(0, int(self.comment_listener.cooldown_until - asyncio.get_event_loop().time()))
        cl = self.comment_listener
        adapt = ""
        if cl.current_interval_secs != cl.interval_secs:
            adapt = f" → 当前 {cl.current_interval_secs}s (自适应)"
        ret = (
            f"📊 B站监控插件状态\n"
            f"  凭据: {'✅ 有效' if valid else '❌ 无效或未登录'}\n"
            f"  订阅会话: {sub_users}\n"
            f"  订阅 UP主 总条数: {sub_total}\n"
            f"  动态周期: {self.cfg.get('interval_secs', 300)}s\n"
            f"  评论周期: {cl.interval_secs}s{adapt}\n"
            f"  评论参数: 双 mode {'开' if cl.dual_mode else '关'} | "
            f"跳过未变动 {'开' if cl.skip_unchanged else '关'}\n"
            f"  自适应: 连续风控 {cl.consecutive_block} 次 / 连续成功 {cl.consecutive_success} 次\n"
            f"  评论扫描冷却剩余: {cooldown_left}s"
        )
        return MessageEventResult().message(ret)

    async def terminate(self):
        for attr in ("dynamic_listener_task", "comment_listener_task"):
            t = getattr(self, attr, None)
            if t and not t.done():
                t.cancel()
                try:
                    await t
                except asyncio.CancelledError:
                    logger.info(f"{attr} cancelled during terminate.")
                except Exception as e:
                    logger.error(f"Error cancelling {attr}: {e}")
