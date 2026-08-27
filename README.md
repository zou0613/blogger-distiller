# blogger-distiller · 博主蒸馏器

一个完全自包含的 TRAE Skill：输入一个抖音博主（名字 / 主页链接 / 本地视频目录），自动完成
**收片 → 拆片 → 蒸馏 → 行动包** 四步流水线，最终产出一份《蒸馏报告》——不是分析文档堆砌，
而是你能直接照着拍自己视频的行动指导。

## 流水线

| 层 | 脚本 | 职责 | 产物 |
|---|---|---|---|
| 收片 | `scripts/download/*` | 抓元数据 + 批量下载 Top N + ffprobe 质检 / 失败修复 / >50MB 压缩闭环 | `meta.json` + `videos/*.mp4` |
| 拆片 | `scripts/extract/*` | 火山方舟豆包视觉模型多模态提取 10 类信息（带时间戳、只提取不分析） | 每条视频的信息档案 `.md` |
| 蒸馏 | 无脚本，纯分析 | 跨档案交叉归纳：商业思维 / 视频思路 / 可照做的行动包 | `distill_report.md` |

拆片的提示词被严格约束为"只提取、不分析"（含证据分级与输出排版规范），蒸馏阶段才做分析。

## 目录结构

```
blogger-distiller/
├── SKILL.md                        # 编排层：流程总控 + 蒸馏方法论
├── scripts/
│   ├── download/
│   │   ├── dy_dl.py                # 单视频下载（headless Chrome CDP 拦截 + 自动校验）
│   │   └── dy_batch_top.py         # 批量下载（按点赞排序 Top N）
│   └── extract/
│       ├── extract_video_info.py   # 火山方舟 Responses API 多模态提取
│       ├── dy_extract.py           # 直传优先提取：抖音直链直接喂方舟，失败自动回退下载上传
│       └── prompt.txt              # 只提取不分析提示词（含排版规范 A-F）
└── references/
    ├── download-playbook.md        # 收片手册
    └── extract-playbook.md         # 拆片手册
```

## 安装（新设备迁移）

技能目录整体拷贝后补齐环境即可，不依赖任何其他 skill：

```bash
pip3 install --user requests websockets   # Python 3.10+
brew install ffmpeg                        # macOS
export ARK_API_KEY=<你的火山方舟 API Key>   # https://console.volcengine.com/ark
```

另需：macOS + Google Chrome（默认路径 `/Applications/Google Chrome.app`）。
建议在**境内家庭/办公网络**下使用：海外或数据中心 IP 会显著提高抖音风控概率。

30 秒冒烟测试：

```bash
python3 -c "import requests, websockets" && ffmpeg -version >/dev/null \
  && ls "/Applications/Google Chrome.app" >/dev/null && echo "依赖OK"
python3 scripts/extract/dy_extract.py "<任一抖音分享链接>" -o /tmp/dy_smoke_test
```

日志依次出现「解析到 aweme_id → 探针 206/xxMB → 提取成功」即迁移完毕。

## 安全红线

- 下载绝不携带用户登录态 cookie（headless Chrome 使用独立 profile，无登录态）
- cookie 仅留在用户浏览器内做只读查询；禁止执行点赞/关注等写操作
- API Key 只进环境变量，绝不写入任何文件
- 禁止伪造数据：所有结论必须溯源到真实提取结果或真实 API 返回

## License

[MIT](LICENSE)
