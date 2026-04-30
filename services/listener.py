"""
B站 UP主 动态/直播 监控主循环。纯文本 + 图片附件推送,不做卡片渲染。
"""

import asyncio
import re
import time
import traceback
from collections import OrderedDict
from typing import Any, Dict, List, Optional, Tuple

from astrbot.api import logger
from astrbot.api.event import MessageEventResult
from astrbot.api.message_components import AtAll, Image, Plain
from astrbot.api.star import Context

from ..bili_client import BiliClient
from ..core.data_manager import DataManager
from ..core.models import DynamicParseResult, RenderPayload, SubscriptionRecord
from ..core.utils import render_text_to_plain


PLAIN_PUSH_ACTIONS = {
    "DYNAMIC_TYPE_AV": "投稿了新视频",
    "DYNAMIC_TYPE_ARTICLE": "发布了新专栏",
    "DYNAMIC_TYPE_DRAW": "发布了新图文动态",
    "DYNAMIC_TYPE_FORWARD": "转发了新动态",
    "DYNAMIC_TYPE_WORD": "发布了新动态",
}
VIDEO_BODY_PREFIX = "投稿了新视频"
GROUP_MESSAGE_TYPE = "GroupMessage"
MIN_AT_ALL_REMAINING = 1
SECONDS_PER_MINUTE = 60
SECONDS_PER_HOUR = 3600


