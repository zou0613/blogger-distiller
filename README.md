# blogger-distiller · 博主蒸馏器

抖音博主打法逆向工具：抓取指定博主的爆款视频，用豆包视觉模型逐条拆解为带时间戳的信息档案，交叉蒸馏出《蒸馏报告》——对方的商业打法 + 一份可直接照做的拍片行动包。

**输入**：博主主页链接（批量 Top N）或单条视频分享链接（单条）。**输出**：`distill_report.md`。

## 《蒸馏报告》的构成

| 部分 | 内容 | 数据来源 |
|---|---|---|
| 事实基础 | 各条视频的信息档案要点与 `meta.json` 的互动数据（发布时间、点赞/评论/收藏） | 模型真实提取结果 |
| 跨档案对比结果 | 高频出现的选题类型、开头句式清单、镜头切换频率统计、CTA 出现位置与原文 | 信息档案交叉比对 |
| 行动包 | 脚本结构模板、候选选题清单、拍摄前 checklist | 基于上述两部分生成 |


## 流水线

| 阶段 | 脚本 | 职责 | 产物 |
|---|---|---|---|
| 收片 | `scripts/download/*` | 抓取全量作品元数据 + 批量下载 Top N + ffprobe 质检 / 失败修复 / 大文件压缩闭环 | `meta.json` + `videos/*.mp4` |
| 拆片 | `scripts/extract/*` | 调用火山方舟豆包视觉模型，多模态提取 10 类信息档案（口播转写/字幕画面文字/镜头结构/人物/品牌/段落切分/数字事实/CTA/声音剪辑，全程带时间戳） | 每条视频一份 `.md` 信息档案 |
| 蒸馏 | 无脚本，纯分析 | 跨档案交叉归纳，转化为行动方案 | `distill_report.md` |

拆片阶段的提示词被严格约束为**只提取、不分析**（证据分级：【明确可见】【明确可听】【OCR/字幕可确认】【不确定】，禁止脑补）；所有主观归纳只发生在蒸馏阶段，且强制区分【事实】【推断】【建议】三档表述。

## 快速开始

```bash
git clone https://github.com/zou0613/blogger-distiller.git
cd blogger-distiller

# 依赖：Python 3.10+、ffmpeg、Google Chrome（macOS 默认路径）
pip3 install --user requests websockets
brew install ffmpeg          # macOS

# 火山方舟 API Key（用于调用豆包视觉模型）
export ARK_API_KEY=<你的 Key>   # https://console.volcengine.com/ark 获取
```

30 秒冒烟测试：

```bash
python3 scripts/extract/dy_extract.py "<任一抖音分享链接>" -o /tmp/dy_smoke_test
```

日志依次出现「解析到 aweme_id → 探针 206/xxMB → 提取成功」即环境就绪。该命令默认走直传路径（视频直链直接交给方舟拉流，本地不落盘）；失败会自动回退为下载后上传，无需人工干预。

## 目录结构

```
blogger-distiller/
├── SKILL.md                        # 四阶段编排说明 + 蒸馏方法论与写作纪律
├── scripts/
│   ├── download/
│   │   ├── dy_dl.py                # 单视频下载（headless Chrome CDP 拦截 + 自动校验）
│   │   └── dy_batch_top.py         # 批量下载（按点赞排序 Top N）
│   └── extract/
│       ├── extract_video_info.py   # 火山方舟 Responses API 多模态提取
│       ├── dy_extract.py           # 直传优先提取入口（失败自动回退下载上传）
│       └── prompt.txt              # 只提取不分析提示词（含输出排版规范 A-F）
└── references/
    ├── download-playbook.md        # 收片手册：元数据抓取、修复闭环、压缩规则
    └── extract-playbook.md         # 拆片手册：API 用法、分段重跑、失败模式表
```

## 使用要点

- 批量规模建议 Top 3~5（每条约 4 分钟提取耗时），可在运行前按需调整
- 输入形式仅限链接：博主主页链接（`douyin.com/user/<sec_uid>`）或单条视频分享链接（`v.douyin.com/xxx` / `douyin.com/video/<id>`）；不支持按名字搜索，也不支持图文作品链接
- 运行产物统一落在一个独立目录 `distilled/<博主名>_<日期>/`，蒸馏报告在根级
- 建议在**境内家庭/办公网络**下使用：海外或数据中心 IP 会显著提高抖音风控概率

## 安全红线

- 下载绝不携带用户登录态 cookie（headless Chrome 使用独立 profile，无登录态）
- cookie 仅留在用户浏览器内做只读查询；禁止执行点赞/关注等任何写操作
- API Key 只进环境变量，绝不写入任何文件
- 禁止伪造数据：所有结论必须溯源到真实提取结果或真实 API 返回

## License

[MIT](LICENSE)
