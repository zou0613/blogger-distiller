<div align="center">

# blogger-distiller · 博主蒸馏器

*> 把爆款"熬成"打法：一键抓取目标博主的爆款视频，逐条拆解成带时间戳的信息档案，交叉蒸馏出一份可复制的商业打法报告。*

抖音博主打法逆向分析工具：无需登录，喂进一个博主主页链接，即可自动完成「下载 → 提取 → 蒸馏 → 出报告」全流程——从口播、画面、字幕到 CTA，把对方 3~5 条最火的视频拆成数据，提炼出流量天花板、钩子结构、变现路径和可直接照做的排片方案。

> **核心卖点**：免下载直传 · 豆包视觉模型精细拆片 · 结构化信息档案 · 打法级蒸馏报告

![License](https://img.shields.io/badge/License-MIT-lightgrey) ![Platform](https://img.shields.io/badge/macOS-%E2%9C%93-black) ![Python](https://img.shields.io/badge/Python-3.10%2B-3776AB)

</div>

---

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

<details>
<summary><b>输入要求与说明</b></summary>

- 输入形式仅限链接：博主主页链接（`douyin.com/user/<sec_uid>`）或单条视频分享链接（`v.douyin.com/xxx` / `douyin.com/video/<id>`）；不支持按名字搜索，也不支持图文作品链接
- 批量规模建议 Top 3~5（每条约 4 分钟提取耗时），可在运行前按需调整
- 运行产物统一落在一个独立目录 `distilled/<博主名>_<日期>/`，蒸馏报告在根级
- 建议在**境内家庭/办公网络**下使用：海外或数据中心 IP 会显著提高抖音风控概率

</details>

## 目录结构

<details open>
<summary><b>展开查看</b></summary>

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

</details>

## License

[MIT](LICENSE)