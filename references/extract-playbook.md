# 拆片手册（Extract Playbook）

> 执行拆片阶段前必读。

把视频"拆解成数据"：上传视频到火山方舟，用豆包视觉模型完成**只提取、不分析**的多模态信息采集，输出「视频信息档案」（10 类核心信息）。

基于官方 Responses API + Files API（文档：火山方舟「视频理解」，`https://ark.cn-beijing.volces.com/api/v3`）。

脚本位置：本技能 `scripts/extract/`（`extract_video_info.py` + 提示词 `prompt.txt`，脚本自动读取同目录的 prompt.txt）。

## 依赖与环境

- Python 3.10+，`pip3 install --user requests`
- 环境变量 `ARK_API_KEY` 必填（火山方舟 API Key，https://console.volcengine.com/ark/region:cn-beijing/apikey）
- 可选环境变量：`ARK_MODEL`（默认 `doubao-seed-2-1-pro-260628`）、`ARK_BASE_URL`

## 核心机制（必须了解）

精简提示词（约 1k token，远小于 5000 token 上限）驱动提取 10 类核心信息，**默认单轮完成**（一次 API 调用）。口播原文优先完整保留，视觉细节从简。

**官方文档依据**（火山方舟《Agent 场景模型调用的正确姿势》）：「回答被截断」的原因是 **max_tokens 未显式传入或值过小**；Responses API 必须显式传 `max_output_tokens`，Agent 场景建议 **≥ 128000**。脚本默认显式传 `max_output_tokens=131072`。

10 类信息：

| 节 | 内容 |
|---|---|
| 1 | 视频基础信息（时长/画幅/人物出镜/音轨构成等） |
| 2 | 完整口播转写（最高优先级，带时间戳） |
| 3 | 字幕与画面文字（含强调样式） |
| 4 | 镜头与视觉结构（简要时间轴） |
| 5 | 人物（外观/动作/表情，简记） |
| 6 | 产品与品牌（含平台/App/专有名词） |
| 7 | 内容段落切分 |
| 8 | 数字与关键事实 |
| 9 | CTA 与结尾 |
| 10 | 声音与剪辑 |

- 已削减的低价值维度：统一时间轴大表、金句候选、重复元素、说话方式、事件顺序、逐镜头详录等
- 若输出被截断（脚本明确报告），用 `--split 2`/`--split 3` 分轮重跑（file_id 复用免上传）

## 使用方式

### 场景〇：抖音链接直传提取（2026-08-27 新增，免下载优先）

```bash
cd <技能目录>/scripts/extract
export ARK_API_KEY=<key>
python3 dy_extract.py "https://v.douyin.com/xxxx/" [-o 输出目录]
```

默认将抖音签名直链（多候选探针择优）直接交给方舟拉流提取，不在本地落盘视频；直链不可用或超 50MB 时自动回退「CDP 下载完整 mp4 → Files API 上传」（回退临时视频提取成功后自动清理）。脚本自动在本技能 `scripts/download/` 下定位内置的 `dy_dl.py`。适用：单条视频链接快速拆解。

### 场景一：本地视频提取（本技能主路径）

```bash
cd <技能目录>/scripts/extract
export ARK_API_KEY=<key>
python3 extract_video_info.py "<视频路径.mp4>"
```

- 默认输出到 `<视频同目录>/<视频名>_extract_<时间戳>/`
- 本地文件走 Files API 上传（方舟托管存储，**最大 512MB**，保留 7 天，file_id 可复用）
- 单轮调用，5 分钟视频实测全程约 5 分钟（上传 15s + 预处理 5s + 生成约 4 分钟）

### 场景二：公网 URL 视频（<50MB）

```bash
python3 extract_video_info.py --url "https://example.com/video.mp4"
```

### 场景三：复用已上传的 file_id（重跑/调整分段，免重新上传）

```bash
python3 extract_video_info.py --file-id file-xxxx "<本地视频路径.mp4>" --split 5
```

file_id 在上次运行完成时的控制台输出里，7 天内有效。

### 常用参数

