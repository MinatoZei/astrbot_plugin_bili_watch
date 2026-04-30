from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


def _to_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"1", "true", "yes", "on"}:
            return True
        if lowered in {"0", "false", "no", "off", ""}:
            return False
    return bool(value)


def _to_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _to_str_list(value: Any) -> List[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value if str(item)]


@dataclass
class ForwardPayload:
    name: str = ""
    avatar: str = ""
    pendant: str = ""
    text: str = ""
    image_urls: List[str] = field(default_factory=list)
    qrcode: str = ""
    url: str = ""
    title: str = ""
    type: str = ""
    summary: str = ""
    uid: str = ""
    banner: str = ""

    @classmethod
    def from_dict(cls, raw: Optional[Dict[str, Any]]) -> "ForwardPayload":
        if not isinstance(raw, dict):
            return cls()
        return cls(
            name=str(raw.get("name", "") or ""),
            avatar=str(raw.get("avatar", "") or ""),
            pendant=str(raw.get("pendant", "") or ""),
            text=str(raw.get("text", "") or ""),
            image_urls=_to_str_list(raw.get("image_urls")),
            qrcode=str(raw.get("qrcode", "") or ""),
            url=str(raw.get("url", "") or ""),
            title=str(raw.get("title", "") or ""),
            type=str(raw.get("type", "") or ""),
            summary=str(raw.get("summary", "") or ""),
            uid=str(raw.get("uid", "") or ""),
            banner=str(raw.get("banner", "") or ""),
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "avatar": self.avatar,
            "pendant": self.pendant,
            "text": self.text,
            "image_urls": list(self.image_urls),
            "qrcode": self.qrcode,
            "url": self.url,
            "title": self.title,
            "type": self.type,
            "summary": self.summary,
            "uid": self.uid,
            "banner": self.banner,
        }


@dataclass
class RenderPayload:
    name: str = ""
    avatar: str = ""
    pendant: str = ""
    text: str = ""
    image_urls: List[str] = field(default_factory=list)
    qrcode: str = ""
    url: str = ""
    title: str = ""
    type: str = ""
    summary: str = ""
    uid: str = ""
    banner: str = ""
    forward: Optional[ForwardPayload] = None

    @classmethod
    def from_dict(cls, raw: Optional[Dict[str, Any]]) -> "RenderPayload":
        if not isinstance(raw, dict):
            return cls()
        forward_raw = raw.get("forward")
        forward_payload = (
            ForwardPayload.from_dict(forward_raw)
            if isinstance(forward_raw, dict)
            else None
        )
        return cls(
            name=str(raw.get("name", "") or ""),
            avatar=str(raw.get("avatar", "") or ""),
            pendant=str(raw.get("pendant", "") or ""),
            text=str(raw.get("text", "") or ""),
            image_urls=_to_str_list(raw.get("image_urls")),
            qrcode=str(raw.get("qrcode", "") or ""),
            url=str(raw.get("url", "") or ""),
            title=str(raw.get("title", "") or ""),
            type=str(raw.get("type", "") or ""),
            summary=str(raw.get("summary", "") or ""),
            uid=str(raw.get("uid", "") or ""),
            banner=str(raw.get("banner", "") or ""),
            forward=forward_payload,
        )

    def to_dict(self) -> Dict[str, Any]:
        payload = {
            "name": self.name,
            "avatar": self.avatar,
            "pendant": self.pendant,
            "text": self.text,
            "image_urls": list(self.image_urls),
            "qrcode": self.qrcode,
            "url": self.url,
            "title": self.title,
            "type": self.type,
            "summary": self.summary,
            "uid": self.uid,
            "banner": self.banner,
        }
        if self.forward:
            payload["forward"] = self.forward.to_dict()
        return payload

    def to_template_context(self) -> Dict[str, Any]:
        return self.to_dict()

    def to_forward_payload(self) -> ForwardPayload:
        return ForwardPayload(
            name=self.name,
            avatar=self.avatar,
            pendant=self.pendant,
            text=self.text,
            image_urls=list(self.image_urls),
            qrcode=self.qrcode,
            url=self.url,
            title=self.title,
            type=self.type,
            summary=self.summary,
            uid=self.uid,
            banner=self.banner,
        )


