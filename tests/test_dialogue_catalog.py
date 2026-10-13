import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
from types import SimpleNamespace

from app.agent.dialogue import DialogueContext, QuestionPlan, plan_question
from app.agent.service import AssistantService
from app.api.database import initialize
from app.auth import connect, insert_user
from app.knowledge.access import AccessPolicy, write_policy
from app.knowledge.catalog import ProductMetadata, answer_catalog, register_product
from app.knowledge.documents import add_knowledge_file


class DialogueCatalogTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.db = connect(self.root / 'company.db')
        self.addCleanup(self.temp.cleanup)
        self.addCleanup(self.db.close)
        initialize(self.db)
        insert_user(self.db, 'owner', 'test-password', 'admin')
        insert_user(self.db, 'staff', 'test-password', 'employee')
        self.owner = {'username': 'owner', 'role': 'admin'}
        self.staff = {'username': 'staff', 'role': 'employee'}
        self.service = AssistantService(self.root)
        self.env = patch.dict('os.environ', {'DEEPSEEK_API_KEY': 'unit-test'})
        self.env.start()
        self.addCleanup(self.env.stop)

    def document(self, name: str, code: str) -> int:
        path = self.root / (name + '.md')
        path.write_text('# ' + name + '\n负责部门为管理部。', encoding='utf-8')
        doc = add_knowledge_file(self.db, self.owner, path, 'general', self.root)
        with self.db:
            register_product(self.db, self.owner, doc, ProductMetadata(company='维科科技', code=code, name=name))
        return doc

    def test_catalog_deduplicates_and_filters_documents_before_count(self) -> None:
        first = self.document('产品10说明', 'WK-P10')
        second = self.document('产品10补充', 'wk-p10')
        secret = self.document('产品11保密', 'WK-P11')
        with self.db:
            write_policy(self.db, self.owner, secret, AccessPolicy.model_validate({'mode': 'restricted', 'groups': [{'usernames': ['owner']}]}))
        answer, ids = answer_catalog(self.db, self.staff, 'list_products', '维科科技')
        self.assertIn('共 1 个', answer)
        self.assertEqual(ids, [first, second])
        self.assertNotIn('产品11', answer)
        self.assertIn('共 2 个', answer_catalog(self.db, self.owner, 'count_products')[0])
        with self.assertRaises(PermissionError):
            register_product(self.db, self.staff, first, ProductMetadata(company='伪造', code='X', name='伪造'))

    def test_catalog_route_does_not_use_vector_search_or_generate_count(self) -> None:
        self.document('产品10', 'WK-P10')
        plan = QuestionPlan(domain='knowledge', confidence=1, standalone_question='维科一共有多少产品', knowledge_action='count_products', company='维科科技')
        with patch('app.agent.service.plan_question', return_value=plan), patch('app.agent.service.answer_from_knowledge') as rag:
            result = self.service.respond(self.db, self.staff, '维科一共有多少产品')
        rag.assert_not_called()
        self.assertIn('共 1 个', result.answer)
        self.assertTrue(result.feedback_allowed)

    def test_followup_partial_answer_then_explicit_handoff(self) -> None:
        doc = self.document('维科产品10经营看板', 'WK-P10')
        context = DialogueContext(question='介绍维科产品10', topic='介绍维科产品10经营看板', source_ids=[doc])
        plan = QuestionPlan(domain='knowledge', confidence=1, followup=True, standalone_question='维科产品10经营看板由谁完成交付？')

        def partial_answer(*args: object, **kwargs: object) -> str:
            kwargs['source_ids'].append(doc)
            kwargs['answer_status']['incomplete'] = True
            return '负责部门为管理部；当前检索资料未说明交付人员。'

        with patch('app.agent.service.plan_question', return_value=plan), patch('app.agent.service.answer_from_knowledge', side_effect=partial_answer) as rag, patch.object(self.service, 'get_embedder'), patch.object(self.service, 'get_store'), patch.object(self.service, 'get_reranker'):
            result = self.service.respond(self.db, self.staff, '由谁完成交付的？', dialogue_context=context.model_dump_json())
        self.assertEqual(rag.call_args.args[3], plan.standalone_question)
        self.assertIn('未说明交付人员', result.answer)
        self.assertEqual(self.db.execute('SELECT count(*) FROM handoffs').fetchone()[0], 0)
        with patch('app.agent.service.plan_question') as planner:
            done = self.service.respond(self.db, self.staff, '转人工', dialogue_context=result.dialogue_context)
        planner.assert_not_called()
        self.assertEqual(done.domain, 'human')
        row = self.db.execute('SELECT question FROM handoffs').fetchone()[0]
        self.assertIn('维科产品10', row)
        self.assertIn('由谁完成交付', row)
        again = self.service.respond(self.db, self.staff, '转人工', dialogue_context=done.dialogue_context)
        self.assertEqual(again.domain, 'clarify')
        self.assertEqual(self.db.execute('SELECT count(*) FROM handoffs').fetchone()[0], 1)

    def test_missing_evidence_and_uncertain_route_do_not_create_handoffs(self) -> None:
        with patch('app.agent.service.plan_question', return_value=QuestionPlan(domain='knowledge', confidence=1)), patch('app.agent.service.answer_from_knowledge', return_value=None), patch.object(self.service, 'get_embedder'), patch.object(self.service, 'get_store'), patch.object(self.service, 'get_reranker'):
            result = self.service.respond(self.db, self.staff, '谁交付维科产品10？')
        self.assertIn('回复“转人工”', result.answer)
        with patch('app.agent.service.plan_question', return_value=QuestionPlan(clarification='你指哪个产品？')):
            clarify = self.service.respond(self.db, self.staff, '谁交付的？')
        self.assertEqual(clarify.domain, 'clarify')
        self.assertIn('你指哪个产品', clarify.answer)
        self.assertEqual(self.db.execute('SELECT count(*) FROM handoffs').fetchone()[0], 0)
        cancel = self.service.respond(self.db, self.staff, '取消转人工', dialogue_context=result.dialogue_context)
        self.assertEqual(DialogueContext.model_validate_json(cancel.dialogue_context).pending_handoff, '')

    def test_clarification_keeps_the_question_until_product_is_selected(self) -> None:
        doc = self.document('维科产品10经营看板', 'WK-P10')
        context = DialogueContext(question='有哪些产品', topic='有哪些产品', source_ids=[doc], catalog_products=['产品02', '维科产品10经营看板'])
        with patch('app.agent.service.plan_question', return_value=QuestionPlan(clarification='你指哪个产品？')):
            result = self.service.respond(self.db, self.staff, '由谁交付？', dialogue_context=context.model_dump_json())
        updated = DialogueContext.model_validate_json(result.dialogue_context)
        self.assertEqual(updated.clarification_question, '由谁交付？')
        client = Mock()
        client.chat.completions.create.return_value = SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps({'domain': 'knowledge', 'confidence': 1.0, 'followup': True, 'standalone_question': '维科产品10经营看板由谁交付？'})))])
        planned = plan_question(client, '我指第10个', updated, ['维科产品10经营看板'], ['维科科技'])
        sent = json.loads(client.chat.completions.create.call_args.kwargs['messages'][1]['content'])
        self.assertEqual(sent['question_to_clarify'], '由谁交付？')
        self.assertFalse(planned.clarification)

    def test_revocation_clears_topic_before_planning(self) -> None:
        doc = self.document('秘密产品10', 'WK-P10')
        context = DialogueContext(question='秘密产品10', topic='秘密产品10', source_ids=[doc])
        with self.db:
            write_policy(self.db, self.owner, doc, AccessPolicy.model_validate({'mode': 'restricted', 'groups': [{'usernames': ['owner']}]}))
        with patch('app.agent.service.plan_question', return_value=QuestionPlan(clarification='指哪个产品？')) as planner:
            self.service.respond(self.db, self.staff, '由谁交付？', dialogue_context=context.model_dump_json())
        self.assertEqual(planner.call_args.args[2].topic, '')
        self.assertEqual(planner.call_args.args[3], [])

    def test_api_product_registration_and_handoff_retry(self) -> None:
        from fastapi.testclient import TestClient
        from app.api.app import create_app
        from uuid import uuid4
        doc = self.document('产品10说明', 'WK-P10')
        with TestClient(create_app(self.root / 'company.db'), headers={'X-Requested-With': 'employee-assistant'}) as client:
            self.assertEqual(client.post('/api/auth/login', json={'username': 'owner', 'password': 'test-password'}).status_code, 200)
            self.assertEqual(client.get(f'/api/knowledge/{doc}/product').json()['code'], 'WK-P10')
            invalid = client.post(f'/api/knowledge/{doc}/product', json={'company': '维科科技', 'code': '', 'name': '产品10'})
            self.assertEqual(invalid.status_code, 422)
            self.assertEqual(client.post('/api/auth/login', json={'username': 'staff', 'password': 'test-password'}).status_code, 200)
            self.assertEqual(client.post(f'/api/knowledge/{doc}/product', json={'company': '维科科技', 'code': 'BAD', 'name': '伪造'}).status_code, 403)
            conv = client.post('/api/conversations').json()['id']
            payload = {'request_id': str(uuid4()), 'question': '转人工：测试产品交付负责人'}
            first = client.post(f'/api/conversations/{conv}/messages', json=payload)
            second = client.post(f'/api/conversations/{conv}/messages', json=payload)
            self.assertEqual(first.status_code, 200, first.text)
            self.assertEqual(first.json(), second.json())
            self.assertEqual(self.db.execute('SELECT count(*) FROM handoffs').fetchone()[0], 1)

    def test_document_replacement_preserves_catalog_and_deletion_removes_it(self) -> None:
        from app.knowledge.lifecycle import replace_knowledge_file, delete_knowledge_document
        old = self.document('产品10说明', 'WK-P10')
        path = self.root / '新版说明.md'
        path.write_text('# 新版产品说明\n这是新版本。', encoding='utf-8')
        with patch('app.knowledge.lifecycle.index_document', return_value=1):
            new, count = replace_knowledge_file(self.db, self.owner, old, path, self.root, Mock(), Mock())
        answer, ids = answer_catalog(self.db, self.staff, 'count_products')
        self.assertIn('共 1 个', answer)
        self.assertEqual(ids, [new])
        delete_knowledge_document(self.db, self.owner, new, self.root, Mock())
        self.assertEqual(self.db.execute('SELECT count(*) FROM product_documents').fetchone()[0], 0)

    def test_planner_validates_identifiers_and_preserves_context(self) -> None:
        client = Mock()
        context = DialogueContext(question='介绍产品10', topic='介绍维科产品10经营看板')
        valid = {'domain': 'knowledge', 'confidence': .99, 'followup': True, 'standalone_question': '维科产品10经营看板由谁交付？'}
        client.chat.completions.create.return_value = SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=json.dumps(valid, ensure_ascii=False)))])
        result = plan_question(client, '由谁交付？', context, ['维科产品10经营看板'], [])
        self.assertFalse(result.clarification)
        self.assertTrue(result.followup)
        context.catalog_products = ['维科产品10经营看板', '维科产品02项目协作']
        self.assertIn('多个产品', plan_question(client, '由谁交付？', context, [], []).clarification)
        context.catalog_products = []
        valid['standalone_question'] = '维科产品11由谁交付？'
        client.chat.completions.create.return_value.choices[0].message.content = json.dumps(valid)
        self.assertTrue(plan_question(client, '由谁交付？', context, [], []).clarification)
        client.chat.completions.create.return_value.choices[0].message.content = '{broken'
        self.assertTrue(plan_question(client, '由谁交付？', context, [], []).clarification)


if __name__ == '__main__':
    unittest.main()
