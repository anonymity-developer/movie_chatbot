import unittest

from app.ai.graph.qa import cited_evidence
from app.ai.retrieval.evidence import Evidence, relevant_excerpt


class EvidenceExcerptTests(unittest.TestCase):
    def test_picks_relevant_passage_after_unrelated_intro(self):
        text = (
            "평론가는 영화의 흥행을 논했다. "
            "인터스텔라는 이론 물리학자 킵 손의 과학 자문을 받았다. "
            "다른 이야기가 이어진다."
        )
        excerpt = relevant_excerpt(text, "킵 손의 과학 자문을 받았다")
        self.assertIn("킵 손의 과학 자문", excerpt)
        self.assertNotIn("평론가는", excerpt)

    def test_citation_excerpt_follows_claim(self):
        evidence = [Evidence("근거1", "제목: 인터스텔라\n흥행 기록 이야기. 감독: 크리스토퍼 놀란", "movie_metadata", "인터스텔라", "https://example.org")]
        citations = cited_evidence("감독은 크리스토퍼 놀란이다[근거1].", evidence)
        self.assertIn("크리스토퍼 놀란", citations[0]["excerpt"])
        self.assertNotEqual(citations[0]["excerpt"], "제목: 인터스텔라")


if __name__ == "__main__":
    unittest.main()
