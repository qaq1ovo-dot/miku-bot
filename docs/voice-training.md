# 音色训练流程

目标：把一个角色的声音训练成可用的音色模型，用于说话（TTS）和唱歌（翻唱）。

## 一、素材准备

### 1. 找素材

理想来源：游戏语音包、官方语音集、剧情录像。

本文以「提取游戏内语音」为例：

```bash
# 1) 解开 APK，拿到 FMOD 音频包
python tools/unzip_apk.py

# 2) 用 vgmstream 把 .fsb 里的每条语音导成 wav
vgmstream-cli -s 1 -o out.wav hero_vo.fsb

# 3) 用 ASR 转写，按关键词筛出目标角色
python voice/asr_clips.py
```

### 2. 切分与清洗

```bash
# 按静音切分，保留 2~10 秒的片段
python voice/slice_voice.py input.wav output_dir
```

要点：
- 单段 2~10 秒最好，太短学不到语调，太长不好对齐
- 去掉有背景音乐、有其他角色说话的片段
- 目标：**至少 3~5 分钟干净语音**，越多越好

## 二、训练方案对比

| 方案 | 素材需求 | 训练时间 | 效果 |
|---|---|---|---|
| **GPT-SoVITS 零样本** | 3~10 秒 | 无需训练 | 能听，但音色会飘向底模 |
| **RVC v2** | 2~10 分钟 | 20~40 分钟 | 音色贴合好，适合翻唱 |
| **DDSP-SVC** | 2~30 分钟 | 30~90 分钟 | 唱歌自然，但素材少容易「电音」 |
| **GPT-SoVITS 微调** | 10 分钟+ | 1~2 小时 | 综合最好，流程最复杂 |

经验结论：
- **少于 2 分钟** → 别训练，直接零样本
- **2~5 分钟** → RVC 最划算
- **10 分钟以上** → 可以上微调

## 三、RVC 训练步骤

### 1. 准备环境

```bash
# Python 3.12 + torch 2.7.1 cu128
uv venv .venv --python 3.12
uv pip install --python .venv/Scripts/python.exe torch==2.7.1+cu128 torchaudio==2.7.1+cu128 \
  --index-url https://mirrors.nju.edu.cn/pytorch/whl/cu128 --index-strategy unsafe-best-match
uv pip install --python .venv/Scripts/python.exe -r requirments_cu128_py312.txt
# 装完要把 torch 换回 cu128 版本（requirements 会把它降成 cpu 版）
uv pip install --reinstall --python .venv/Scripts/python.exe torch==2.7.1+cu128 torchaudio==2.7.1+cu128 \
  --index-url https://mirrors.nju.edu.cn/pytorch/whl/cu128 --index-strategy unsafe-best-match
```

### 2. 需要的预训练文件

放到 `assets/` 下：

```
assets/hubert_base/{config.json, preprocessor_config.json, pytorch_model.bin}
assets/rmvpe/rmvpe.pt
assets/pretrained_v2/f0G48k.pth
assets/pretrained_v2/f0D48k.pth
```

### 3. 预处理

```bash
python -m train.preprocess <数据集目录> 48000 4 <实验目录> False 3.0
python -m train.dataset.extract_f0 cuda 1 0 0 <实验目录> True
python -m train.dataset.extract_hubert_feature cuda:0 1 0 <实验目录> v2 True
```

### 4. 生成 filelist

格式：`wav路径|hubert特征路径|f0路径|f0nsf路径|speaker_id`

```
logs/seyui/0_gt_wavs/0_1.wav|logs/seyui/3_feature768/0_1.npy|logs/seyui/2a_f0/0_1.wav.npy|logs/seyui/2b-f0nsf/0_1.wav.npy|0
```

### 5. 训练

```bash
python -m train.train -se 50 -te 300 \
  -pg assets/pretrained_v2/f0G48k.pth -pd assets/pretrained_v2/f0D48k.pth \
  -g 0 -bs 8 -e seyui -sr 48k -f0 1 -l 1 -c 0 -sw 1 -v v2
```

产物：`assets/weights/<实验名>.pth`（约 54 MB）

### 6. 转换测试

```python
from rvc_python.infer import RVCInference

rvc = RVCInference(device="cuda:0")
rvc.load_model("seyui.pth", version="v2")
rvc.set_params(f0method="rmvpe", index_rate=0.5, protect=0.33, resample_sr=48000)
rvc.infer_file("input.wav", "output.wav")
```

## 四、常见问题

**Q：出来的声音有「电音味」**
素材太少或训练过头。试试：
- 减少 epoch（300 → 150）
- 检查素材有没有混入背景音乐
- 换 RVC 而不是 DDSP

**Q：结尾被截断**
RVC 输出采样率和输入不一致。转换前先把输入重采样到 48k，并把 `resample_sr` 设为 48000。

**Q：参考音频总被拒**
GPT-SoVITS 要求参考音频 3~10 秒，太短会报错。可以把相邻短句拼起来。

**Q：换气声被磨掉**
调大 `protect`（0.33 → 0.45），或调小 `index_rate`。
