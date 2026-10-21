"""Task-owned logging remains parallel, keeps exception fields and restores stdout."""
from contextlib import redirect_stdout
import io
import sys
import threading
import unittest
from unittest.mock import patch

import app as web
from modules.task_output import capture_task_stdout


class FinishedRecord(dict):
    def __init__(self, value, event): super().__init__(value); self.event=event
    def update(self,*args,**kwargs):
        super().update(*args,**kwargs)
        if self.get('status') in ('completed','failed'): self.event.set()


class TaskOutputTests(unittest.TestCase):
    def test_nested_capture_returns_to_the_outer_task_and_original_stream(self):
        original=sys.stdout;outer=io.StringIO();inner=io.StringIO()
        with capture_task_stdout(outer):
            print('outer-before')
            with capture_task_stdout(inner): print('inner')
            print('outer-after')
        self.assertEqual(outer.getvalue(),'outer-before\nouter-after\n');self.assertEqual(inner.getvalue(),'inner\n');self.assertIs(sys.stdout,original)

    def test_parallel_captures_and_unrelated_output_do_not_mix(self):
        base=io.StringIO();a=io.StringIO();b=io.StringIO();startedA=threading.Event();startedB=threading.Event();doneA=threading.Event();errors=[]
        def first():
            try:
                with capture_task_stdout(a):
                    print('A-before');startedA.set();self.assertTrue(startedB.wait(4));print('A-after')
            except Exception as error: errors.append(error)
            finally:doneA.set()
        def second():
            try:
                with capture_task_stdout(b):
                    print('B-before');startedB.set();self.assertTrue(doneA.wait(4));print('B-after')
            except Exception as error: errors.append(error)
        with redirect_stdout(base):
            one=threading.Thread(target=first);two=threading.Thread(target=second);one.start();self.assertTrue(startedA.wait(4));print('outside');two.start();one.join(6);two.join(6)
            self.assertFalse(one.is_alive());self.assertFalse(two.is_alive());self.assertIs(sys.stdout,base)
        self.assertFalse(errors);self.assertEqual(a.getvalue(),'A-before\nA-after\n');self.assertEqual(b.getvalue(),'B-before\nB-after\n');self.assertEqual(base.getvalue(),'outside\n')

    def test_exception_restores_the_stream_and_keeps_partial_output(self):
        original=sys.stdout;buffer=io.StringIO()
        with self.assertRaisesRegex(ValueError,'fixture error'):
            with capture_task_stdout(buffer):print('before error');raise ValueError('fixture error')
        self.assertEqual(buffer.getvalue(),'before error\n');self.assertIs(sys.stdout,original)

    def test_stream_attributes_and_headless_fallback_keep_ordinary_print_semantics(self):
        buffer=io.StringIO()
        with capture_task_stdout(buffer):
            self.assertEqual(sys.stdout.write('abc'),3);sys.stdout.flush();self.assertFalse(sys.stdout.isatty());self.assertEqual(sys.stdout.encoding,buffer.encoding)
        self.assertEqual(buffer.getvalue(),'abc')
        with patch.object(sys,'stdout',None):
            with capture_task_stdout(buffer):print('headless')
            self.assertIsNone(sys.stdout)
        self.assertEqual(buffer.getvalue(),'abcheadless\n')

    def test_scope_does_not_clobber_a_foreign_stdout_replacement(self):
        original=sys.stdout;foreign=io.StringIO()
        try:
            with capture_task_stdout(io.StringIO()):sys.stdout=foreign
            self.assertIs(sys.stdout,foreign)
        finally:sys.stdout=original

    def background_pair(self,fail=False):
        startedA=threading.Event();startedB=threading.Event();doneA=threading.Event();doneB=threading.Event();base=io.StringIO();records={}
        def first():
            print('A-before');startedA.set();self.assertTrue(startedB.wait(4));print('A-after')
            if fail:raise ValueError('fixture failure')
            return {'owner':'A'}
        def second():
            print('B-before');startedB.set();self.assertTrue(doneA.wait(4));print('B-after');return {'owner':'B'}
        with patch.object(web,'tasks',records),redirect_stdout(base):
            aid=web.create_task('A');bid=web.create_task('B');records[aid]=FinishedRecord(records[aid],doneA);records[bid]=FinishedRecord(records[bid],doneB)
            web.run_task(aid,first);self.assertTrue(startedA.wait(4));print('outside');web.run_task(bid,second);self.assertTrue(doneA.wait(6));self.assertTrue(doneB.wait(6));self.assertIs(sys.stdout,base)
            with web.app.test_client() as client:a=client.get('/api/tasks/'+aid).json;b=client.get('/api/tasks/'+bid).json;listing=client.get('/api/tasks').json
        self.assertEqual(a['output'],'A-before\nA-after\n');self.assertEqual(b['output'],'B-before\nB-after\n');self.assertEqual(base.getvalue(),'outside\n');self.assertEqual(b['result'],{'owner':'B'});self.assertEqual(b['status'],'completed');self.assertIsNotNone(a['finished']);self.assertEqual(len(listing['tasks']),2)
        return a

    def test_real_run_task_parallel_completion_keeps_status_result_and_output_fields(self):
        a=self.background_pair();self.assertEqual(a['status'],'completed');self.assertEqual(a['result'],{'owner':'A'})

    def test_real_run_task_failure_keeps_traceback_without_capturing_the_other_task(self):
        a=self.background_pair(fail=True);self.assertEqual(a['status'],'failed');self.assertEqual(a['error'],'fixture failure');self.assertIn('ValueError: fixture failure',a['traceback'])

    def test_real_run_task_preserves_args_kwargs_and_running_state(self):
        started=threading.Event();release=threading.Event();done=threading.Event();records={}
        def work(number,*,word):print(word);started.set();self.assertTrue(release.wait(4));return {'number':number}
        with patch.object(web,'tasks',records):
            tid=web.create_task('fixture');records[tid]=FinishedRecord(records[tid],done);web.run_task(tid,work,7,word='owned')
            try:
                self.assertTrue(started.wait(4))
                with web.app.test_client() as client:self.assertEqual(client.get('/api/tasks/'+tid).json['status'],'running')
            finally:release.set()
            self.assertTrue(done.wait(5));self.assertEqual(records[tid]['result'],{'number':7});self.assertEqual(records[tid]['output'],'owned\n')


if __name__=='__main__':unittest.main()