@dataclass
class DynamicParseResult:
    dyn_id: Optional[str] = None
    payload: Optional[RenderPayload] = None
    skipped: bool = False
    reason: str = ""

    @classmethod
    def deliver(
        cls, payload: RenderPayload, dyn_id: Optional[str]
    ) -> "DynamicParseResult":
        return cls(dyn_id=dyn_id, payload=payload, skipped=False, reason="")

    @classmethod
    def skip(cls, dyn_id: Optional[str], reason: str) -> "DynamicParseResult":
        return cls(dyn_id=dyn_id, payload=None, skipped=True, reason=reason)

    @classmethod
    def empty(cls) -> "DynamicParseResult":
        return cls(dyn_id=None, payload=None, skipped=False, reason="")

    def has_payload(self) -> bool:
        return self.payload is not None


@dataclass
class SubscriptionRecord:
    uid: int
    last: str = ""
    is_live: bool = False
    filter_types: List[str] = field(default_factory=list)
    filter_regex: List[str] = field(default_factory=list)
    recent_ids: List[str] = field(default_factory=list)
    live_atall: bool = False
    last_live_start_ts: int = 0
    # 评论监控相关 (M2 新增)
    notified_rpids: List[str] = field(default_factory=list)
    last_comment_scan_at: int = 0
    last_comment_counts: Dict[str, int] = field(default_factory=dict)
    comment_baselined: bool = False  # 是否已建立 baseline(首次订阅时只标记不推送)

    @classmethod
    def from_dict(cls, raw: Dict[str, Any]) -> "SubscriptionRecord":
        uid = _to_int(raw.get("uid"), default=-1)
        if uid < 0:
            raise ValueError(f"invalid uid: {raw.get('uid')}")
        last_comment_counts_raw = raw.get("last_comment_counts") or {}
        last_comment_counts = {
            str(k): _to_int(v) for k, v in last_comment_counts_raw.items()
            if isinstance(last_comment_counts_raw, dict)
        }
        return cls(
            uid=uid,
            last=str(raw.get("last", "") or ""),
            is_live=_to_bool(raw.get("is_live", False)),
            filter_types=_to_str_list(raw.get("filter_types")),
            filter_regex=_to_str_list(raw.get("filter_regex")),
            recent_ids=_to_str_list(raw.get("recent_ids")),
            live_atall=_to_bool(raw.get("live_atall", False)),
            last_live_start_ts=max(0, _to_int(raw.get("last_live_start_ts", 0))),
            notified_rpids=_to_str_list(raw.get("notified_rpids")),
            last_comment_scan_at=max(0, _to_int(raw.get("last_comment_scan_at", 0))),
            last_comment_counts=last_comment_counts,
            comment_baselined=_to_bool(raw.get("comment_baselined", False)),
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "uid": self.uid,
            "last": self.last,
            "is_live": self.is_live,
            "filter_types": list(self.filter_types),
            "filter_regex": list(self.filter_regex),
            "recent_ids": list(self.recent_ids),
            "live_atall": self.live_atall,
            "last_live_start_ts": self.last_live_start_ts,
            "notified_rpids": list(self.notified_rpids),
            "last_comment_scan_at": self.last_comment_scan_at,
            "last_comment_counts": dict(self.last_comment_counts),
            "comment_baselined": self.comment_baselined,
        }

    def update_filters(
        self, filter_types: List[str], filter_regex: List[str], live_atall: bool
    ) -> None:
        self.filter_types = list(filter_types)
        self.filter_regex = list(filter_regex)
        self.live_atall = bool(live_atall)

    def is_comment_notified(self, cache_key: str) -> bool:
        return cache_key in self.notified_rpids

    def mark_comment_notified(self, cache_key: str, limit: int) -> None:
        if cache_key in self.notified_rpids:
            return
        self.notified_rpids.append(cache_key)
        if len(self.notified_rpids) > limit:
            del self.notified_rpids[: len(self.notified_rpids) - limit]

    def update_comment_count(self, oid: str, count: int) -> None:
        self.last_comment_counts[str(oid)] = int(count)

    def get_last_comment_count(self, oid: str) -> Optional[int]:
        return self.last_comment_counts.get(str(oid))

    def record_dynamic(self, dyn_id: str, history_limit: int) -> None:
        self.last = dyn_id
        if not dyn_id:
            return
        if dyn_id in self.recent_ids:
            self.recent_ids.remove(dyn_id)
        self.recent_ids.insert(0, dyn_id)
        if len(self.recent_ids) > history_limit:
            del self.recent_ids[history_limit:]
