# Product backlog

Deferred work, newest first. Each item says where it came from and what "done" means.

## From the process-first Phase 1a spike (2026-10-17)

- [ ] **Calibrate the CTC score gate.** The 0.50 similarity gate from NaturalReader is undefined
  for CTC (it aligns the known text, so it cannot "mishear"). The spike only measured a
  structural pass rate (100%), so there is no calibrated score threshold yet. The code ships with
  one documented, uncalibrated constant.
  Done when: a threshold is chosen from labelled data (include numeral-heavy and messy text, not
  only clean prose) and the fraction of sentences falling back to `timing_source='estimated'` is
  measured for both Kokoro and Piper.
  Source: plan section "Spike results (Phase 1a)", skipped item "CTC score gate not calibrated".

- [ ] **Regenerate `backend/requirements.txt` from the lockfile.** The spike and the aligner work
  changed dependencies (`ctc-forced-aligner`, `onnx`, `faster-whisper` as dev) and
  `requirements.txt` is generated from `uv.lock`, never hand-edited (see `CLAUDE.md`).
  Done when: `requirements.txt` matches the lock and the Docker build still passes.

- [ ] **GPU measurements for the spike numbers.** Everything in "Spike results (Phase 1a)" is CPU
  only; there is no GPU on the dev box. Left out on purpose for now.
  Done when: Kokoro synthesis RTF, CTC aligner RTF and whisper RTF are measured on a GPU (or
  Modal) and the plan's render-time estimates are updated. Also re-measure Kokoro synthesis RTF
  on an idle CPU, since the spike's 0.511 was inflated by parallel test load.
