# 致谢与第三方许可

本项目没有包含任何第三方项目的源代码，只在运行时依赖或调用它们。
下面列出实际用到的项目、许可证和用途。

---

## 运行时依赖

| 项目 | 许可证 | 用途 | 链接 |
|---|---|---|---|
| **AstrBot** | **AGPL-3.0** | 机器人框架（插件宿主） | https://github.com/AstrBotDevs/AstrBot |
| **NapCat** | 见其仓库 | QQ 协议端 | https://github.com/NapNeko/NapCatQQ |
| **aiohttp** | Apache-2.0 | 异步 HTTP | PyPI |
| **Pillow** | HPND | 图像处理（桌宠） | PyPI |
| **requests** | Apache-2.0 | 同步 HTTP | PyPI |
| **jieba** | MIT | 中文分词（记忆检索） | PyPI |
| **@jibaru/vocaloid-cli** | MIT | 生成 VOCALOID 工程 | npm |

## 音色与歌声相关

| 项目 | 许可证 | 用途 |
|---|---|---|
| **GPT-SoVITS** | MIT | 语音合成（本地） |
| **RVC-Project/RVC-WebUI** | MIT | 音色转换 |
| **DDSP-SVC** | 见其仓库 | 歌声转换 |
| **Demucs** | MIT | 人声分离 |
| **vgmstream** | 见其仓库 | 游戏音频解包 |

> 本项目**不包含**任何训练好的音色模型、音频素材或数据集。
> 训练数据与模型文件均在 `.gitignore` 中排除。

---

## 关于 AstrBot 的 AGPL-3.0

这是最需要注意的一点。

AstrBot 采用 **AGPL-3.0**。本项目的插件通过 import 使用它的 API，在它之上运行。

法律上，「插件是否属于衍生作品」是一个灰色地带（类似 WordPress 插件）。为了稳妥：

- `plugins/` 目录下的插件按 **AGPL-3.0** 授权，与 AstrBot 保持一致
- 其余部分（桌宠、agent、脚本）不依赖 AstrBot，按 **MIT** 授权

如果你要商用，建议咨询律师；个人使用没有这个问题。

---

## 关于角色形象

桌宠自带的是初音未来的同人像素立绘。

初音未来受 **Piapro Character License**（ピアプロ・キャラクター・ライセンス）约束：

- ✅ 允许非商业的同人创作与二次使用
- ❌ 不允许商业用途
- ❌ 不允许损害角色形象的用途

如果你想商用，请替换成自己的角色立绘（`assets/pets/<name>/`）。

---

## 关于语音克隆

仓库里的音色训练脚本是通用的，但：

- **不要提交**从游戏、动画、他人作品中提取的语音
- **不要分发**训练好的角色音色模型
- 克隆真人声音还涉及人格权，务必先取得同意

---

## 关于 QQ 自动化

用 NapCat 这类协议端登录 QQ，可能违反腾讯的服务条款，存在账号风险。

本项目仅供个人学习研究，请自行承担使用风险。

---

## 引用本项目时

如果你在别处提到这套东西，可以写：

```
参考：miku-bot（AstrBot 插件集 + 桌面宠物）
基于 AstrBot（AGPL-3.0）与 NapCat
```
