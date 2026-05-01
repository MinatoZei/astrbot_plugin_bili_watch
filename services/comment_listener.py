"""
UP主 评论区(含楼中楼)监控主循环。

每个 UID 周期:
1. 拉一次最新动态(`get_dynamics_new`,Soulter 已实现)
2. 取最近 N 条动态
3. 对每条动态:
   - 用 `item.basic.comment_type` / `comment_id_str` 拿评论参数(B 站官方姿势)
   - 用 `module_stat.comment.count` 跳过未变动
   - 双 mode 扫(可配置):mode=time 时间倒序 + mode=hot 热度
   - 楼中楼:每条主楼自带前 ~3 条 + 翻页(可配置 max_subreply_pages)
4. UP主自评(`mid == up_uid`)→ 推送
5. rpid 持久化去重(FIFO)+ 评论数缓存
"""

from __future__ import annotations

import asyncio
import time
import traceback
from typing import Any, Dict, List, Optional, Tuple

from astrbot.api import logger
from astrbot.api.event import MessageEventResult
from astrbot.api.message_components import Image, Plain
from astrbot.api.star import Context

from ..bili_client import BiliClient
from ..core.data_manager import DataManager
from ..core.models import SubscriptionRecord


SECURITY_CODES = {-412, -352, -799}  # 风控错误码


