"""Owned process-tree stop tests: no real signals or foreign-process discovery."""
import os
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import Mock,patch
from types import SimpleNamespace
import psutil
import app as web
from modules.script_process_control import ScriptProcessTree


class Node:
    def __init__(self,pid,events,children=()):
        self.pid=pid;self.events=events;self.desc=list(children);self.created=1000+pid
        self.alive=True;self.frozen=False;self.deny_children=False;self.deny_kill=False
    def create_time(self):return self.created
    def is_running(self):return self.alive
    def status(self):return psutil.STATUS_STOPPED if self.frozen else psutil.STATUS_RUNNING
    def suspend(self):self.events.append((self.pid,'suspend'));self.frozen=True
    def resume(self):self.events.append((self.pid,'resume'));self.frozen=False
    def kill(self):
        self.events.append((self.pid,'kill'))
        if self.deny_kill:raise psutil.AccessDenied(self.pid)
        self.alive=False
    def children(self,recursive=False):
        self.events.append((self.pid,'children'))
        if self.deny_children:raise psutil.AccessDenied(self.pid)
        return [c for c in self.desc if c.alive]+([g for c in self.desc for g in c.children(True)] if recursive else [])


class ProcessTreeTests(unittest.TestCase):
    def setUp(self):
        self.events=[];self.leaf=Node(13,self.events);self.child=Node(12,self.events,[self.leaf]);self.root=Node(11,self.events,[self.child]);self.foreign=Node(99,self.events)
        patcher=patch('modules.script_process_control.psutil.Process',return_value=self.root);patcher.start();self.addCleanup(patcher.stop)
        wait=patch('modules.script_process_control.psutil.wait_procs',return_value=([],[]));wait.start();self.addCleanup(wait.stop)
        self.control=ScriptProcessTree(SimpleNamespace(pid=11,poll=lambda:None))

    def test_freezes_parents_before_discovery_and_stops_entire_owned_tree(self):
        result=self.control.stop();self.assertTrue(result['confirmed']);self.assertTrue(self.foreign.alive)
        self.assertEqual([v['pid'] for v in result['targets']],[11,12,13])
        for pid in (11,12,13):self.assertLess(self.events.index((pid,'suspend')),self.events.index((pid,'children')))
        self.assertEqual([pid for pid,action in self.events if action=='kill'],[11,12,13])

    def test_reused_pid_is_not_signalled(self):
        self.root.created+=1;result=self.control.stop();self.assertTrue(result['confirmed']);self.assertEqual(self.events,[])

    def test_web_process_cannot_be_registered_as_script(self):
        with self.assertRaises(ValueError):ScriptProcessTree(SimpleNamespace(pid=os.getpid(),poll=lambda:None))

    def test_discovery_denied_restores_only_our_suspension_without_killing(self):
        self.root.deny_children=True;result=self.control.stop();self.assertFalse(result['confirmed']);self.assertFalse(self.root.frozen)
        self.assertNotIn((11,'kill'),self.events);self.assertIn((11,'resume'),self.events)

    def test_previously_suspended_process_is_not_resumed_on_failure(self):
        self.root.frozen=True;self.root.deny_children=True;self.control.stop();self.assertTrue(self.root.frozen);self.assertNotIn((11,'resume'),self.events)

    def test_partial_stop_keeps_remaining_identity_and_allows_exact_retry(self):
        self.leaf.deny_kill=True;result=self.control.stop();self.assertFalse(result['confirmed']);self.assertEqual([r['pid'] for r in result['remaining']],[13]);self.assertFalse(self.leaf.frozen)
        self.leaf.deny_kill=False;self.assertTrue(self.control.stop()['confirmed']);self.assertTrue(self.foreign.alive)

    def test_process_count_limit_rolls_back_before_any_kill(self):
        with patch('modules.script_process_control.MAX_MEMBERS',1):result=self.control.stop()
        self.assertFalse(result['confirmed']);self.assertFalse(self.root.frozen);self.assertFalse(any(action=='kill' for pid,action in self.events))

    def test_live_descendant_is_retained_when_original_parent_exits(self):
        self.control.observe();self.root.alive=False;result=self.control.stop();self.assertTrue(result['confirmed']);self.assertFalse(self.child.alive);self.assertFalse(self.leaf.alive)

    def test_finished_children_do_not_exhaust_limit_in_long_serial_scripts(self):
        self.child.desc=[]
        with patch('modules.script_process_control.MAX_MEMBERS',2),patch('modules.script_process_control.time.monotonic',return_value=100):self.control.observe()
        self.child.alive=False;self.root.desc=[self.leaf]
        with patch('modules.script_process_control.MAX_MEMBERS',2),patch('modules.script_process_control.time.monotonic',return_value=101):self.control.observe()
        self.assertEqual(len(self.control.members),2);self.assertTrue(self.control.stop()['confirmed'])


