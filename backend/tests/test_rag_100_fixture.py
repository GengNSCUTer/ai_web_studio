"""扩展题集不能靠重复引用、伪引文或缺失槽位凑够数量。"""
import unittest
from scripts.build_rag_100 import validate_cases, make_batches, QUOTAS


class Rag100FixtureTests(unittest.TestCase):
    def material(self, index):
        return dict(document_file="资料.pdf", chunk_index=index,
                    quotes=["这是真实原文中的一段可核验说明文字，不能编造。"])

    def test_quote_ids_bind_back_to_original_text_and_chunk(self):
        materials=[self.material(1),self.material(2)]
        source={(m["document_file"],m["chunk_index"]):m["quotes"][0] for m in materials}
        slot=dict(slot="case1",question_type="synthesis",materials=materials)
        case=dict(slot="case1",question_type="synthesis",query="这两段说明了什么联系？",
            expected_answer_claims=["原文说明"],evidence=[dict(source_index=0,quote_index=0),dict(source_index=1,quote_index=0)])
        result=validate_cases([case],[slot],source)
        self.assertEqual([e["chunk_index"] for e in result[0]["expected_chunk_selectors"]],[1,2])
        case["evidence"]=[dict(source_index=0,quote_index=0)]*2
        with self.assertRaises(ValueError):validate_cases([case],[slot],source)

    def test_out_of_range_quote_cannot_create_a_target(self):
        materials=[self.material(1)]
        slot=dict(slot="case1",question_type="fact",materials=materials)
        case=dict(slot="case1",question_type="fact",query="原文的事实是什么？",
            expected_answer_claims=["原文事实"],evidence=[dict(source_index=0,quote_index=100)])
        with self.assertRaises(ValueError):validate_cases([case],[slot],{("资料.pdf",1):materials[0]["quotes"][0]})

    def test_fixed_slots_cover_95_positive_cases_and_reserve_five_negatives(self):
        docs={n:{i:"正文内容"*100 for i in range(30)} for n in ["企业.pptx","快速.pptx","python.pdf","Golang.pdf"]}
        batches=make_batches("chinese",docs)
        slots=[s for b in batches for s in b["slots"]]
        self.assertEqual(len(slots),95)
        self.assertEqual(len({s["slot"] for s in slots}),95)
        self.assertEqual(sum(QUOTAS.values()),100)
        self.assertEqual(QUOTAS["no_answer"],5)
