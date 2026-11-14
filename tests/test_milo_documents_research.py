"""Document scopes and explicit web research — synthetic local fixtures only."""
from __future__ import annotations

from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from colibri.milo_core.documents import (
    DocumentError, documents_context, extract_text,
    import_document, list_documents, remove_document, retrieve_documents,
)
from colibri.milo_core.web_research import (
    ResearchError, WebSource, parse_rss, query_for_research, source_context,
    source_footer, wants_research, web_enabled, set_web_enabled,
    _safe_url, search_results_reply,
)
from colibri.milo_core.conversation import MiloConversation, MiloProfile
from colibri.milo_core.store import LocalMemoryStore


class FauxProvider:
    def __init__(self):
        self.messages = None

    def stream(self, messages):
        self.messages = messages
        yield "Synthesised source-grounded response."


class DocumentAndResearchTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.store = LocalMemoryStore(self.root / "memory.sqlite3")
        self.files = self.root / "documents"

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def _upload(self, name, content, scope):
        file = self.root / name
        file.write_text(content, encoding="utf-8")
        return import_document(self.store.db, self.files, file, scope)

    def test_core_document_available_to_all_bots(self):
        self._upload("core.md", "Crystal fountain canon history verified.", "core")
        self._upload("code.txt", "Module code-testing procedure private.", "code")
        self._upload("research.txt", "Private research notebook.", "research")
        code = documents_context(self.store.db, "code", "Crystal fountain module")
        research = documents_context(self.store.db, "research", "Crystal fountain research")
        milo = documents_context(self.store.db, "milo", "Crystal fountain")
        self.assertIn("core.md", code)
        self.assertIn("code.txt", code)
        self.assertNotIn("research.txt", code)
        self.assertIn("research.txt", research)
        self.assertNotIn("code.txt", research)
        self.assertIn("core.md", milo)
        self.assertNotIn("code.txt", milo)
        self.assertNotIn("research.txt", milo)

    def test_document_remove_uses_actual_owner_scope(self):
        item = self._upload("private.md", "Only for code bot.", "code")
        self.assertEqual(len(list_documents(self.store.db, "code")), 1)
        self.assertFalse(remove_document(self.store.db, self.files, item["id"], "research"))
        self.assertTrue(remove_document(self.store.db, self.files, item["id"], "code"))
        self.assertEqual(list_documents(self.store.db, "code"), [])
        self.assertEqual(list((self.files / "bots" / "code").iterdir()), [])

    def test_no_symlinks_duplicates_executable_import_or_binary(self):
        self._upload("owner.md", "Synthetic text only.", "code")
        with self.assertRaisesRegex(DocumentError, "Already imported"):
            import_document(self.store.db, self.files, self.root / "owner.md", "code")
        script = self.root / "process.exe"
        script.write_bytes(b"\x00\x00\x01")
        with self.assertRaises(DocumentError):
            extract_text(script)
        with self.assertRaises(DocumentError):
            import_document(self.store.db, self.files, self.root / "owner.md", "../")

    def test_only_explicit_web_request_triggers(self):
        self.assertTrue(wants_research("Can you research this product online?"))
        self.assertTrue(wants_research("search the web for Vulkan fixes"))
        self.assertFalse(wants_research("Explain Vulkan shaders"))
        self.assertFalse(wants_research("this is a website I visited"))
        with self.assertRaises(ResearchError):
            query_for_research("Give me coding advice")

    def test_user_speech_web_search_is_explicit_and_grounded(self):
        phrase = "search for UFO online for me please"
        self.assertTrue(wants_research(phrase))
        self.assertEqual(query_for_research(phrase), "UFO")
        self.assertFalse(wants_research("Can you list the Downloads folder?"))
        self.assertFalse(wants_research("Can you open milo_icon_alpha_cleaned.png?"))
        from colibri.milo_core.web_research import WebSource
        result = search_results_reply(
            "UFO", [WebSource(
                "Verified test article", "https://example.org/ufo",
                "A real source snippet, not an invented Milo summary")])
        self.assertIn("https://example.org/ufo", result)
        self.assertIn("Search snippet only", result)
        self.assertIn("not independently verified", result)

    def test_bot_web_is_opt_in_and_stored_per_bot(self):
        self.assertFalse(web_enabled(self.store.db, "code"))
        self.assertFalse(web_enabled(self.store.db, "research"))
        set_web_enabled(self.store.db, "research", True)
        self.assertTrue(web_enabled(self.store.db, "research"))
        self.assertFalse(web_enabled(self.store.db, "code"))
        self.assertFalse(web_enabled(self.store.db, "milo"))
        with self.assertRaises(ResearchError):
            set_web_enabled(self.store.db, "milo", True)

    def test_web_blocks_internal_hosts_and_plain_http(self):
        for url in (
            "http://example.com/private", "https://127.0.0.1/api",
            "https://10.0.0.5/private", "https://localhost/",
            "https://router.local/", "https://[::1]/",
            "https://user:pass@example.com/private",
        ):
            with self.subTest(url=url), self.assertRaises(ResearchError):
                _safe_url(url)
        self.assertEqual(_safe_url("https://example.com/hello"),
                         "https://example.com/hello")

    def test_real_rss_parsing_and_citation_links(self):
        rss = (b'<?xml version="1.0"?><rss><channel>'
               b'<item><title>Source A</title><link>https://example.org/a</link>'
               b'<description>Example snippet</description></item>'
               b'<item><title>Local</title><link>https://127.0.0.1/p</link>'
               b'</item></channel></rss>')
        # This test verifies parser behavior; it makes no network calls.
        sources = parse_rss(rss)
        self.assertEqual(len(sources), 1)
        self.assertEqual(sources[0].url, "https://example.org/a")
        footer = source_footer(sources)
        self.assertIn("[W1]", footer)
        self.assertIn("https://example.org/a", footer)
        self.assertIn("snippet only", footer)

    def test_document_reference_and_web_source_reach_model_separately(self):
        self._upload("core.txt", "The crystal fountain is central.", "core")
        provider = FauxProvider()
        web = [WebSource("Public article", "https://example.org/a",
                         "Public snippet", "Fetched public page text")]
        from colibri.milo_core.web_research import source_context, source_footer
        chat = MiloConversation(
            provider, self.store,
            profile=MiloProfile("Research", "You are research specialist."),
            bot_id="research", bot_name="Research",
            research_context=source_context(web), answer_suffix=source_footer(web))
        response = chat.reply("Research the crystal fountain", chat_id="research-dm")
        prompt = provider.messages[0]["content"]
        self.assertIn("[D1]", prompt)
        self.assertIn("core.txt", prompt)
        self.assertIn("[W1]", prompt)
        self.assertIn("https://example.org/a", response)
        self.assertEqual(self.store.recent("research-dm")[-1]["content"], response)
        # Core reference text is not part of the outbound web search query.
        self.assertNotIn("central.", query_for_research("Research the crystal fountain"))


if __name__ == "__main__":
    unittest.main()
