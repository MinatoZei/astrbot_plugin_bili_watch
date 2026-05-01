# CLAUDE.md - astrbot_plugin_bili_watch

先读本目录 `AGENTS.md`。以下是执行任务时最容易漏掉的检查点。

## 当前项目事实

- 本目录是 Git 仓库根目录。
- 上级目录保存本地 release zip 和 API 探测脚本。
- `metadata.yaml` 是 AstrBot 插件版本来源。

## 图片处理约定

“不做图片渲染”是指不渲染 HTML/T2I 卡片,不是不发图片。

必须保留:

- `DynamicListener` 的转发原图/视频封面附件。
- `CommentListener` 的一级评论图片附件。
- `CommentListener` 的楼中楼回复图片附件。

## 最小验证

```bash
python3 -m unittest tests.test_image_attachments -v
python3 -m py_compile services/listener.py services/comment_listener.py core/utils.py tests/test_image_attachments.py
```

## 发布流程

改用户可见行为时:

1. bump `metadata.yaml::version`。
2. commit。
3. tag。
4. 在上级目录重新生成 zip。
5. push `main` 和 tag。
6. 创建或更新 GitHub Release 并上传 zip。
7. 下载 Release zip 验证 `metadata.yaml`。

只 push commit/tag 不够;AstrBot 用户通常从 GitHub Release zip 安装。
