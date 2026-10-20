"""Instructions stay available without native resources or task mutations."""
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from flask import Flask
from modules.workbench_routes import create_workbench

PROJECT = Path(__file__).resolve().parents[1]


class SystemGuideTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.project = self.root / 'program'
        self.docs = self.project / 'docs'
        self.docs.mkdir(parents=True)
        self.text = ('# 操作说明\n\n已有记录无需重新求解。\n\n'
                     '[联调记录](custom_material_full_chain_20261020.md)\n\n'
                     '| 名称 | 参数 |\n| --- | --- |\n| 测试材料 | 0.85 |\n\n'
                     '<script>alert("example")</script>\n')
        self.source = self.docs / 'system_user_guide.md'
        self.source.write_bytes(self.text.encode())
        app = Flask('guide', template_folder=str(PROJECT / 'templates'))
        with patch.dict(os.environ, {'MAGSIM_CPU_ONLY': '1', 'MAGSIM_AUTO_RESUME_QUEUE': '0'}), \
                patch('modules.workbench_routes.OptimizationPlans.start_monitor'):
            app.register_blueprint(create_workbench(self.project, self.root / 'records', self.root))
        self.client = app.test_client()

    def test_reader_formats_existing_manual_and_resolves_linked_instructions(self):
        response = self.client.get('/system/guide')
        self.assertEqual(response.status_code, 200)
        self.assertIn('<h1>操作说明</h1>', response.text)
        self.assertIn('<table>', response.text)
        self.assertIn('href="/system/guide/custom_material_full_chain_20261020.md"', response.text)
        self.assertIn('/system/guide/system_user_guide.md/download', response.text)
        self.assertNotIn('<script>', response.text)
        self.assertIn('&lt;script&gt;', response.text)

    def test_reader_and_download_work_without_resources_or_writing_records(self):
        before = {p.relative_to(self.root).as_posix(): p.read_bytes()
                  for p in self.root.rglob('*') if p.is_file()}
        self.assertEqual(self.client.get('/system/guide/system_user_guide.md').status_code, 200)
        response = self.client.get('/system/guide/system_user_guide.md/download')
        self.addCleanup(response.close)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data, self.source.read_bytes())
        self.assertIn('attachment;', response.headers['Content-Disposition'])
        self.assertIn('text/markdown', response.content_type)
        after = {p.relative_to(self.root).as_posix(): p.read_bytes()
                 for p in self.root.rglob('*') if p.is_file()}
        self.assertEqual(before, after)
        self.assertFalse(any(p.is_file() for p in (self.root / 'records').rglob('*')))

    def test_unknown_documents_are_not_served_from_program_or_private_root(self):
        (self.docs / 'private.md').write_text('private content', encoding='utf8')
        for suffix in ('private.md', 'app.py', 'unknown.md'):
            for path in (f'/system/guide/{suffix}', f'/system/guide/{suffix}/download'):
                response = self.client.get(path)
                self.assertEqual(response.status_code, 400)
                self.assertNotIn('private content', response.text)

    def test_missing_included_document_has_actionable_error(self):
        for path in ('/system/guide/batch_result_views_20261020.md',
                     '/system/guide/batch_result_views_20261020.md/download'):
            response = self.client.get(path)
            self.assertEqual(response.status_code, 400)
            self.assertIn('缺少系统操作说明', response.json['error'])

    def test_homepage_links_to_the_in_app_guide(self):
        response = self.client.get('/system')
        self.assertEqual(response.status_code, 200)
        self.assertIn('href="/system/guide"', response.text)
        self.assertIn('使用说明与完整流程', response.text)


if __name__ == '__main__':
    unittest.main()
