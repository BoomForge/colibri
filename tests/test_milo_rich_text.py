"""Untrusted generated code is displayed and copied, never executed."""
import unittest
from desktop.rich_text import split_fenced_blocks, visual_rows


class FencedCodeTests(unittest.TestCase):
    def test_mixed_text_code_blocks(self):
        fence = chr(96) * 3
        source = ("Here's Python:\n" + fence + "python\n"
                  + "print('hello')\n  # indent\n" + fence
                  + "\nAnd we're done.")
        blocks = split_fenced_blocks(source)
        self.assertEqual([b.kind for b in blocks], ["text", "code", "text"])
        self.assertEqual(blocks[1].language, "python")
        self.assertEqual(blocks[1].content, "print('hello')\n  # indent\n")
        self.assertIn("And we're done.", blocks[2].content)

    def test_streaming_unclosed_fence(self):
        source = "Intro\n" + chr(96) * 3 + "powershell\nGet-ChildItem\n"
        blocks = split_fenced_blocks(source)
        self.assertEqual([b.kind for b in blocks], ["text", "code"])
        self.assertEqual(blocks[1].content, "Get-ChildItem\n")

    def test_plain_text_not_executed(self):
        source = "import os; os.remove('anything')"
        self.assertEqual(split_fenced_blocks(source)[0].content, source)

    def test_unknown_language_safe(self):
        blocks = split_fenced_blocks(chr(96) * 3 + "my-lang\n1\n" + chr(96) * 3)
        self.assertEqual(blocks[0].language, "my-lang")


if __name__ == "__main__":
    unittest.main()