class DynamicListener:
    """后台轮询 B站 动态 + 直播,纯文本推送。"""

    def __init__(
        self,
        context: Context,
        data_manager: DataManager,
        bili_client: BiliClient,
        cfg: dict,
    ):
        self.context = context
        self.data_manager = data_manager
        self.bili_client = bili_client
        self.interval_secs = max(1, int(cfg.get("interval_secs", 300)))
        self.task_gap_secs = self._parse_float(cfg.get("task_gap_secs"), 20, minimum=0)
        self.dynamic_limit = int(cfg.get("dynamic_limit", 5))
        self.forward_image_limit = max(0, int(cfg.get("forward_image_limit", 1)))
        self.render_cache: OrderedDict[str, list] = OrderedDict()
        self.render_cache_limit = int(cfg.get("render_cache_limit", 32))

    @staticmethod
    def _parse_float(value: Any, default: float, minimum: float = 0) -> float:
        try:
            parsed = float(value)
        except (TypeError, ValueError):
            return default
        return max(parsed, minimum)

    # ============= 主循环 =============
    async def start(self):
        uid_states: Dict[int, float] = {}
        next_dispatch_at = 0.0

        while True:
            try:
                if self.bili_client.credential is None:
                    logger.warning(
                        "Bilibili 凭据未设置,无法获取动态。请使用 /biliw_login 登录或在配置中设置 sessdata。"
                    )
                    await asyncio.sleep(self.interval_secs)
                    continue

                uid_targets = self._build_uid_targets()
                current_uids = set(uid_targets.keys())
                now = time.monotonic()

                for uid in list(uid_states):
                    if uid not in current_uids:
                        uid_states.pop(uid, None)

                for uid in current_uids:
                    uid_states.setdefault(uid, now)

                if not current_uids:
                    await asyncio.sleep(2)
                    continue

                due_uids = [uid for uid in current_uids if uid_states[uid] <= now]
                if not due_uids:
                    next_due_at = min(uid_states[uid] for uid in current_uids)
                    wait_secs = min(max(next_due_at - now, 0.2), 2.0)
                    await asyncio.sleep(wait_secs)
                    continue

                if now < next_dispatch_at:
                    wait_secs = min(max(next_dispatch_at - now, 0.2), 2.0)
                    await asyncio.sleep(wait_secs)
                    continue

                run_uid = min(due_uids, key=lambda uid: (uid_states[uid], uid))
                await self._run_uid_task(run_uid, uid_targets.get(run_uid, []))

                finished_at = time.monotonic()
                uid_states[run_uid] = finished_at + self.interval_secs
                next_dispatch_at = finished_at + self.task_gap_secs
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.error(f"UID任务池调度异常: {e}\n{traceback.format_exc()}")
                await asyncio.sleep(1)

    def _build_uid_targets(self) -> Dict[int, List[Tuple[str, SubscriptionRecord]]]:
        uid_targets: Dict[int, List[Tuple[str, SubscriptionRecord]]] = {}
        all_subs = self.data_manager.get_all_subscriptions()
        for sub_user, sub_list in (all_subs or {}).items():
            for sub_data in sub_list or []:
                try:
                    uid_int = int(sub_data.uid)
                except (TypeError, ValueError):
                    continue
                uid_targets.setdefault(uid_int, []).append((sub_user, sub_data))
        return uid_targets

    async def _run_uid_task(
        self, uid: int, targets: List[Tuple[str, SubscriptionRecord]]
    ) -> None:
        if not targets:
            return
        try:
            dyn = await self.bili_client.get_latest_dynamics(uid)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.error(f"拉取 UID={uid} 动态失败: {e}")
            dyn = None

        should_check_live = any(
            "live" not in sub_data.filter_types for _, sub_data in targets
        )
        live_room = None
        if should_check_live:
            try:
                live_room = await self.bili_client.get_live_info_by_uids([uid])
            except Exception as e:
                logger.error(f"拉取 UID={uid} 直播状态失败: {e}")
                live_room = None

        for sub_user, sub_data in targets:
            try:
                await self._check_single_up(sub_user, sub_data, dyn, live_room)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.error(
                    f"处理订阅者 {sub_user} 的 UP主 {sub_data.uid} 时异常: {e}\n{traceback.format_exc()}"
                )

    async def _check_single_up(
        self,
        sub_user: str,
        sub_data: SubscriptionRecord,
        dyn: Optional[Dict[str, Any]],
        live_room: Optional[Dict[str, Any]],
    ):
        uid = int(sub_data.uid)
        if dyn:
            result_list = self._parse_and_filter_dynamics(dyn, sub_data)
            sent = 0
            for result in reversed(result_list):
                if result.has_payload():
                    if sent < self.dynamic_limit:
                        sent += 1
                        await self._handle_new_dynamic(sub_user, result.payload, result.dyn_id)
                    if result.dyn_id:
                        await self.data_manager.update_last_dynamic_id(sub_user, uid, result.dyn_id)
                elif result.dyn_id:
                    await self.data_manager.update_last_dynamic_id(sub_user, uid, result.dyn_id)

        if "live" in sub_data.filter_types:
            return
        if live_room:
            await self._handle_live_status(sub_user, sub_data, live_room)

    # ============= 推送(纯文本 + 图片附件) =============
    def _build_chain(self, payload: RenderPayload, nested: bool = False) -> list:
        chain: list = []
        name = (payload.name or "").strip() or "未知作者"
        action = PLAIN_PUSH_ACTIONS.get(payload.type or "", "发布了新动态")
        subject = "原动态作者" if nested else "UP 主"
        header = f"📣 {subject}「{name}」 {action}"

        text = (payload.summary or "").strip()
        if not text:
            text = render_text_to_plain(payload.text or "")
        if payload.type == "DYNAMIC_TYPE_AV" and text.startswith(VIDEO_BODY_PREFIX):
            text = text.removeprefix(VIDEO_BODY_PREFIX).strip()

        body_parts = [header]
        if payload.title:
            body_parts.append(f"标题: {payload.title}")
        if text:
            body_parts.append(text)
        chain.append(Plain("\n".join(body_parts)))

        for pic in (payload.image_urls or []):
            if pic:
                chain.append(Image.fromURL(pic))

        if payload.forward:
            fwd_chain = self._build_chain(self._forward_to_payload(payload.forward), nested=True)
            chain.append(Plain("​\n转发内容:"))
            chain.extend(fwd_chain)

        if not nested and payload.url:
            chain.append(Plain(f"\n{payload.url}"))
        return chain

    @staticmethod
    def _forward_to_payload(forward) -> RenderPayload:
        return RenderPayload(
            name=forward.name,
            text=forward.text,
            image_urls=list(forward.image_urls),
            url=forward.url,
            title=forward.title,
            type=forward.type,
            summary=forward.summary,
            uid=forward.uid,
        )

    def _cache_render(self, dyn_id: Optional[str], chain: list):
        if not dyn_id:
            return
        self.render_cache[dyn_id] = chain
        while len(self.render_cache) > self.render_cache_limit:
            self.render_cache.popitem(last=False)

    async def _handle_new_dynamic(
        self, sub_user: str, payload: Optional[RenderPayload], dyn_id: Optional[str] = None
    ):
        if not payload:
            return
        cached = self.render_cache.get(dyn_id) if dyn_id else None
        if cached:
            await self.context.send_message(
                sub_user, MessageEventResult(chain=list(cached)).use_t2i(False)
            )
            return
        chain = self._build_chain(payload)
        await self.context.send_message(
            sub_user, MessageEventResult(chain=chain).use_t2i(False)
        )
        self._cache_render(dyn_id, chain)
        logger.info(f"动态推送: sub_user={sub_user} dyn_id={dyn_id} type={payload.type}")

    # ============= 直播状态(纯文本) =============
    @staticmethod
    def _extract_group_session(sub_user: str) -> Optional[Tuple[str, str]]:
        try:
            platform_id, message_type, session_id = sub_user.split(":", 2)
        except ValueError:
            return None
        if message_type != GROUP_MESSAGE_TYPE:
            return None
        group_id = session_id.split("_")[-1].strip()
        if not group_id:
            return None
        return platform_id, group_id

    @staticmethod
    def _extract_action_data(action_result: Any) -> Dict[str, Any]:
        if not isinstance(action_result, dict):
            return {}
        payload = action_result.get("data")
        if isinstance(payload, dict):
            return payload
        return action_result

    @staticmethod
    def _parse_live_start_timestamp(live_room: Dict[str, Any]) -> int:
        try:
            ts = int(live_room.get("live_time", 0) or 0)
        except (TypeError, ValueError):
            return 0
        return ts if ts > 0 else 0

    @staticmethod
    def _calc_live_duration_seconds(current_ts: int, live_start_ts: int) -> int:
        if current_ts <= 0 or live_start_ts <= 0 or current_ts <= live_start_ts:
            return 0
        return current_ts - live_start_ts

    @staticmethod
    def _format_live_duration_text(seconds: int) -> str:
        if seconds <= 0:
            return ""
        h = seconds // SECONDS_PER_HOUR
        m = (seconds % SECONDS_PER_HOUR) // SECONDS_PER_MINUTE
        s = seconds % SECONDS_PER_MINUTE
        if h > 0:
            return f"{h}小时{m}分钟{s}秒"
        if m > 0:
            return f"{m}分钟{s}秒"
        return f"{s}秒"

    async def _should_send_live_atall(self, sub_user: str, enabled: bool) -> bool:
        if not enabled:
            return False
        group_ctx = self._extract_group_session(sub_user)
        if not group_ctx:
            return False
        platform_id, group_id = group_ctx
        platform_inst = self.context.get_platform_inst(platform_id)
        if not platform_inst:
            return False
        client = platform_inst.get_client()
        if not client or not hasattr(client, "call_action"):
            return False
        gid: int | str = int(group_id) if group_id.isdigit() else group_id
        remain_raw = await client.call_action("get_group_at_all_remain", group_id=gid)
        remain_data = self._extract_action_data(remain_raw)
        if not bool(remain_data.get("can_at_all")):
            return False
        if int(remain_data.get("remain_at_all_count_for_group", 0) or 0) < MIN_AT_ALL_REMAINING:
            return False
        self_remain = remain_data.get(
            "remain_at_all_count_for_self",
            remain_data.get("remain_at_all_count_for_uin", 0),
        )
        if int(self_remain or 0) < MIN_AT_ALL_REMAINING:
            return False
        return True

    async def _handle_live_status(
        self, sub_user: str, sub_data: SubscriptionRecord, live_room: Dict[str, Any]
    ):
        is_live_now = live_room.get("live_status", "") == 1
        is_live_started = is_live_now and not sub_data.is_live
        is_live_ended = (not is_live_now) and sub_data.is_live
        cur_start_ts = self._parse_live_start_timestamp(live_room)
        if is_live_now and cur_start_ts > 0:
            sub_data.last_live_start_ts = cur_start_ts

        user_name = str(live_room.get("uname", "Unknown") or "Unknown")
        text = ""
        with_atall = False

        if is_live_started:
            if cur_start_ts > 0:
                sub_data.last_live_start_ts = cur_start_ts
            title = str(live_room.get("title", "") or "")
            room_id = live_room.get("room_id", 0)
            text = f"📣 你订阅的 UP 「{user_name}」 开播了!\n标题: {title}\nhttps://live.bilibili.com/{room_id}"
            with_atall = await self._should_send_live_atall(
                sub_user, bool(sub_data.live_atall)
            )
            await self.data_manager.update_live_status(sub_user, sub_data.uid, True)
        elif is_live_ended:
            cached = int(sub_data.last_live_start_ts or 0)
            start = max(cur_start_ts, cached)
            duration = self._calc_live_duration_seconds(int(time.time()), start)
            duration_text = self._format_live_duration_text(duration)
            if duration_text:
                text = f"📣 你订阅的 UP 「{user_name}」 下播了!\n本场直播时长: {duration_text}"
            else:
                text = f"📣 你订阅的 UP 「{user_name}」 下播了!"
            sub_data.last_live_start_ts = 0
            await self.data_manager.update_live_status(sub_user, sub_data.uid, False)

        if not text:
            return
        chain: list = []
        if with_atall:
            chain.append(AtAll())
            chain.append(Plain(" "))
        chain.append(Plain(text))
        await self.context.send_message(
            sub_user, MessageEventResult(chain=chain).use_t2i(False)
        )

    # ============= 动态解析 =============
    def _get_dynamic_items(self, dyn: Dict[str, Any], data: SubscriptionRecord):
        last = data.last
        items = dyn.get("items") or []
        recent_ids = data.recent_ids
        known_ids = {x for x in ([last] + list(recent_ids)) if x}
        new_items = []
        for item in items:
            if "modules" not in item:
                continue
            module_tag = item["modules"].get("module_tag")
            if module_tag and module_tag.get("text") == "置顶":
                continue
            if item.get("id_str") in known_ids:
                break
            new_items.append(item)
        return new_items

    def _match_filter_regex(
        self, text: Optional[str], filter_regex: List[str], log_template: str
    ) -> bool:
        if not text or not filter_regex:
            return False
        for pattern in filter_regex:
            try:
                if re.search(pattern, text):
                    logger.info(log_template.format(regex_pattern=pattern))
                    return True
            except re.error:
                logger.warning(f"无效的正则表达式: {pattern}")
                continue
        return False

    def _parse_and_filter_dynamics(
        self, dyn: Dict[str, Any], data: SubscriptionRecord
    ) -> List[DynamicParseResult]:
        filter_types = data.filter_types
        filter_regex = data.filter_regex
        uid = str(data.uid)
        items = self._get_dynamic_items(dyn, data)
        result_list: List[DynamicParseResult] = []
        for item in items:
            dyn_id = item.get("id_str") or ""
            t = item.get("type")
            if t == "DYNAMIC_TYPE_FORWARD":
                result = self._handle_forward_dynamic(item, dyn_id, uid, filter_types, filter_regex)
            elif t in ("DYNAMIC_TYPE_DRAW", "DYNAMIC_TYPE_WORD"):
                result = self._handle_draw_or_word_dynamic(item, dyn_id, uid, filter_types, filter_regex)
            elif t == "DYNAMIC_TYPE_AV":
                result = self._handle_video_dynamic(item, dyn_id, uid, filter_types)
            elif t == "DYNAMIC_TYPE_ARTICLE":
                result = self._handle_article_dynamic(item, dyn_id, uid, filter_types)
            else:
                result = DynamicParseResult.skip(None, "unsupported type")
            result_list.append(result)
        return result_list

    def _build_payload(self, item: Dict[str, Any], is_forward: bool = False) -> RenderPayload:
        author = item.get("modules", {}).get("module_author") or {}
        major = item.get("modules", {}).get("module_dynamic", {}).get("major") or {}
        t = item.get("type") or ""
        payload = RenderPayload(
            name=str(author.get("name") or ""),
            type=str(t),
        )
        if t == "DYNAMIC_TYPE_AV":
            arc = major.get("archive") or {}
            payload.title = str(arc.get("title") or "")
            payload.text = str((item.get("modules", {}).get("module_dynamic", {}).get("desc") or {}).get("text") or "")
            payload.image_urls = [str(arc.get("cover") or "")] if arc.get("cover") else []
            if not is_forward:
                bv = arc.get("bvid")
                payload.url = f"https://www.bilibili.com/video/{bv}" if bv else ""
        elif t in ("DYNAMIC_TYPE_DRAW", "DYNAMIC_TYPE_WORD", "DYNAMIC_TYPE_ARTICLE"):
            opus = major.get("opus") or {}
            summary = opus.get("summary") or {}
            payload.summary = str(summary.get("text") or "")
            payload.text = str(summary.get("text") or "")
            payload.title = str(opus.get("title") or "")
            payload.image_urls = [str(p["url"]) for p in (opus.get("pics") or [])[:9] if p.get("url")]
            jump_url = str(opus.get("jump_url") or "")
            if not is_forward and jump_url:
                payload.url = f"https:{jump_url}" if jump_url.startswith("//") else jump_url
        elif t == "DYNAMIC_TYPE_FORWARD":
            desc = item.get("modules", {}).get("module_dynamic", {}).get("desc") or {}
            payload.text = str(desc.get("text") or "")
        return payload

    def _handle_forward_dynamic(
        self, item: Dict, dyn_id: str, uid: str, filter_types: List[str], filter_regex: List[str]
    ) -> DynamicParseResult:
        try:
            is_forward_lottery = (
                item["orig"]["modules"]["module_dynamic"]["major"]["opus"]["summary"][
                    "rich_text_nodes"
                ][0].get("text") == "互动抽奖"
            )
        except (KeyError, TypeError):
            is_forward_lottery = False
        if "forward_lottery" in filter_types and is_forward_lottery:
            return DynamicParseResult.skip(dyn_id, "forward_lottery")
        if "forward" in filter_types:
            return DynamicParseResult.skip(dyn_id, "forward")

        try:
            content_text = item["modules"]["module_dynamic"]["desc"]["text"]
        except (TypeError, KeyError):
            content_text = ""
        if "lottery" in filter_types and re.search(
            r"恭喜.*等\d+位同学中奖", content_text
        ):
            return DynamicParseResult.skip(dyn_id, "lottery")
        if self._match_filter_regex(content_text, filter_regex, "转发内容匹配正则 {regex_pattern}"):
            return DynamicParseResult.skip(dyn_id, "regex")

        payload = self._build_payload(item, is_forward=False)
        payload.uid = uid
        payload.url = f"https://t.bilibili.com/{dyn_id}"

        forward_payload = self._build_payload(item.get("orig") or {}, is_forward=True)
        if forward_payload.image_urls and self.forward_image_limit > 0:
            forward_payload.image_urls = forward_payload.image_urls[: self.forward_image_limit]
        elif self.forward_image_limit == 0:
            forward_payload.image_urls = []
        payload.forward = forward_payload.to_forward_payload()
        return DynamicParseResult.deliver(payload, dyn_id)

    def _handle_draw_or_word_dynamic(
        self, item: Dict, dyn_id: str, uid: str, filter_types: List[str], filter_regex: List[str]
    ) -> DynamicParseResult:
        if "draw" in filter_types:
            return DynamicParseResult.skip(dyn_id, "draw")
        major = item.get("modules", {}).get("module_dynamic", {}).get("major", {})
        if major.get("type") == "MAJOR_TYPE_BLOCKED":
            return DynamicParseResult.skip(dyn_id, "major_blocked")
        opus = major.get("opus", {})
        summary_text = (opus.get("summary") or {}).get("text", "") or ""
        rich_nodes = (opus.get("summary") or {}).get("rich_text_nodes", []) or []
        first_node_text = rich_nodes[0].get("text") if rich_nodes else ""
        if first_node_text == "互动抽奖" and "lottery" in filter_types:
            return DynamicParseResult.skip(dyn_id, "lottery")
        if self._match_filter_regex(summary_text, filter_regex, f"图文动态 {dyn_id} summary 匹配 {{regex_pattern}}"):
            return DynamicParseResult.skip(dyn_id, "regex")
        payload = self._build_payload(item)
        payload.uid = uid
        return DynamicParseResult.deliver(payload, dyn_id)

    def _handle_video_dynamic(
        self, item: Dict, dyn_id: str, uid: str, filter_types: List[str]
    ) -> DynamicParseResult:
        if "video" in filter_types:
            return DynamicParseResult.skip(dyn_id, "video")
        payload = self._build_payload(item)
        payload.uid = uid
        return DynamicParseResult.deliver(payload, dyn_id)

    def _handle_article_dynamic(
        self, item: Dict, dyn_id: str, uid: str, filter_types: List[str]
    ) -> DynamicParseResult:
        if "article" in filter_types:
            return DynamicParseResult.skip(dyn_id, "article")
        major = item.get("modules", {}).get("module_dynamic", {}).get("major", {})
        if major.get("type") == "MAJOR_TYPE_BLOCKED":
            return DynamicParseResult.skip(dyn_id, "major_blocked")
        payload = self._build_payload(item)
        payload.uid = uid
        return DynamicParseResult.deliver(payload, dyn_id)