| 参数 | 说明 |
|---|---|
| `-o <目录>` | 指定输出目录 |
| `--split N` | 分段轮数，默认 1（单轮全量）；仅输出被截断时才需要调高 |
| `--fps 2` | 抽帧采样率，默认 1.0；画面信息密集（快剪、大量屏幕文字）可提高到 2，代价是 token 消耗增大 |
| `--file-id <id>` | 复用 7 天内已上传的文件，跳过上传 |
| `--model <id>` | 覆盖默认模型，需选择支持视频理解的模型 |
| `--max-tokens N` | 单轮最大输出 token，默认 131072（官方推荐 ≥128000，防截断） |
| `--no-stream` | 排障用，禁用流式输出 |

### 完整流程（脚本自动完成）

1. **上传**：`POST /api/v3/files`（multipart，`purpose=user_data`，`preprocess_configs[video][fps]`）
2. **等待预处理**：轮询 `GET /api/v3/files/{file_id}` 直到 `status != processing`
3. **提取**：`POST /api/v3/responses`，input 为 `input_video`（file_id）+ `input_text`（提示词），流式接收（bytes 迭代 + UTF-8 解码，避免 `decode_unicode=True` 切坏多字节字符）
4. **落盘**：保存 report.md

## 输出说明

输出目录只有一个文件：

| 文件 | 内容 |
|---|---|
| `<YYYYMMDD_HHMM>.md` | 「视频信息档案」10 类核心信息（口播转写为骨干，带时间戳），文件名即报告创建时间，精确到分钟 |

运行元信息（file_id、token 用量、截断状态）只打印到控制台，不落盘：
- **file_id**：7 天内可 `--file-id` 复用免重新上传，需要重跑时从上次运行日志中取
- **token 用量**：input/output 分项汇总
- **截断告警**：出现 `⚠ 截断轮次` 时按提示用更高 `--split` 重跑

提示词的核心约束（详见 `scripts/extract/prompt.txt`）：
- **只提取，不分析**：禁止分析爆款原因、人设、用户心理、传播价值
- **证据分级**：【明确可见】【明确可听】【OCR/字幕可确认】【不确定】
- **不擅自补全**：听不清标【听不清】，看不清标【文字不清】，不凭常识补全
- **保留时间戳**：格式 `00:00–00:05`
- **不重复输出**：每条信息只在最合适的章节出现一次

向用户汇报结果时，优先基于档案中的**原始证据**，不要混入主观分析——主观分析属于蒸馏阶段。

## 失败模式与处理

| 症状 | 原因 | 处理 |
|---|---|---|
| `HTTP 401` | API Key 缺失/失效 | 检查 `ARK_API_KEY` |
| 上传 413 / 超限报错 | 文件 >512MB | 用 ffmpeg 压缩（`ffmpeg -i in.mp4 -vf scale=-2:720 -c:v libx264 -crf 26 out.mp4`）或配置 TOS（官方最大 2GB） |
| 某轮截断（日志明确报告"截断=True"） | 单轮输出超过 max_output_tokens（128k） | 用更高 `--split` 值重跑 + `--file-id` 复用免上传 |
| 预处理长时间 processing | 视频过长 | 正常等待（脚本每小时超时）；file_id 保留 7 天可复用 |
| 读取响应超时 | 长视频 + 非流式 | 默认已用流式；若仍超时，降低 `--fps` 或压缩视频 |
| 提取信息与视频对不上 | fps 太低漏帧 | 提高 `--fps`（如 2）重跑；file_id 仍在保留期内可免重新上传 |
| 输出未按排版规范（标题缺 `###` / 条目挤成段落 / 缺 `---` 分隔） | 模型偶发未遵守「输出排版规范 A-F」 | 低概率事件：重跑一次即可；prompt 已明文禁止此类输出 |
| dy_extract 直链探针全挂 / 直传报错 / 链接超 50MB | 签名失效或风控拦截方舟拉流 | 脚本已自动回退「下载后上传」，无需人工干预；仅当回退也失败时按上表排查 |

## 硬性约束

1. 禁止用模拟数据、编造内容填充档案——所有信息必须来自模型对视频的真实提取结果
2. API Key 只从环境变量或用户显式提供，绝不写入任何文件
3. 上传的文件会保存在方舟托管存储（默认 7 天），敏感视频注意此边界