class StopAPITests(unittest.TestCase):
    original_start=staticmethod(web._start_job)
    def setUp(self):
        tmp=tempfile.TemporaryDirectory();self.addCleanup(tmp.cleanup)
        p=patch.object(web,'_script_journal_for');factory=p.start();self.addCleanup(p.stop)
        from modules.script_job_history import ScriptJobHistory
        factory.return_value=ScriptJobHistory(Path(tmp.name)/'history',web._script_instance)
        for mapping in (web.running_jobs,):
            p=patch.dict(mapping,clear=True);p.start();self.addCleanup(p.stop)
        p=patch.object(web,'job_queue',[]);p.start();self.addCleanup(p.stop)
        self.job=web._make_job('SYNTHETIC_NEVER_EXECUTED.bat',['NEVER']);self.job.update(status='running',process=SimpleNamespace(poll=lambda:None))
        web.running_jobs[self.job['id']]=self.job;self.client=web.app.test_client()

    def test_unverified_live_process_is_not_signalled(self):
        response=self.client.post('/api/jobs/'+self.job['id']+'/stop');self.assertEqual(response.status_code,409);self.assertEqual(response.json['code'],'script_stop_unverified')

    def test_incomplete_tree_blocks_queue_and_cannot_be_cleared(self):
        control=Mock();control.stop.return_value=dict(confirmed=False,root_alive_at_request=True);self.job['_process_tree']=control
        response=self.client.post('/api/jobs/'+self.job['id']+'/stop');self.assertEqual(response.status_code,409);self.assertFalse(self.job['done']);self.assertEqual(self.job['status'],'needs_attention')
        queued=web._make_job('SYNTHETIC_QUEUED.bat',['NEVER']);web.running_jobs[queued['id']]=queued;web.job_queue.append(queued['id'])
        with patch.object(web,'_start_job') as start:web._advance_queue();start.assert_not_called()
        self.assertEqual(self.client.post('/api/jobs/clear-done').json['cleared'],0)

    def test_successful_retry_after_reader_ended_releases_queue(self):
        control=Mock();control.stop.return_value=dict(confirmed=True,root_alive_at_request=False);self.job.update(_process_tree=control,termination_attention=True,stop_requested=True,_reader_finished=True,process=SimpleNamespace(poll=lambda:0))
        with patch.object(web,'_advance_queue') as advance:
            response=self.client.post('/api/jobs/'+self.job['id']+'/stop');self.assertTrue(response.json['success']);advance.assert_called_once()
        self.assertTrue(self.job['done']);self.assertEqual(self.job['status'],'stopped')

    def test_root_gone_with_open_reader_is_not_claimed_stopped(self):
        control=Mock();control.stop.return_value=dict(confirmed=True,root_alive_at_request=False);self.job.update(_process_tree=control,process=SimpleNamespace(poll=lambda:0))
        response=self.client.post('/api/jobs/'+self.job['id']+'/stop');self.assertEqual(response.status_code,409);self.assertFalse(self.job['done']);self.assertTrue(self.job['termination_attention'])

    def test_reader_end_with_remaining_owned_child_keeps_queue_blocked(self):
        control=Mock();control.identity.return_value=dict(pid=12345,created=1);control.remaining.return_value=[dict(pid=54321,created=2)]
        proc=SimpleNamespace(pid=12345,stdout=iter([]),poll=lambda:0,wait=lambda:None,returncode=0)
        queued=web._make_job('SYNTHETIC_QUEUED.bat',['NEVER']);web.running_jobs[queued['id']]=queued;web.job_queue.append(queued['id'])
        with patch.object(web,'ScriptProcessTree',return_value=control),patch.object(web.subprocess,'Popen',return_value=proc),patch.object(web,'_start_job') as start:
            self.original_start(self.job);deadline=time.monotonic()+3
            while not self.job.get('_reader_finished') and time.monotonic()<deadline:time.sleep(.01)
            with self.job['lifecycle_lock']:
                self.assertTrue(self.job.get('_reader_finished'));self.assertEqual(self.job['status'],'needs_attention');self.assertFalse(self.job['done']);start.assert_not_called()

    def test_partial_stop_cannot_race_reader_into_false_terminal_status(self):
        entered=threading.Event();release=threading.Event();control=Mock();control.identity.return_value=dict(pid=12345,created=1);control.remaining.return_value=[]
        def wait():entered.set();release.wait(3)
        def stop():release.set();return dict(confirmed=False,root_alive_at_request=True)
        control.stop.side_effect=stop;proc=SimpleNamespace(pid=12345,stdout=iter([]),poll=lambda:0 if release.is_set() else None,wait=wait,returncode=0)
        with patch.object(web,'ScriptProcessTree',return_value=control),patch.object(web.subprocess,'Popen',return_value=proc):
            self.original_start(self.job);self.assertTrue(entered.wait(3));response=self.client.post('/api/jobs/'+self.job['id']+'/stop');self.assertEqual(response.status_code,409)
            deadline=time.monotonic()+3
            while not self.job.get('_reader_finished') and time.monotonic()<deadline:time.sleep(.01)
        with self.job['lifecycle_lock']:
            saved=web._script_journal_for().read(self.job['id']);self.assertFalse(saved['done']);self.assertEqual(saved['status'],'needs_attention')


if __name__=='__main__':unittest.main()
