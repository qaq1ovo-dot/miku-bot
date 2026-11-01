# Miku Bot · 初音未来 QQ 机器人套件

一套基于 **AstrBot + NapCat** 的个人向 QQ 机器人方案，包含写歌、翻唱、长期记忆、主动聊天、联网搜索等模块。

> 这是一个「有性格」的机器人：她不只会回答问题，还会自己写歌、写说说、主动找你说话。

---

## 功能一览

| 模块 | 说明 |
|---|---|
| 🎵 **写歌** | 自动作词、编曲、生成 VPR 工程，导出人声与音频 |
| 🎤 **翻唱** | Demucs 分离人声 → RVC 转换音色 → 混音导出 |
| 🧠 **长期记忆** | 抽取对话事实，按重要性 + 时间衰减检索 |
| 💭 **内心独白** | 定时生成内心活动，影响主动发言 |
| 📢 **主动消息** | 随机间隔主动找你聊天，会判断你有没有回复 |
| 🌐 **联网搜索** | Bing 搜索 + 网页正文抓取，无需 API key |
| 🖥️ **电脑控制** | 看屏幕、点鼠标、敲键盘（通过本地 agent） |
| 📱 **桌宠同步** | QQ 里的回复同步到桌面宠物 |
| 🔌 **掉线补偿** | 重连后检查漏掉的消息并补回复 |

---

## 目录结构

```
miku-bot/
├─ plugins/                  # AstrBot 插件
│  ├─ astrbot_plugin_songmaker/     写歌 + VOCALOID 宏
│  ├─ astrbot_plugin_miku_memory/   长期记忆
│  ├─ astrbot_plugin_miku_mind/     内心独白
│  ├─ astrbot_plugin_miku_pet/      桌宠同步
│  ├─ astrbot_plugin_pc_screen/     看屏幕 / 控制电脑
│  ├─ astrbot_plugin_websearch/     联网搜索
│  └─ astrbot_plugin_catchup/       掉线补偿
├─ agent/                    # 本地执行器
│  ├─ pc_agent_v2.py               电脑控制 HTTP 服务
│  └─ miku_runner.py               远程任务执行器
├─ voice/                    # 音色处理脚本
│  ├─ bili_dl.py                   B 站音频下载
│  ├─ slice_voice.py               按静音切片
│  └─ asr_clips.py                 批量语音转写
├─ docs/
│  ├─ architecture.md        架构与数据流
│  └─ voice-training.md      音色训练流程（RVC / DDSP）
├─ .env.example              环境变量模板
└─ README.md
```

---

## 快速开始

### 1. 环境要求

- Windows / Linux
- Python 3.10+
- [AstrBot](https://github.com/AstrBotDevs/AstrBot) v4.x
- [NapCat](https://github.com/NapNeko/NapCatQQ)（QQ 协议端）

### 2. 安装插件

把 `plugins/` 下需要的目录复制到 AstrBot 的插件目录：

```
<AstrBot>/data/plugins/
```

重启 AstrBot，插件会自动加载。

### 3. 配置

每个插件在 AstrBot WebUI 的「插件配置」里都有独立配置项。
敏感信息（token、API key）也可以通过环境变量提供，见 `.env.example`。

### 4. 本地 agent（可选，用于电脑控制）

```bash
cd agent
set PC_AGENT_TOKEN=<你的随机 token>
python pc_agent_v2.py
```

它只监听 `127.0.0.1`，建议配合 SSH 隧道暴露给服务器：

```bash
ssh -R 19883:127.0.0.1:9883 user@server
```

---

## 各插件简介

### 🎵 songmaker

用 LLM 生成「分节 + 假名注音」的结构化歌词，再交给编曲器生成旋律与和弦，
最终导出 `.vpr`（VOCALOID 工程）或直接驱动 VOCALOID 导出音频。

关键点：
- 歌词带假名，解决日文被当中文念的问题
- 音素直接写进 `.vpr` 的 `sequence.json`，绕开 GUI 批量修改

### 🧠 miku_memory

- 每 8 轮对话抽取一次事实
- 每条事实带 `imp`（重要性 0~1）
- 检索公式：`关键词重合 × (0.4 + 重要性) × e^(-天数/45)`
- 超过 3 天且有效分 < 0.12 的事实会被淡忘

数据存 `facts.jsonl`，纯文本、可读、可手工编辑。

### 💭 miku_mind

定时写「内心独白」，并把这些想法作为上下文提供给主动消息，
让她主动开口时更像「有话说」而不是随机搭话。

### 🌐 websearch

不需要任何 API key：

- 抓 Bing 搜索结果页（备用百度）
- 提供 `search_web` 和 `read_webpage` 两个 LLM 工具

### 🔌 catchup

重连后拉取最近的历史消息，找出「比自己上次在线时间更新、且不是自己发的」消息，
用 LLM 补一条回复，最多 2 条，避免刷屏。

---

## 注意事项

- 本项目是**个人使用**向的，没有做多用户隔离
- 涉及账号、token 的配置请放在环境变量或 AstrBot 配置里，**不要提交到仓库**
- 训练音色时请确保素材来源合法

---

## License

MIT

## 她的「内在」系统

除了聊天，她还有一套会自己运转的内部状态，详见 [docs/inner-life.md](docs/inner-life.md)。

| 插件 | 作用 | 命令 |
|---|---|---|
| `miku_psyche` | 情绪四维（心情/精力/社交欲/好奇） | `/状态` |
| `miku_mind` | 定时内心独白 | `/心里话` |
| `miku_self` | 每日自我叙事，形成稳定的「我是谁」 | `/自我` |
| `miku_learn` | 从你的纠正里学规则 | `/经验` |
| `miku_rewrite` | 受审批的自我改写（改准则/性格参数） | `/改写` |
| `miku_heartbeat` | 心跳：没人时自己决定做点什么 | `/心跳` |
| `usage` | token 用量与花费统计 | `/用量` |

设计原则：**所有自我改变都要经主人批准**，所有数据都是可读的纯文本。
---

## 许可

- `pet/`、`agent/`、`voice/`、`tools/`：**MIT**
- `plugins/`：**AGPL-3.0**（与 AstrBot 框架一致）

第三方依赖、角色形象授权、语音克隆与 QQ 自动化的注意事项，
见 [CREDITS.md](CREDITS.md)。