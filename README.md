<div align="center">

# 🔬 blogger-distiller · 博主蒸馏器

*> 把单条爆款"熬成"打法 —— 一键逆向复刻别人的流量密码。*

抖音单视频打法逆向分析工具：解析单条视频分享链接，用豆包视觉模型直传拆解为带时间戳的信息档案，蒸馏出打法要点与可直接照做的行动包。

**核心能力**：单视频免登录直传 · 无头 Chrome 独立 profile 免 Cookie · 豆包视觉精细拆片 · 打法级蒸馏行动包

<!-- 徽章组：输入 -->
![输入](https://img.shields.io/badge/输入-单视频分享链接-1D4ED8)
![直传](https://img.shields.io/badge/直传-免下载优先-3B82F6)
![回退](https://img.shields.io/badge/回退-自动下载上传-60A5FA)
![免Cookie](https://img.shields.io/badge/免登录免Cookie-独立profile-93C5FD)

<!-- 徽章组：智能 -->
![模型](https://img.shields.io/badge/模型-豆包视觉-0E7490)
![拆片](https://img.shields.io/badge/拆片-10维信息档案-0891B2)
![蒸馏](https://img.shields.io/badge/蒸馏-打法行动包-06B6D4)
![只提取](https://img.shields.io/badge/原则-只提取不分析-22D3EE)

<!-- 徽章组：环境和许可 -->
![Python](https://img.shields.io/badge/Python-3.10%2B-3776AB)
![macOS](https://img.shields.io/badge/macOS-%E2%9C%93-black)
![License](https://img.shields.io/badge/License-MIT-lightgrey)

</div>

---

## 快速开始

```bash
git clone https://github.com/zou0613/blogger-distiller.git
cd blogger-distiller

# 依赖：Python 3.10+、ffmpeg、Google Chrome（macOS 默认路径，仅作本地签名引擎，不登录不读 Cookie）
pip3 install --user requests websockets
brew install ffmpeg          # macOS

# 火山方舟 API Key（用于调用豆包视觉模型）
export ARK_API_KEY=<你的 Key>   # https://console.volcengine.com/ark 获取
```

## 输入要求与说明

| 项目 | 说明 |
|---|---|
| 输入形式 | 直接输入抖音单条视频分享链接即可（`v.douyin.com/xxx` 短链或 `douyin.com/video/<id>` 长链） |
| 登录要求 | 免登录、免 Cookie。仅借本机 headless Chrome + 独立 profile（`/tmp/dy-profile`）算签名拿直链，不读取你的登录态 |
| 输出位置 | 运行产物落在一个独立目录，信息档案按创建时间命名（`<YYYYMMDD_HHMM>.md`），蒸馏说明随对话交付 |
| 网络建议 | 建议在**境内家庭/办公网络**下使用：海外或数据中心 IP 会显著提高抖音风控概率 |

## 目录结构

<details open>
<summary><b>展开查看</b></summary>

```
blogger-distiller/
├── SKILL.md                        # 编排说明（拆片→蒸馏→行动包）+ 蒸馏方法论与写作纪律
├── scripts/
│   ├── download/
│   │   └── dy_dl.py                # 单视频下载（直传失败回退用，headless CDP 拦截 + 自动校验）
│   └── extract/
│       ├── extract_video_info.py   # 火山方舟 Responses API 多模态提取
│       ├── dy_extract.py           # ★ 单视频入口：解析→拿直链→直传豆包→出报告（失败自动回退）
│       └── prompt.txt              # 只提取不分析提示词（含输出排版规范）
└── references/
    ├── download-playbook.md        # 收片手册：直传/下载、headless 进程回收、音轨校验
    └── extract-playbook.md         # 拆片手册：API 用法、分段重跑、失败模式表
```

</details>

## License

[MIT](LICENSE)
