"""Synthetic model directory tests; no inference engine or weights downloaded."""
from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from colibri.milo_core.local_engine import (
    EngineStartupError, LocalEngine, chosen_model,
    discover_models, managed_models_root, model_valid,
    discover_loose_gguf, gguf_target,
)


def fake_model(root: Path, name: str) -> Path:
    m = root / name
    m.mkdir(parents=True)
    (m / "config.json").write_text('{"model_type":"qwen3"}', encoding="utf-8")
    (m / "tokenizer.json").write_text("{}", encoding="utf-8")
    (m / "model.gguf").write_bytes(b"synthetic-not-a-real-model")
    return m


class ModelFoldersTest(unittest.TestCase):
    def test_creates_named_models_folder(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = managed_models_root(Path(tmp))
            self.assertEqual(root, Path(tmp) / "Models")
            self.assertTrue(root.is_dir())

    def test_detects_complete_nested_model(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = managed_models_root(Path(tmp))
            model = fake_model(root / "LLMs", "SmallQwen")
            self.assertEqual(discover_models(root), [model])
            self.assertTrue(model_valid(model))
            self.assertEqual(chosen_model(root, "LLMs/SmallQwen"), model.resolve())

    def test_does_not_include_incomplete_downloads(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = managed_models_root(Path(tmp))
            folder = root / "incomplete"
            folder.mkdir()
            (folder / "config.json").write_text("{}", encoding="utf-8")
            self.assertEqual(discover_models(root), [])

    def test_selected_model_cannot_escape_the_managed_folder(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = managed_models_root(Path(tmp))
            fake_model(Path(tmp), "external")
            self.assertIsNone(chosen_model(root, "../external"))

    def test_detects_loose_gguf_and_first_party_link_metadata(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = managed_models_root(Path(tmp))
            gguf = root / "Qwen3-4B.gguf"
            gguf.write_bytes(b"GGUF" + b"\0" * 100)
            self.assertEqual(discover_loose_gguf(root), [gguf.resolve()])
            sidecar = gguf_target(root, gguf)
            self.assertEqual(sidecar.name, "Qwen3-4B.colibri")
            sidecar.mkdir()
            (sidecar / "config.json").write_text("{}", encoding="utf-8")
            (sidecar / "tokenizer.json").write_text("{}", encoding="utf-8")
            (sidecar / "gguf.json").write_text(
                '{"format":"colibri-gguf-v1","mode":"link",'
                '"gguf":' + __import__("json").dumps(str(gguf)) + '}',
                encoding="utf-8")
            self.assertTrue(model_valid(sidecar))
            self.assertEqual(len(discover_models(root)), 1)
            self.assertTrue(discover_models(root)[0].samefile(sidecar))

    def test_missing_engine_is_not_started(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            model = fake_model(root, "Model")
            manager = LocalEngine(root, log_folder=root / "logs")
            with self.assertRaises(EngineStartupError):
                manager.start(model)
            self.assertIsNone(manager.process)


if __name__ == "__main__":
    unittest.main()
