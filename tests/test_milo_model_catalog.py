"""Verify optional variant metadata before any large model download."""
import unittest

from colibri.milo_core.model_catalog import (
    MODEL_FILENAME, ModelDownloadError, model_asset_from_metadata,
)


class AlternativeModelTests(unittest.TestCase):
    def test_matching_quant_requires_sha256(self):
        entry = {"path": MODEL_FILENAME, "size": 2500000000,
                 "lfs": {"oid": "a" * 64}}
        result = model_asset_from_metadata([entry])
        self.assertEqual(result.size, 2500000000)
        self.assertEqual(result.sha256, "a" * 64)
        self.assertTrue(result.url.startswith("https://huggingface.co/"))
        with self.assertRaises(ModelDownloadError):
            model_asset_from_metadata([{**entry, "lfs": {}}])

    def test_cannot_accidentally_download_wrong_variant(self):
        with self.assertRaises(ModelDownloadError):
            model_asset_from_metadata([
                {"path": "Qwen3-4B-Thinking-Abliterated.gguf",
                 "size": 2500000000, "lfs": {"oid": "a" * 64}}
            ])


if __name__ == "__main__":
    unittest.main()
