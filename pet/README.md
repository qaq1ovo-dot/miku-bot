# Miku Pet · 可被机器人控制的桌面宠物

一个轻量的 Windows 桌面宠物：透明窗口、像素动画、气泡对话、语音播报，
并且**可以被远程机器人控制**——QQ 里她说了什么，桌面上就同步做出反应。

---

## 特性

- 🪟 **透明置顶窗口**，可拖动、双击互动、右键菜单
- 🎞️ **帧动画状态机**：idle / talking / thinking / clicked / dragged / success / error …
- 💬 **气泡对话**：说的话显示在头顶
- 🔊 **语音播报**：对接本地 GPT-SoVITS，说出来的话同步播放
- 🔌 **HTTP 控制接口**：任何程序都能让她说话、换表情、移动
- 🧩 **易扩展**：加状态只改 JSON，加接口只写一个函数

---

## 快速开始

### 1. 依赖

```bash
pip install pillow requests
```

（tkinter 是 Python 自带的，Windows 上默认可用）

### 2. 配置

把 `config.example.json` 复制成 `config.json`：

```json
{
  "pet":  {"asset_dir": "assets/pets/miku_pixel", "scale": 1.5, "start_pos": [1400, 650]},
  "api":  {"host": "127.0.0.1", "port": 9890, "token": "换成你的随机字符串"},
  "tts":  {"enable": true, "endpoint": "http://127.0.0.1:9880/tts",
           "ref_audio": "E:/参考音频/xxx.wav", "ref_text": "参考音频对应的文本",
           "text_lang": "ja"},
  "behavior": {"idle_to_sleep_sec": 1800, "bubble_sec": 6}
}
```

### 3. 启动

```bash
python main.py
```

---

## 控制接口

所有接口只监听 `127.0.0.1`，写操作需要 `X-Auth-Token` 头。

| 方法 | 路径 | 参数 | 说明 |
|---|---|---|---|
| GET | `/ping` | - | 存活检查 |
| GET | `/status` | - | 当前状态、空闲秒数、可用状态列表 |
| POST | `/say` | `text`, `voice`, `seconds` | 说话（气泡 + 可选语音） |
| POST | `/bubble` | `text`, `seconds` | 只显示气泡 |
| POST | `/state` | `name`, `duration` | 切换动画状态 |
| POST | `/move` | `x`, `y` | 移动窗口 |
| POST | `/quit` | - | 退出 |

示例：

```bash
curl -H "X-Auth-Token: your-token" -X POST http://127.0.0.1:9890/say \
  -d '{"text":"おはよう！","voice":true}'
```

---

## 和 QQ 机器人联动

桌宠跑在自己电脑上，AstrBot 跑在服务器上，用 SSH 反向隧道连起来：

```bash
# 在自己电脑上执行（把 9890 转发到服务器的 19890）
ssh -R 19890:127.0.0.1:9890 user@your-server
```

然后安装 `astrbot_plugin_miku_pet2` 插件，把 `endpoint` 设为
`http://127.0.0.1:19890`，token 和桌宠配置一致。

效果：

```
QQ 里她说「在呀，一直在这儿呢。」
        ↓
桌面上的小人切到 talking 状态，头顶冒出同样的气泡，同时播放语音
```

---

## 自定义立绘

在 `assets/pets/<名字>/` 放一个 `pet.json`：

```json
{
  "name": "my_pet",
  "default": "idle",
  "spritesheet": {"file": "spritesheet.png", "frame_w": 128, "frame_h": 128, "cols": 8},
  "states": {
    "idle":    {"row": 0, "frame_count": 6, "fps": 6,  "loop": true},
    "talking": {"row": 1, "frame_count": 8, "fps": 10, "loop": true}
  }
}
```

两种素材组织方式都支持：

- **雪碧图**：一张图按行列切帧（`row` + `frame_count`）
- **序列帧**：每个状态一组 png（`frames: ["raw/idle_0.png", ...]`）

---

## 扩展开发

### 加一个新接口

在 `core/actions.py` 里加个函数并注册进返回的字典：

```python
def wave(body: dict) -> dict:
    win.root.after(0, lambda: win.machine.transition("notice", duration=2))
    return {"ok": True}

return {..., "wave": wave}
```

### 加一个新状态

只改 `pet.json`，不用动代码。

### 换成 Live2D

替换 `ui/window.py` 里的渲染层即可——状态机、API、气泡都是独立的。

---

## 目录结构

```
pet-app/
├─ main.py
├─ config.example.json
├─ core/
│  ├─ config.py     配置
│  ├─ assets.py     立绘加载
│  ├─ state.py      状态机
│  ├─ server.py     HTTP 服务
│  ├─ actions.py    接口实现
│  └─ tts.py        语音合成与播放
├─ ui/
│  └─ window.py     窗口与交互
└─ assets/pets/
   └─ miku_pixel/   像素初音（含 spritesheet）
```

---

## 已知限制

- 透明用的是 Windows 的 `-transparentcolor`，只对硬边像素图效果好
- 语音播放依赖 `winsound`，只支持 wav
- 没有系统托盘图标（需要额外依赖，后续可加）

---

## License

MIT

---

## 三种运行方式

### A. 完全独立（不装任何机器人）

配置 `llm` 段后，右键点她 → **「和我说话…」**，直接输入就能聊，
她回答的同时会说出来。适合先单独玩。

```json
"llm": {
  "base_url": "https://api.deepseek.com",
  "api_key": "sk-...",
  "model": "deepseek-chat",
  "system_prompt": "你叫初音未来……"
}
```

### B. 接 QQ 机器人（AstrBot）

用 SSH 隧道把服务器的 19890 指到本机 9890，装 `astrbot_plugin_miku_pet2` 即可。

### C. 接其他平台

桌宠本体不关心来源，任何程序只要能 POST `/say` 就能驱动它。

`integrations/` 里附了一个 **Telegram 适配器** 作为示例：

```bash
set TG_TOKEN=123456:ABC...
set PET_TOKEN=你的桌宠token
set LLM_KEY=sk-...
python -m integrations.telegram_bot
```

它收到 Telegram 消息 → 问 LLM → 同时让桌宠说话 + 回复 Telegram。

自己写别的平台适配器，照抄这个文件改 API 调用即可。