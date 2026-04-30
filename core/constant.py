"""核心常量。"""

PLUGIN_NAME = "astrbot_plugin_bili_watch"
DATA_FILE_NAME = "astrbot_plugin_bili_watch.json"
DATA_PATH = "data/astrbot_plugin_bili_watch.json"  # legacy 兼容路径

# 订阅过滤选项
VALID_FILTER_TYPES = {
    "forward",
    "lottery",
    "video",
    "article",
    "draw",
    "live",
    "forward_lottery",
    "comment",  # 加 comment 关闭该订阅的评论扫描
}
LIVE_ATALL_OPTION = "live_atall"
VALID_SUB_OPTIONS = {LIVE_ATALL_OPTION}

DEFAULT_CFG = {
    "bili_sub_list": {},
    "credential": None,
}

RECENT_DYNAMIC_CACHE = 4
