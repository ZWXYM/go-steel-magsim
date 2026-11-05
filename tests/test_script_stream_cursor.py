"""Snapshot/stream cursors avoid duplicate output and cover the handover gap."""
import json
import unittest
from unittest.mock import patch
import app as web


class ScriptStreamCursorTests(unittest.TestCase):
    def setUp(self):
        self.job=web._make_job('SYNTHETIC_NOT_EXECUTED.bat',['NEVER'])
        self.job.update(id='SYNTHETIC_CURSOR',status='running',done=False,lines=['line_1','line_2','line_3'])
        mapping=patch.dict(web.running_jobs,{self.job['id']:self.job},clear=True);mapping.start();self.addCleanup(mapping.stop)
        self.client=web.app.test_client();self.route='/api/jobs/'+self.job['id']

    def stream(self,suffix='',headers=None):
        self.job.update(status='completed',done=True)
        response=self.client.get(self.route+'/stream'+suffix,headers=headers or {})
        self.assertEqual(response.status_code,200)
        text=response.get_data(as_text=True)
        return [json.loads(line[6:]) for line in text.splitlines() if line.startswith('data: ')],text

    def test_status_cursor_is_end_of_snapshot_not_tail_length(self):
        self.job['lines']=[str(i) for i in range(80)]
        response=self.client.get(self.route+'/status').json
        self.assertEqual(response['log_cursor'],80);self.assertEqual(response['recent_lines'],self.job['lines'][-50:])

    def test_recent_snapshot_then_stream_does_not_repeat_lines(self):
        status=self.client.get(self.route+'/status').json
        events,_=self.stream('?after='+str(status['log_cursor']))
        self.assertEqual([e['line'] for e in events if 'line' in e],[])
        self.assertEqual(events[-1]['cursor'],3);self.assertTrue(events[-1]['done'])

    def test_lines_arriving_between_status_and_stream_are_not_lost(self):
        status=self.client.get(self.route+'/status').json
        self.job['lines'].extend(['gap_4','gap_5'])
        events,text=self.stream('?after='+str(status['log_cursor']))
        self.assertEqual([e['line'] for e in events if 'line' in e],['gap_4','gap_5'])
        self.assertIn('id: 4\n',text);self.assertIn('id: 5\n',text)

    def test_browser_last_event_id_resumes_beyond_original_query(self):
        events,_=self.stream('?after=1',{'Last-Event-ID':'2'})
        self.assertEqual([e['line'] for e in events if 'line' in e],['line_3'])

    def test_older_last_event_id_cannot_rewind_requested_cursor(self):
        events,_=self.stream('?after=2',{'Last-Event-ID':'1'})
        self.assertEqual([e['line'] for e in events if 'line' in e],['line_3'])

    def test_default_stream_remains_compatible_with_existing_clients(self):
        events,_=self.stream()
        self.assertEqual([e['line'] for e in events if 'line' in e],self.job['lines'])

    def test_idle_connection_opens_before_a_new_output_line(self):
        self.job['lines']=[]
        response=self.client.get(self.route+'/stream?after=0',buffered=False)
        self.assertIn(b': stream ready',next(iter(response.response)));response.close()

    def test_quiet_worker_keeps_connection_alive_without_fake_output(self):
        self.job['lines']=[];clock={'time':0,'sleeps':0}
        def advance(seconds):
            clock['time']+=16;clock['sleeps']+=1
            if clock['sleeps']==2:self.job.update(done=True,status='completed')
        with patch.object(web.time,'monotonic',side_effect=lambda:clock['time']),patch.object(web.time,'sleep',side_effect=advance):
            response=self.client.get(self.route+'/stream?after=0');text=response.get_data(as_text=True)
        self.assertIn(': keep-alive',text);self.assertNotIn('"line":',text);self.assertIn('"done": true',text)

    def test_invalid_or_out_of_range_cursor_is_explicit_and_never_starts_jobs(self):
        with patch.object(web,'_start_job') as start,patch.object(web.subprocess,'Popen') as launch:
            for suffix,headers in [('?after=-1',{}),('?after=',{}),('?after=1.5',{}),('?after=4',{}),('?after='+'9'*17,{}),('',{'Last-Event-ID':'bad'}),('',{'Last-Event-ID':'4'})]:
                with self.subTest(suffix=suffix,headers=headers):
                    response=self.client.get(self.route+'/stream'+suffix,headers=headers)
                    self.assertEqual(response.status_code,400);self.assertEqual(response.json['code'],'invalid_log_cursor')
            start.assert_not_called();launch.assert_not_called()

    def test_job_reference_remains_valid_when_live_card_is_cleared(self):
        self.job.update(status='completed',done=True)
        response=self.client.get(self.route+'/stream?after=1',buffered=False)
        web.running_jobs.clear();text=response.get_data(as_text=True)
        self.assertIn('line_2',text);self.assertIn('line_3',text)


if __name__=='__main__':unittest.main()
