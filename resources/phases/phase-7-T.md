# Phase 7-T — Modelserver Unit Tests

**Status:** ✅ All passed (2026-05-21)
**Scope:** 1 test file, 13 tests
**File:** `tests/test_phase7_modelserver.py`

All tests mock the heavy ML libraries (torch, transformers, sentence-transformers) so
they run without a GPU or trained weights. Docker integration (real weights) is covered
by the docker-compose smoke test.

---

## Test Inventory

### weights.py — 2 tests

**`test_weights_not_found_on_missing_model_card`**
- Creates a mock MinIO client whose `fget_object` raises `S3Error(code="NoSuchKey")`
- Calls `download_and_verify(mc)`
- Asserts `WeightsNotFound` is raised
- Verifies: missing model card → safe mock-mode fallback (not a crash)

**`test_weights_sha256_mismatch_raises_runtime_error`**
- Writes a `model_card.json` with `weights_sha256 = "deadbeef" * 8` (wrong hash)
- Writes fake bytes to `model.safetensors` (so the file exists and can be hashed)
- Calls `download_and_verify(mc)`
- Asserts `RuntimeError` matching `"SHA-256 mismatch"` is raised
- Verifies: corrupted weights → hard boot failure (data integrity guarantee)

---

### classifier.py — 1 test

**`test_classifier_predict_returns_label_and_confidence`**
- Patches `sys.modules["torch"]` and `sys.modules["transformers"]` with `MagicMock`
  so the test runs without GPU packages installed
- Wires `mock_probs.argmax() = 0`, `mock_probs[0] = 0.9`
- Instantiates `Classifier({"id2label": {"0": "bug", "1": "enhancement", "2": "documentation"}})`
- Calls `clf.predict("null pointer exception in merge")`
- Asserts: `label == "bug"`, `0.0 < confidence <= 1.0`
- Verifies: id2label mapping from model card is used correctly

---

### classical.py — 1 test

**`test_classical_classifier_predict`**
- Uses **real** (minimal) sklearn objects — MagicMock is not picklable
- Fits a `TfidfVectorizer` + `LogisticRegression` on 3 dummy texts/labels
- Pickles both to `tmp_path`
- Monkeypatches `WEIGHTS_DIR = tmp_path`
- Instantiates `ClassicalClassifier()` and calls `clf.predict("add support for new dtype")`
- Asserts: label in `["bug", "enhancement", "documentation"]`, confidence in (0, 1]
- Verifies: pkl load + vectorizer → LR pipeline works end-to-end

---

### reranker.py — 2 tests

**`test_reranker_returns_score_per_passage`**
- Pops `modelserver.app.reranker` from `sys.modules` (forces reimport with mock)
- Patches `sentence_transformers` in `sys.modules` + patches `CrossEncoder`
- Wires `mock_model.predict.return_value = [0.9, 0.4, 0.7]`
- Calls `r.rerank("null pointer bug", ["passage A", "passage B", "passage C"])`
- Asserts: `len(scores) == 3`, `scores[0] ≈ 0.9`, `scores[2] ≈ 0.7`

**`test_reranker_empty_passages`**
- Same setup, passes empty list to `rerank()`
- Asserts: returns `[]` without error
- Verifies: edge case — no passages to score

---

### ner.py — 3 tests

These tests use the **real** spaCy EntityRuler (no GPU required — pure regex/phrase matching).

**`test_ner_extracts_known_package`**
- Input: `"pandas raises ValueError when merging on NaN keys"`
- Asserts: `"PACKAGE" in labels`, `"pandas" in texts`
- Verifies: phrase pattern match on known package list

**`test_ner_extracts_exception`**
- Input: `"This raises a ValueError in the core."`
- Asserts: `"ValueError" in texts`
- Verifies: `EXCEPTION` regex pattern `^[A-Z]\w*(Error|Exception|Warning)$`

**`test_ner_deduplicates_entities`**
- Input: `"pandas pandas pandas"`
- Asserts: exactly 1 entity with text `"pandas"` (not 3)
- Verifies: `(text, label)` deduplication logic in `extract()`

---

### summarizer.py — 1 test

**`test_summarizer_returns_string`**
- Patches `OpenAI` constructor + wires `chat.completions.create()` to return
  `MagicMock(choices=[MagicMock(message.content="This is a summary.")])`
- Instantiates `Summarizer("sk-test")` and calls `s.summarize("Issue: crash on merge...")`
- Asserts: `result == "This is a summary."`
- Verifies: OpenAI response is passed through without modification

---

### main.py — 2 tests

**`test_health_returns_mock_mode_when_no_weights`**
- Sets `ms_main._mode = "mock"` directly (bypasses lifespan — ASGITransport skips it)
- `GET /health` via `AsyncClient`
- Asserts: `status_code == 200`, `json["mode"] == "mock"`

**`test_api_boots_when_modelserver_in_mock_mode_and_flag_off`**
- `monkeypatch.chdir(tmp_path)` — avoids loading project `.env`
- Sets `VAULT_ADDR` and `VAULT_TOKEN` env vars
- Instantiates `Settings()` with no `REQUIRE_REAL_MODELSERVER` set
- Asserts: `s.require_real_modelserver is False`
- Verifies: default allows mock mode (safe for development)

**`test_api_setting_can_be_enabled`**
- Same setup + `monkeypatch.setenv("REQUIRE_REAL_MODELSERVER", "true")`
- Calls `config.get_settings()`
- Asserts: `s.require_real_modelserver is True`
- Verifies: env var correctly activates the strict boot guard

---

## Pass Criteria — All Met ✅

```
pytest tests/test_phase7_modelserver.py -v

test_weights_not_found_on_missing_model_card         PASSED
test_weights_sha256_mismatch_raises_runtime_error    PASSED
test_classifier_predict_returns_label_and_confidence PASSED
test_classical_classifier_predict                    PASSED
test_reranker_returns_score_per_passage              PASSED
test_reranker_empty_passages                         PASSED
test_ner_extracts_known_package                      PASSED
test_ner_extracts_exception                          PASSED
test_ner_deduplicates_entities                       PASSED
test_summarizer_returns_string                       PASSED
test_health_returns_mock_mode_when_no_weights        PASSED
test_api_boots_when_modelserver_in_mock_mode_and_flag_off  PASSED
test_api_setting_can_be_enabled                      PASSED

13 passed in 5.59s
```

**Total suite after Phase 7: 119/119 passing**
(106 from Phases 1-5 + 13 new Phase 7 tests)
