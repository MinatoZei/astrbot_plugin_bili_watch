# AGENTS.md - astrbot_plugin_bili_watch

本目录是实际 AstrBot 插件仓库。上一级 `/opt/astrbot-bili-watch` 是开发工作区,包含 refs、探测脚本和本地打包产物。

## 项目边界

- 本仓库只包含可发布插件代码、文档和单元测试。
- 上级 `refs/` 仅作参考,不要复制进仓库。
- 上级 `tests/.env` 含 B站 凭据,严禁提交。
- GitHub 仓库: `https://github.com/MinatoZei/astrbot_plugin_bili_watch`

## 功能边界

本插件故意不做 HTML/T2I 卡片渲染,使用纯文本 + 图片消息链推送。

不要误删以下图片附件能力:

- 转发动态的原动态图片或视频封面。
- UP主一级评论里的 `reply.content.pictures`。
- UP主楼中楼回复里的 `reply.content.pictures`。

`forward_image_limit` 只控制转发动态原图数量: `0` 不发,默认 `1`,更大值按数量截断。

## 常用验证

```bash
python3 -m unittest tests.test_image_attachments -v
python3 -m py_compile services/listener.py services/comment_listener.py core/utils.py tests/test_image_attachments.py
```

`tests/test_image_attachments.py` 覆盖转发原图、评论图片和楼中楼图片回归。

## 版本与发布

- 用户可见功能修复或行为变更要更新 `metadata.yaml::version`。
- 提交后推送 `main` 和版本 tag。
- GitHub Release 不会因为 tag 自动拥有上传包;必须额外创建/更新 Release 并上传 zip。
- 发布 zip 从上级工作区生成: `/opt/astrbot-bili-watch/astrbot_plugin_bili_watch.zip`。
- 发布后下载 GitHub Release asset,检查 zip 内 `metadata.yaml` 的版本。

## 打包

在 `/opt/astrbot-bili-watch` 执行:

```bash
tmpdir=$(mktemp -d)
mkdir -p "$tmpdir/astrbot_plugin_bili_watch"
rsync -a --exclude='.git' --exclude='tests' --exclude='__pycache__' --exclude='*.pyc' \
  plugin/ "$tmpdir/astrbot_plugin_bili_watch/"
(cd "$tmpdir" && zip -qr "$tmpdir/astrbot_plugin_bili_watch.zip" astrbot_plugin_bili_watch)
mv "$tmpdir/astrbot_plugin_bili_watch.zip" /opt/astrbot-bili-watch/astrbot_plugin_bili_watch.zip
```

检查包内没有 `.git`、`tests/`、`__pycache__/`。

## 凭据安全

不要把 GitHub token 或 B站 cookie 写入文件、remote URL、commit message、README 或日志。需要推送时优先使用已有认证;临时 token 只能通过 stdin/GIT_ASKPASS 使用。
