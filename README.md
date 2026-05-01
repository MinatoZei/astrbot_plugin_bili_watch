# astrbot_plugin_bili_watch

> 基于 [Soulter/astrbot_plugin_bilibili](https://github.com/Soulter/astrbot_plugin_bilibili) 改造,新增 **UP主 评论区(含楼中楼)监控** 能力的 [AstrBot](https://github.com/AstrBotDevs/AstrBot) 插件。

> Licensed under [AGPL-3.0](./LICENSE) (与 AstrBot 主仓库一致)。

监控指定 UP主 的:

| | 内容 | 来源 |
|---|---|---|
| 📺 | 视频投稿 / 图文 / 文字 / 转发 / 专栏动态 | `feed/space` (库内置 WBI 签名,绕 -412) |
| 🔴 | 直播开播 / 下播 + 直播时长 | `live/Room/get_status_info_by_uids` |
| 💬 | UP主 在自己**动态/视频评论区**的发言 | `reply/main` (热度 + 时间双 mode) |
| 💬💬 | UP主 在**楼中楼**里回复粉丝 | `reply/reply` 翻页 |
| 🖼️ | 转发动态时附带的**原动态图片** | 解析 `item.orig.modules.module_dynamic.major.opus.pics` |
| 🖼️ | UP主 评论 / 楼中楼回复里的图片 | 解析 `reply.content.pictures` |

**纯文本+图片消息链推送**,不做卡片渲染(故意精简,跟原版 Soulter 插件最大的区别)。这里砍掉的是 HTML/T2I 卡片渲染,不是 B站 原图附件;转发动态原图、评论图片、楼中楼回复图片都会随消息链发送。

群聊隔离:**天然支持** — 不同群/私聊订阅互不干扰,同一 UP 多人订阅 API 只拉一次。

## 安装

### 方式 A:WebUI 上传 zip(最简单)

1. 下载本仓库的 zip 包(或 GitHub 自动打的 zip)
2. 在 AstrBot WebUI → 插件管理 → 「上传插件」选这个 zip
3. 安装完成后在「插件配置」里填 `sessdata`(或者用扫码登录)

### 方式 B:Git clone(开发用)

```bash
cd <AstrBot>/data/plugins/
git clone https://github.com/<your>/astrbot_plugin_bili_watch.git
# 然后在 WebUI 重载插件,WebUI 配 sessdata
```

### 方式 C:扫码登录(管理员私聊)

不想手贴 sessdata 可以直接扫码:

```
/biliw_login
```

(必须管理员私聊执行,会出二维码,B站 App 扫码即可,凭据自动持久化)

## 指令清单

> **指令前缀 `/biliw_`** (bili-watch 缩写)、中文别名前缀「监控」 — 跟原版 Soulter 插件指令完全错开,可并存。

| 指令 | 别名 | 权限 | 说明 |
|---|---|---|---|
| `/biliw_sub <uid> [filters]` | `/监控订阅` | 普通 | 订阅一个 UP主,动态 + 视频 + 直播 + 评论一并推送 |
| `/biliw_sub_list` | `/监控列表` | 普通 | 当前会话的全部订阅 |
| `/biliw_sub_del <uid>` | `/取消监控` | 普通 | 取消订阅 |
| `/biliw_sub_test <uid>` | `/监控测试` | 普通 | 不存订阅,只拉一次最新动态测试推送 |
| `/biliw_comment_test <uid>` | `/监控评论测试` | 普通 | 强扫一次评论(忽略 rpid 缓存),测试 UP主自评识别 |
| `/biliw_status` | `/监控状态` | 普通 | 查看凭据状态/订阅总数/冷却剩余 |
| `/biliw_login` | — | ADMIN(私聊) | 扫码登录 |
| `/biliw_logout` | — | ADMIN | 清除凭据 |
| `/biliw_global_sub <umo> <uid> [filters]` | `/监控全局订阅` | ADMIN | 给指定群/私聊批量加订阅 |
| `/biliw_global_del <umo>` | `/监控全局删除` | ADMIN | 删指定 umo 的全部订阅 |
| `/biliw_global_list` | `/监控全局列表` | ADMIN | 查看所有订阅会话 |

### filter 选项(空格分隔)

| 关键词 | 关闭哪类内容 |
|---|---|
| `forward` | 不推转发动态 |
| `lottery` | 不推抽奖 |
| `forward_lottery` | 不推"转发抽奖" |
| `video` | 不推视频投稿 |
| `article` | 不推专栏 |
| `draw` | 不推图文/文字 |
| `live` | 不推直播开播下播 |
| `comment` | 不扫该 UP 的评论区 |
| `live_atall` | 直播开播 @全体成员(默认关) |
| 其他字符串 | 当正则过滤动态正文 |

例子:`/biliw_sub 1298779265 forward comment` = 订阅这个 UP 的视频/图文/直播,但不要转发也不要扫评论。

## 配置项(全部在 WebUI 后台可视化编辑)

| 类别 | 字段 | 默认 | 说明 |
|---|---|---|---|
| 凭据 | `sessdata` | - | B站 cookie SESSDATA |
| 凭据 | `proxy` | - | 代理(socks5/http) |
| 动态轮询 | `interval_secs` | 300 | 单 UID 检测周期 |
| 动态轮询 | `task_gap_secs` | 20 | 相邻 UID 间隔 |
| 动态轮询 | `dynamic_limit` | 5 | 单次推送条数上限 |
| 动态轮询 | `recent_dynamic_cache` | 4 | 动态去重缓存 |
| 推送 | `forward_image_limit` | 1 | 转发动态原图推送数(0=不发,9=全推) |
| 评论扫描 | `enable_comment_watch` | true | 总开关 |
| 评论扫描 | `comment_interval_secs` | 90 | 检测周期 |
| 评论扫描 | `comment_task_gap_secs` | 10 | 相邻 UID 间隔 |
| 评论扫描 | `comment_scope_recent_n` | 5 | 每次覆盖最近 N 条动态 |
| 评论扫描 | `comment_max_subreply_pages` | 2 | 楼中楼翻页上限(每页 20) |
| 评论扫描 | `comment_dual_mode` | true | 热度 + 时间倒序 双 mode 扫 |
| 评论扫描 | `comment_skip_unchanged` | true | 评论数没变就跳过(请求量降至 1/3~1/5) |
| 评论扫描 | `comment_content_max_len` | 150 | 评论正文截断长度 |
| 评论扫描 | `notified_rpids_limit` | 200 | 每订阅的 rpid 缓存上限(FIFO) |
| 风控 | `security_cooldown_secs` | 600 | 触发 -412/-352 后冷却 |

## 常见问题

### 评论扫描有遗漏?

- 默认只扫**最近 5 条动态**(`comment_scope_recent_n`),老动态评论不扫。可调大。
- 默认只扫**第一页评论**(20 条):
  - 热度模式覆盖热门,时间倒序模式覆盖新增
  - 双模式合并 = 涵盖大部分场景
- 楼中楼翻页默认 2 页(40 条),极热评论可调到 5

### 凭据失效会怎样?

- 启动时打日志告警
- 评论/动态扫描循环会暂停,不报错刷屏
- 等管理员重新 `/biliw_login` 即可

### 跟原版 Soulter 插件冲突吗?

不冲突。`metadata.yaml::name = astrbot_plugin_bili_watch`,数据目录、配置 key 都跟 Soulter 隔离。可以并存(虽然没必要)。

## 数据存放

- **配置**(`_conf_schema.json` 字段值):AstrBot 内部 DB
- **运行时数据**(订阅列表/凭据/已通知的 dyn_id 和 rpid):`data/plugin_data/astrbot_plugin_bili_watch/astrbot_plugin_bili_watch.json`
- 升级/重装插件不会丢数据(在 `data/` 下,跟插件代码目录分离)

## 致谢

特别感谢 [Soulter](https://github.com/Soulter) & [Flartiny](https://github.com/Flartiny) 的 [**astrbot_plugin_bilibili**](https://github.com/Soulter/astrbot_plugin_bilibili) — 本插件的**订阅核心逻辑**(UID 任务池调度、动态去重、过滤体系、凭据管理、扫码登录、直播状态推送)全部直接沿用自该项目,没有他们的工作就没有本插件。

在此基础上本插件做了两件事:

1. **精简**:砍掉了 Bangumi 番剧推荐 / 全站热门视频搜索 / BV 链接解析 / QQ 小程序解析 / HTML 卡片渲染 等非核心功能,只保留 UP主 订阅推送这个最常用场景。推送从卡片图片渲染改成纯文本+原图消息链,不依赖 Playwright,体积和资源占用都更轻量。
2. **加上 UP主 评论区(含楼中楼)监控**:新增 `services/comment_listener.py`,定时扫描订阅 UP主 在自己动态/视频评论区的发言(含楼中楼里回复粉丝),识别后推送到对应群/私聊。这是原版 [astrbot_plugin_bilibili](https://github.com/Soulter/astrbot_plugin_bilibili) 没有的功能。评论扫描参考了 [Suyannnnnnnn/bilibili-notifier-new](https://github.com/Suyannnnnnnn/bilibili-notifier-new)(动态评论 `type=11` 与 `module_stat.comment.count` 增量检测)和 [Trevo1/bilibili_monitor](https://github.com/Trevo1/bilibili_monitor)(rpid 持久化去重 + 风控冷却)的思路。

如果你只需要原版动态推送 + 卡片渲染,推荐直接用 [astrbot_plugin_bilibili](https://github.com/Soulter/astrbot_plugin_bilibili);只有需要"评论区监控"或"轻量纯文本"这两个场景才装本插件。

## License

本插件以 **AGPL-3.0** 发布(与 AstrBot 主仓库一致),完整许可文本见 [LICENSE](./LICENSE)。

如果你 fork 本插件继续改造,请保留:
- 本 LICENSE 文件
- README 中"致谢"段落对原作者的署名
- `metadata.yaml::author` 字段中链接式的来源追溯
