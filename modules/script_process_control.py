"""Control only the process object created for this live script job and its children."""
import os
import threading
import time
import psutil


MAX_MEMBERS=256


class ScriptProcessTree:
    def __init__(self,process):
        if not isinstance(process.pid,int) or isinstance(process.pid,bool) or process.pid==os.getpid():
            raise ValueError('脚本进程身份无效')
        self.process=process;self.root=psutil.Process(process.pid)
        self.root_key=(self.root.pid,self.root.create_time())
        if process.poll() is not None:raise psutil.NoSuchProcess(process.pid)
        self.members={self.root_key:self.root};self.lock=threading.RLock();self.last_observed=0

    def identity(self):
        return dict(pid=self.root_key[0],created=self.root_key[1],scope='live script process and observed descendants')

    @staticmethod
    def _alive(process,key):
        try:
            return process.is_running() and process.create_time()==key[1] and process.status() not in (psutil.STATUS_ZOMBIE,psutil.STATUS_DEAD)
        except psutil.NoSuchProcess:return False

    def _remember(self,process):
        key=(process.pid,process.create_time())
        if process.pid==os.getpid():raise ValueError('不能将Web服务自身加入脚本进程树')
        if key not in self.members and len(self.members)>=MAX_MEMBERS:self._prune()
        if key not in self.members and len(self.members)>=MAX_MEMBERS:raise ValueError('脚本进程树超过256项，未扩大停止范围')
        self.members[key]=process
        return key

    def _prune(self):
        for key,process in list(self.members.items()):
            try:
                if key!=self.root_key and not self._alive(process,key):self.members.pop(key,None)
            except psutil.Error:pass

    def observe(self):
        with self.lock:
            now=time.monotonic()
            if now-self.last_observed<.5:return
            self.last_observed=now
            self._prune()
            if not self._alive(self.root,self.root_key):return
            for process in self.root.children(recursive=True):
                try:self._remember(process)
                except psutil.NoSuchProcess:pass

    def remaining(self):
        with self.lock:
            return [dict(pid=key[0],created=key[1]) for key,process in self.members.items() if self._alive(process,key)]

    def stop(self,timeout=3):
        """Freeze parents before discovery, then stop the verified tree and confirm exit."""
        with self.lock:
            frozen=[];resumable=[];seen=set();errors=[];started=False
            self._prune()
            root_alive=self._alive(self.root,self.root_key)
            def freeze(process,key):
                if key in seen or not self._alive(process,key):return
                seen.add(key)
                if process.status()!=psutil.STATUS_STOPPED:
                    process.suspend();resumable.append((process,key))
                frozen.append((process,key))
                for child in process.children():
                    try:child_key=self._remember(child);freeze(child,child_key)
                    except psutil.NoSuchProcess:pass
            try:
                for key,process in list(self.members.items()):freeze(process,key)
                for process,key in frozen:
                    try:
                        if self._alive(process,key):process.kill();started=True
                    except psutil.NoSuchProcess:pass
                    except psutil.Error as error:errors.append(str(error))
                psutil.wait_procs([process for process,key in frozen],timeout=timeout)
            except (psutil.Error,ValueError,OSError) as error:errors.append(str(error))
            finally:
                for process,key in reversed(resumable):
                    try:
                        if self._alive(process,key):process.resume()
                    except psutil.NoSuchProcess:pass
                    except psutil.Error as error:errors.append('恢复失败：'+str(error))
            try:remaining=self.remaining()
            except psutil.Error as error:
                errors.append(str(error));remaining=[dict(pid=key[0],created=key[1]) for key in self.members]
            return dict(confirmed=not remaining and not errors,action_started=started,
                        root_alive_at_request=root_alive,targets=[dict(pid=key[0],created=key[1]) for process,key in frozen],
                        remaining=remaining,errors=errors[:20])
