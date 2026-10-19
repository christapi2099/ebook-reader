# TTS engine candidates (research, 2026-10-17)

Status: **research only, nothing integrated.** Revisit after process-first Phase 1b ships
(`docs/plans/process-first-architecture.md`). Everything here comes from web searches and model
cards read on 2026-10-17; none of it was run on this machine (CPU only, no GPU). Verify specs
before relying on them.

Supertonic was evaluated earlier and **shelved** (plan: `.omo/plans/supertonic-second-engine.md`):
10 preset voices, utterance-level duration predictor (no per-word times), and its Voice Builder
cloning service was announced to shut down 2026-08-31.

## dots.tts (deep dive)

| Item | Finding | Source |
|---|---|---|
| Publisher | Xiaohongshu (RedNote) hi-lab with Shanghai Jiao Tong University. Preprint 2026-06-05. | [Baidu Baike](https://baike.baidu.com/en/item/dots.tts/2414187), search results |
| Where it lives | HF org `dots-studio` (also seen as `rednote-hilab`); GitHub `studio-dots-ai/dots.tts` (also seen as `rednote-hilab/dots.tts`). Names are inconsistent across sources. | [HF model card](https://huggingface.co/dots-studio/dots.tts-base), [GitHub](https://github.com/studio-dots-ai/dots.tts) |
| Size / format | 2B parameters, BF16 safetensors | model card |
| Architecture | Fully continuous autoregressive TTS, no discrete codec tokens. Frozen 48 kHz AudioVAE with a BigVGAN-style causal decoder; LLM backbone initialised from Qwen2.5-1.5B-Base consuming BPE text directly (no phonemes); AR flow-matching (DiT) head; CAM++ speaker x-vector side input. | model card |
| Training | About 1.5M hours of speech (base checkpoint) | model card |
| Checkpoints | `base` (fine-tuning start), `soar` (highest speaker similarity), `mf` (MeanFlow distilled, NFE=4, low latency), plus `mf-2steps` / `mf-1step` variants per the GitHub README | model card, search results |
| Voice cloning | Yes, zero-shot. Recommended mode = reference wav + exact transcript ("continuation cloning"). x-vector-only mode needs no transcript but gives lower similarity. Eval used a ~3 s reference; min/max reference length is not stated. | model card, search results |
| Languages | **Unconfirmed.** Model card cites a 24-language benchmark (MiniMax Multilingual) and says Arabic, Hindi, Turkish and Vietnamese have higher WER. Blogs claim 100+ / 107; treat 24 as the best-supported figure. | model card vs [promptit.ai](https://promptit.ai/blog/71-dots-tts-open-source-voice-cloning/) |
| Output rate | Not stated explicitly. AudioVAE is 48 kHz, so probably 48 kHz; confirm by running it. | model card |
| Speed / RTF / VRAM / CPU | **Not stated anywhere I could read.** The `mf` distilled variant is the low-latency one. | model card |
| Timestamps | **None.** Neither API nor snippet returns alignments, so word timings would need the aligner. | model card |
| License | Apache-2.0 | model card |
| Quality claims (model card) | Seed-TTS-Eval avg WER / SIM: dots.tts-base 2.92 / 78.8 vs Qwen3-TTS 1.7B 3.07 / 74.5 and VoxCPM2 2.0B 3.65 / 76.7. MiniMax Multilingual (24 langs): WER 6.6, SIM 83.5. Self-reported. | model card |
| Limitations | Speech only (no singing or sound effects); intended for research and authorised deployment, with warnings about impersonation and fraud. | model card |
| Ports / wrappers | [MLX port for Apple Silicon](https://github.com/sb1992/dots-tts-mlx); [DotsTTS-Studio](https://github.com/timoncool/DotsTTS-Studio) (third-party Windows wrapper, NVIDIA GPU); [vllm-omni request](https://github.com/vllm-project/vllm-omni/issues/4218) | search results |

Inference snippet (from the card):

```python
from dots_tts.runtime import DotsTtsRuntime
runtime = DotsTtsRuntime.from_pretrained("dots-studio/dots.tts-base", precision="bfloat16")
result = runtime.generate(text="...", prompt_audio_path="ref.wav",
                          prompt_text="exact transcript of ref.wav",
                          num_steps=10, guidance_scale=1.2)
# result["audio"], result["sample_rate"]
```

Install: Python 3.10, `pip install git+https://github.com/studio-dots-ai/dots.tts.git -c <constraints/recommended.txt>`.

### Assessment for this app
- **Likely quality leader** among the open cloning models on its own benchmarks, but those are self-reported.
- **Heavy**: 2B parameters with an LLM backbone and flow matching. Almost certainly needs a GPU, so it belongs on Modal or a GPU box, not this CPU-only machine. Try the `mf` distilled checkpoint first.
- **Open questions to answer before integrating**: real RTF and VRAM, true output sample rate, long-form stability over a 500-sentence chapter (AR models can skip or repeat), the official language list.
- Needs the aligner (CTC preferred, since the text is known) for word timings, and a 48 kHz to 24 kHz resample step in the render path.

## Other candidates (shorter notes)

| Engine | Cloning | Size / speed (claimed) | License | Notes |
|---|---|---|---|---|
| [Chatterbox](https://resemble.ai/chatterbox) (Turbo 350M EN, Multilingual 500M 23+ langs, [Nano 110M EN](https://huggingface.co/ResembleAI/chatterbox-nano)) | Yes, about 5 s reference | Nano: about 3x real time on an 8-core CPU (model card). Turbo latency claims conflict (sub-200 ms vs about 75 ms). | MIT | Outputs carry a PerTh watermark; do not remove. Best first candidate because Nano may run on CPU. |
| [LuxTTS](https://lilting.ch/en/articles/luxtts-lightweight-voice-cloning) | Yes, about 3 s | Under 1 GB VRAM; claims faster than real time on CPU; 48 kHz | Apache-2.0 (project pages; one blog says MIT) | EN/ZH mainly. |
| [VoxCPM](https://huggingface.co/openbmb/VoxCPM1.5) (VoxCPM2: 2B, 30 langs) | Yes, plus voice design from text | 4.2 GB; RTF about 0.13 to 0.3 on RTX 4090 (third-party figures); 48 kHz | Apache-2.0 | GPU. |
| [OmniVoice](https://huggingface.co/k2-fsa/OmniVoice) | Yes, 3 to 10 s reference | 600+ languages; reported RTF 0.025; 24 kHz | Reported Apache-2.0, **not confirmed on the official repo** | NVIDIA or Apple Silicon only. Wants speaker consent. |
| [Qwen3-TTS](https://rits.shanghai.nyu.edu/ai/%F0%9F%93%A2-qwen3%E2%80%91tts-open%E2%80%91source-text%E2%80%91to%E2%80%91speech-tts-family/) (0.6B, 1.7B) | Base models clone; VoiceDesign / CustomVoice variants | 2.5 GB / 4.5 GB; 10 languages | Apache-2.0 | Good all-round quality option on Modal. |

Suggested order after Phase 1b: Chatterbox Nano (CPU-capable cloning), then LuxTTS, then dots.tts
`mf` and Qwen3-TTS on Modal.

## Cross-cutting integration notes
1. **Why the architecture helps**: a book is rendered once in the background, so slow engines are
   fine. A new voice or reference clip is a new fingerprint and re-renders while the old render
   stays readable.
2. **Word timings**: none of these return per-word times, so the aligner (spike: CTC vs
   faster-whisper) becomes mandatory, not a fallback.
3. **Sample rate**: pipeline assumes 24 kHz; 48 kHz engines need one explicit resample step.
4. **Long-form stability**: run a 500-sentence chapter test per engine before shipping it.
5. **Consent and licence**: cloning from third-party recordings (for example LibriVox readers)
   should use clips the user supplies or that are clearly permitted; several model cards ask for
   speaker consent and warn against impersonation. Keep watermarks (Chatterbox PerTh) intact.
6. **Engine plumbing**: each engine adds an `engine_family` value (part of the render
   fingerprint) and a voice listing. Piper's voice list is currently a hardcoded catalog in
   `backend/routers/voices.py` and should become discovery-based first.