class CommentListener:
    def __init__(
        self,
        context: Context,
        data_manager: DataManager,
        bili_client: BiliClient,
        cfg: dict,
    ) -> None:
        self.context = context
        self.data_manager = data_manager
        self.bili_client = bili_client
        self.cfg = cfg
        self._prev_base_interval = 0  # 用于检测 cfg 改了 base 周期
        self.current_interval_secs = 0  # 运行时动态周期
        self.consecutive_block = 0
        self.consecutive_success = 0
        self._refresh_settings(initial=True)
        self.cooldown_until: float = 0.0
        self.last_block_alert_at: float = 0.0

    def _refresh_settings(self, initial: bool = False):
        self.enabled = bool(self.cfg.get("enable_comment_watch", True))
        new_base = max(30, int(self.cfg.get("comment_interval_secs", 90)))
        self.task_gap_secs = max(0, int(self.cfg.get("comment_task_gap_secs", 10)))
        self.scope_recent_n = max(1, int(self.cfg.get("comment_scope_recent_n", 5)))
        self.max_subreply_pages = max(0, int(self.cfg.get("comment_max_subreply_pages", 2)))
        self.dual_mode = bool(self.cfg.get("comment_dual_mode", True))
        self.skip_unchanged = bool(self.cfg.get("comment_skip_unchanged", True))
        self.content_max_len = max(20, int(self.cfg.get("comment_content_max_len", 150)))
        self.notified_rpids_limit = max(50, int(self.cfg.get("notified_rpids_limit", 200)))
        self.cooldown_secs = max(60, int(self.cfg.get("security_cooldown_secs", 600)))
        self.render_as_image = bool(self.cfg.get("comment_render_as_image", False))

        # base 周期变化(WebUI 改了配置)→ 重置动态状态
        if initial or new_base != self._prev_base_interval:
            self._prev_base_interval = new_base
            self.interval_secs = new_base
            self.current_interval_secs = new_base
            self.consecutive_block = 0
            self.consecutive_success = 0
            if not initial:
                logger.info(
                    f"[CommentListener] 检测到 base 周期变化 → {new_base}s,重置自适应"
                )
        else:
            self.interval_secs = new_base

        # 自适应周期上下界
        self.min_interval_secs = max(30, new_base)  # 最小 = base(不能比用户设的还短)
        # 上限默认 300s = 5min。再大就跟动态周期一个量级,失去"评论更激进"的设计意义
        self.max_interval_secs = max(
            new_base, int(self.cfg.get("comment_max_interval_secs", 300))
        )

    # ============= 自适应周期 =============
    def _on_security_block(self):
        """触发风控 → 进入冷却 + 拉长周期"""
        self.cooldown_until = time.monotonic() + self.cooldown_secs
        self.consecutive_block += 1
        self.consecutive_success = 0
        old = self.current_interval_secs
        # 第 1 次:1.5x;第 2 次:2x;第 3+ 次:3x
        multiplier = 1.5 + 0.5 * min(self.consecutive_block - 1, 2)
        self.current_interval_secs = min(
            int(self.current_interval_secs * multiplier),
            self.max_interval_secs,
        )
        logger.warning(
            f"[CommentListener] 触发风控#{self.consecutive_block} 冷却 {self.cooldown_secs}s "
            f"周期 {old}s → {self.current_interval_secs}s"
        )

    def _on_scan_success(self):
        """成功扫描一轮 → 连续 N 次后慢慢缩回"""
        self.consecutive_success += 1
        self.consecutive_block = 0
        # 连续 5 次成功才缩,且只在当前 > base 时缩
        if (
            self.consecutive_success >= 5
            and self.current_interval_secs > self.interval_secs
        ):
            old = self.current_interval_secs
            self.current_interval_secs = max(
                int(self.current_interval_secs * 0.85),
                self.interval_secs,
            )
            self.consecutive_success = 0
            if old != self.current_interval_secs:
                logger.info(
                    f"[CommentListener] 连续 5 次成功,周期 {old}s → {self.current_interval_secs}s"
                )

    # ============= 主循环 =============
    async def start(self):
        logger.info("[CommentListener] 启动")
        while True:
            had_block_this_round = False
            try:
                self._refresh_settings()
                if not self.enabled or self.bili_client.credential is None:
                    await asyncio.sleep(self.current_interval_secs)
                    continue
                now = time.monotonic()
                if now < self.cooldown_until:
                    wait = max(1, int(self.cooldown_until - now))
                    logger.info(
                        f"[CommentListener] 风控冷却中,剩 {wait}s (当前周期 {self.current_interval_secs}s)"
                    )
                    await asyncio.sleep(min(wait, 60))
                    continue
                # _tick 内部 _scan_one_uid 抛风控异常时会调 _on_security_block
                # 用 flag 跟踪这一轮是否进过 cooldown(刚才进过的就别再当成功)
                cooldown_before = self.cooldown_until
                await self._tick()
                if self.cooldown_until > cooldown_before:
                    had_block_this_round = True
                else:
                    self._on_scan_success()
            except asyncio.CancelledError:
                logger.info("[CommentListener] 收到 cancel,退出")
                raise
            except Exception as e:
                logger.error(f"[CommentListener] 主循环异常: {e}\n{traceback.format_exc()}")
            sleep_for = self.current_interval_secs
            if had_block_this_round:
                # 触发了风控,直接靠 cooldown_until 等待,这里小睡即可
                sleep_for = min(sleep_for, 30)
            await asyncio.sleep(sleep_for)

    async def _tick(self):
        """单次扫描周期:聚合 UID 后逐个扫"""
        all_subs = self.data_manager.get_all_subscriptions()
        # UID -> [(sub_user, rec)] 聚合
        uid_targets: Dict[int, List[Tuple[str, SubscriptionRecord]]] = {}
        for sub_user, recs in (all_subs or {}).items():
            for rec in (recs or []):
                # filter_types 含 "comment" 的关闭评论扫描
                if "comment" in (rec.filter_types or []):
                    continue
                try:
                    uid_targets.setdefault(int(rec.uid), []).append((sub_user, rec))
                except Exception:
                    continue

        if not uid_targets:
            return

        for uid, targets in uid_targets.items():
            try:
                await self._scan_one_uid(uid, targets)
            except Exception as e:
                logger.error(f"[CommentListener] UID={uid} 扫描异常: {e}")
            await asyncio.sleep(self.task_gap_secs)

    # ============= 单 UID 扫描 =============
    async def _scan_one_uid(
        self, uid: int, targets: List[Tuple[str, SubscriptionRecord]]
    ):
        """每个 UID 拉一次动态,然后分发给每个订阅者扫评论"""
        dyn_data = await self.bili_client.get_latest_dynamics(uid)
        if not dyn_data:
            return
        items = (dyn_data.get("items") or [])[: self.scope_recent_n]
        if not items:
            return

        for sub_user, rec in targets:
            is_baseline = not rec.comment_baselined
            # baseline 模式:收集所有 UP 自评,末尾只推最新 1 条
            baseline_buffer: List[Tuple[int, Dict[str, Any], Optional[Dict[str, Any]], str, int, Dict[str, Any]]] = []

            for item in items:
                try:
                    await self._scan_one_dynamic(
                        sub_user, rec, item, uid,
                        baseline_buffer=baseline_buffer if is_baseline else None,
                    )
                except Exception as e:
                    if self._is_security_block(e):
                        self._on_security_block()
                        return
                    logger.error(
                        f"[CommentListener] 扫描动态异常 uid={uid} dyn={item.get('id_str')}: {e}"
                    )

            # baseline 末尾:从 buffer 里挑 ctime 最新的 1 条推送
            if is_baseline:
                if baseline_buffer:
                    latest = max(baseline_buffer, key=lambda x: int((x[1].get("ctime") or 0)))
                    level, reply, parent, oid_str, type_int, item = latest
                    asyncio.create_task(
                        self._push(sub_user, uid, level, reply, parent, oid_str, type_int, item)
                    )
                    logger.info(
                        f"[CommentListener] baseline: 推送最新 1 条 UP自评 (skip {len(baseline_buffer) - 1} 条历史) uid={uid} sub_user={sub_user}"
                    )
                else:
                    logger.info(
                        f"[CommentListener] baseline: 无 UP自评 uid={uid} sub_user={sub_user}"
                    )
                rec.comment_baselined = True
                await self.data_manager.save()

    async def _scan_one_dynamic(
        self, sub_user: str, rec: SubscriptionRecord, item: Dict[str, Any], up_uid: int,
        baseline_buffer: Optional[List[Tuple[int, Dict[str, Any], Optional[Dict[str, Any]], str, int, Dict[str, Any]]]] = None,
    ):
        """扫一条动态的评论。
        baseline_buffer:
            - None: 正常推送
            - list: 不推送,把 UP 自评追加到 buffer (caller 决定如何挑选)
        """
        basic = item.get("basic") or {}
        oid_str = basic.get("comment_id_str") or basic.get("rid_str")
        type_int = basic.get("comment_type")
        if not oid_str or type_int is None:
            return

        # 跳过未变动检测
        cur_count = (
            ((item.get("modules") or {}).get("module_stat") or {})
            .get("comment", {})
            .get("count", 0)
        )
        if self.skip_unchanged:
            last_count = rec.get_last_comment_count(str(oid_str))
            if last_count is not None and int(last_count) == int(cur_count):
                return

        # 双 mode 扫
        orders = ["hot", "time"] if self.dual_mode else ["time"]
        seen_rpids: set = set()
        up_replies: List[Tuple[int, Dict[str, Any], Optional[Dict[str, Any]]]] = []

        for order in orders:
            try:
                resp = await self.bili_client.get_comments(
                    oid=int(oid_str), type_int=int(type_int),
                    page_index=1, order=order,
                )
            except Exception as e:
                if self._is_security_block(e):
                    raise
                logger.warning(f"[CommentListener] get_comments 失败 ({order}): {e}")
                continue
            if not resp:
                continue
            replies = resp.get("replies") or []
            self._collect_up_replies(replies, up_uid, seen_rpids, up_replies)
            await asyncio.sleep(0.8)

        # 楼中楼翻页(对每条 rcount > 免费送条数 的主楼)
        if self.max_subreply_pages > 0:
            await self._fetch_pageful_subs(
                int(oid_str), int(type_int), up_uid, seen_rpids, up_replies, orders
            )

        # 推送 + 标记
        for level, reply, parent in up_replies:
            rpid = str(reply.get("rpid"))
            cache_key = f"{type_int}_{oid_str}_{rpid}"
            if self.data_manager.is_comment_notified(sub_user, up_uid, cache_key):
                continue
            await self.data_manager.mark_comment_notified(
                sub_user, up_uid, cache_key, limit=self.notified_rpids_limit
            )
            if baseline_buffer is not None:
                # baseline 模式:只 mark + 收集,不立即推
                baseline_buffer.append((level, reply, parent, str(oid_str), int(type_int), item))
            else:
                asyncio.create_task(
                    self._push(sub_user, up_uid, level, reply, parent, oid_str, type_int, item)
                )

        # 更新 count
        await self.data_manager.update_comment_count(sub_user, up_uid, str(oid_str), int(cur_count))

    # ============= 收集 UP 自评 =============
    def _collect_up_replies(
        self,
        replies: List[Dict[str, Any]],
        up_uid: int,
        seen_rpids: set,
        out: List[Tuple[int, Dict[str, Any], Optional[Dict[str, Any]]]],
    ):
        """从 replies 数组里筛 UP主 一级 + 楼中楼前几条免费送"""
        up_uid_str = str(up_uid)
        for r in replies:
            rpid = str(r.get("rpid"))
            if rpid in seen_rpids:
                pass  # 仍然要看楼中楼,不直接 continue
            else:
                seen_rpids.add(rpid)
                if str((r.get("member") or {}).get("mid", "")) == up_uid_str:
                    out.append((1, r, None))

            for sub in (r.get("replies") or []):
                sub_rpid = str(sub.get("rpid"))
                if sub_rpid in seen_rpids:
                    continue
                seen_rpids.add(sub_rpid)
                if str((sub.get("member") or {}).get("mid", "")) == up_uid_str:
                    out.append((2, sub, r))

    async def _fetch_pageful_subs(
        self,
        oid: int,
        type_int: int,
        up_uid: int,
        seen_rpids: set,
        out: List[Tuple[int, Dict[str, Any], Optional[Dict[str, Any]]]],
        orders: List[str],
    ):
        """对每条 rcount 多的主楼翻页拉楼中楼"""
        # 复用一次 hot/time 模式的 replies(从 out 关联?不能,简单做法:再拉一次 time 模式拿全主楼)
        # 为了简单复用,这里只对一种 mode 的主楼翻页(用 time 排序)
        try:
            resp = await self.bili_client.get_comments(
                oid=oid, type_int=type_int, page_index=1, order="time",
            )
        except Exception as e:
            if self._is_security_block(e):
                raise
            return
        if not resp:
            return
        replies = resp.get("replies") or []
        for r in replies:
            rcount = int(r.get("rcount") or 0)
            free_subs = r.get("replies") or []
            if rcount <= len(free_subs):
                continue
            root_rpid = r.get("rpid")
            for pn in range(1, self.max_subreply_pages + 1):
                try:
                    sub_resp = await self.bili_client.get_sub_comments(
                        oid=oid, type_int=type_int, root_rpid=int(root_rpid), page_index=pn
                    )
                except Exception as e:
                    if self._is_security_block(e):
                        raise
                    break
                if not sub_resp:
                    break
                subs = sub_resp.get("replies") or []
                if not subs:
                    break
                for s in subs:
                    sub_rpid = str(s.get("rpid"))
                    if sub_rpid in seen_rpids:
                        continue
                    seen_rpids.add(sub_rpid)
                    if str((s.get("member") or {}).get("mid", "")) == str(up_uid):
                        out.append((2, s, r))
                await asyncio.sleep(0.5)

    # ============= 推送 =============
    @staticmethod
    def _extract_comment_image_urls(reply: Dict[str, Any]) -> List[str]:
        content = reply.get("content") or {}
        pictures = content.get("pictures") or []
        if not isinstance(pictures, list):
            return []
        urls: List[str] = []
        for picture in pictures:
            if not isinstance(picture, dict):
                continue
            url = str(picture.get("img_src") or picture.get("url") or "").strip()
            if url:
                urls.append(url)
        return urls

    async def _push(
        self,
        sub_user: str,
        up_uid: int,
        level: int,
        reply: Dict[str, Any],
        parent: Optional[Dict[str, Any]],
        oid_str: str,
        type_int: int,
        item: Dict[str, Any],
    ):
        try:
            up_name = (
                (item.get("modules") or {}).get("module_author", {}).get("name") or str(up_uid)
            )
            content_data = reply.get("content") or {}
            content = content_data.get("message", "") or ""
            ctime_ts = int(reply.get("ctime") or 0)
            from datetime import datetime
            comment_time = (
                datetime.fromtimestamp(ctime_ts).strftime("%Y-%m-%d %H:%M:%S")
                if ctime_ts > 0 else "?"
            )

            dyn_id = item.get("id_str") or oid_str
            rpid = reply.get("rpid")
            if level == 1:
                title = f"💬 UP主「{up_name}」 在动态评论区发言"
                jump = f"https://t.bilibili.com/{dyn_id}#reply{rpid}"
                prefix = ""
            else:
                p_name = (parent or {}).get("member", {}).get("uname", "?") if parent else "?"
                p_msg = ((parent or {}).get("content") or {}).get("message", "")[:30]
                title = f"💬 UP主「{up_name}」 在楼中楼回复"
                jump = (
                    f"https://t.bilibili.com/{dyn_id}?reply_id="
                    f"{(parent or {}).get('rpid')}#reply{rpid}"
                )
                prefix = f"↪ 回复 @{p_name}「{p_msg}」: "

            body = (prefix + content)[: self.content_max_len]
            if len(prefix + content) > self.content_max_len:
                body += "..."

            text = (
                f"{title}\n"
                f"{body}\n\n"
                f"🕒 {comment_time}\n"
                f"🔗 {jump}"
            )
            chain: List[Any] = [Plain(text)]
            for image_url in self._extract_comment_image_urls(reply):
                chain.append(Image.fromURL(image_url))
            await self.context.send_message(
                sub_user, MessageEventResult(chain=chain).use_t2i(False)
            )
            logger.info(
                f"[CommentListener] 推送 UP自评 sub_user={sub_user} dyn={dyn_id} rpid={rpid} L{level}"
            )
        except Exception as e:
            logger.error(f"[CommentListener] 推送失败: {e}\n{traceback.format_exc()}")

    # ============= 风控辅助 =============
    @staticmethod
    def _is_security_block(exc: Exception) -> bool:
        msg = str(exc)
        if "412" in msg or "352" in msg or "799" in msg:
            return True
        if "request was banned" in msg:
            return True
        if "请求过于频繁" in msg:
            return True
        return False
