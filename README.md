<div align="center">

# blogger-distiller · 博主蒸馏器

**把一个博主，蒸馏成一份行动包。**

抓取指定博主的爆款视频，用豆包视觉模型逐条拆解为带时间戳的信息档案，再交叉比对产出《蒸馏报告》。

![License MIT](https://img.shields.io/badge/License-MIT-lightgrey) ![Python](https://img.shields.io/badge/Python-3.10%2B-3776AB) ![Platform macOS](https://img.shields.io/badge/macOS-%E2%9C%93-black)

`输入 IN ▸ 博主主页链接（批量 Top N）`&nbsp;&nbsp;·&nbsp;&nbsp;`输入 IN ▸ 单条视频分享链接`&nbsp;&nbsp;·&nbsp;&nbsp;`输出 OUT ▸ distill_report.md`

</div>

---

## 壹 · 流水线

| 阶段 | 说明 | 输出 |
|---|---|---|
| **01 收片** | 全量作品元数据抓取；按点赞批量下载 Top N；ffprobe 质检、失败修复、大文件压缩闭环 | `videos/*.mp4` + `meta.json` |
| **02 拆片** | 豆包视觉模型逐条提取 10 类信息档案——口播转写、字幕画面文字、镜头结构、品牌记录，全程带时间戳。支持直传优先：直链直接交方舟拉流，本地不落盘 | 每条视频一份档案 `.md` |
| **03 蒸馏** | 跨档案交叉比对：高频选题类型、开头句式清单、镜头切换频率统计、CTA 出现位置与原文 | 交叉对比结论 |
| **04 行动包** | 脚本结构模板、候选选题清单、拍摄前 checklist——基于前两步生成，填空即可使用 | `distill_report.md` |

> **支持两种链接输入**：博主主页链接 与 单条视频分享链接（单条模式走直传，跳过收片层）。不支持按名字搜索，不支持图文作品。

## 贰 · 《蒸馏报告》的构成

| 部分 | 内容 | 数据来源 |
|---|---|---|
| PART Ⅰ 事实基础 | 各条视频的信息档案要点与 `meta.json` 的互动数据（发布时间、点赞/评论/收藏） | 模型真实提取结果 |
| PART Ⅱ 跨档案对比结果 | 高频出现的选题类型、开头句式清单、镜头切换频率统计、CTA 出现位置与原文 | 信息档案交叉比对 |
| PART Ⅲ 行动包 | 脚本结构模板、候选选题清单、拍摄前 checklist | 基于上述两部分生成 |

## 叁 · 快速开始

```bash
git clone https://github.com/zou0613/blogger-distiller.git
cd blogger-distiller

# 依赖：Python 3.10+、ffmpeg、Google Chrome（macOS 默认路径）
pip3 install --user requests websockets
brew install ffmpeg          # macOS

# 火山方舟 API Key —— https://console.volcengine.com/ark
export ARK_API_KEY=<你的 Key>

# 30 秒冒烟测试
python3 scripts/extract/dy_extract.py "<任一抖音分享链接>" -o /tmp/dy_smoke_test
# ✓ 解析到 aweme_id → 探针 206/x.xMB → 提取成功   即环境就绪
```

| # | 说明 |
|:-:|---|
| 01 | 日志依次出现「解析到 aweme_id → 探针 206/xxMB → 提取成功」即环境就绪 |
| 02 | 该命令默认走直传路径，视频在方舟侧拉流，本地不落盘；探针失败自动回退为下载后上传 |
| 03 | 建议境内家庭/办公网络使用；海外或数据中心 IP 会显著提高风控概率 |
| 04 | 批量规模建议 Top 3~5，每条约 4 分钟提取耗时 |

## 肆 · 安全红线

| 红线 | 说明 |
|---|---|
| **RULE 01 无登录态下载** | headless Chrome 使用独立 profile，不含用户 cookie |
| **RULE 02 只读浏览** | cookie 仅留在用户浏览器内做只读查询；禁止点赞/关注等写操作 |
| **RULE 03 Key 不落盘** | API Key 只进环境变量，绝不写入任何文件 |
| **RULE 04 零伪造** | 所有结论必须溯源到真实提取结果或真实 API 返回 |

---

<div align="center">

**收片 ▸ 拆片 ▸ 蒸馏 ▸ 行动包**

[可视化项目主页](docs/index.html) · [GitHub](https://github.com/zou0613/blogger-distiller) · [MIT License](LICENSE) · [火山方舟文档](https://www.volcengine.com/docs/82379/1895586)

</div>
