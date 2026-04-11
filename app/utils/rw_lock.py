from contextlib import contextmanager, asynccontextmanager
import threading
from threading import Lock
import random
import time
import asyncio
from collections import Counter
import fcntl

DEBUG = False

def get_worker_id():
    thread_id = ''
    coroutine_id = ''
    try:
        thread_id = threading.get_ident()
    except:
        pass
    try:
        coroutine_id = asyncio.current_task().get_name()
    except:
        pass
    return f'{thread_id}-{coroutine_id}'


class RWLock:
    """ RWLock class; this is meant to allow an object to be read from by
        multiple threads, but only written to by a single thread at a time. See:
        https://en.wikipedia.org/wiki/Readers%E2%80%93writer_lock
        Usage:
            from rwlock import RWLock
            my_obj_rwlock = RWLock()
            # When reading from my_obj:
            with my_obj_rwlock.r_locked():
                do_read_only_things_with(my_obj)
            # When writing to my_obj:
            with my_obj_rwlock.w_locked():
                mutate(my_obj)
    """

    def __init__(self):
        self.w_lock = Lock()
        self.num_r_lock = Lock()
        self.num_r = 0
        self._readers = Counter()
        self._writer = None
        self.num_w = 0

    def r_acquire(self):
        current_id = get_worker_id()
        rr_hold = self._readers[current_id] > 0
        rw_hold = self._writer == current_id

        self.num_r_lock.acquire()
        # Allow read during self write
        if not rw_hold:
            # no need for rw. need otherwise
            if self.num_r == 0:
                self.w_lock.acquire()
        # always need after qualified.
        self.num_r += 1
        if DEBUG:
            print(f'{self.num_r=}')
        self._readers[current_id] += 1
        self.num_r_lock.release()

    def r_release(self):
        current_id = get_worker_id()
        rr_hold = self._readers[current_id] > 1
        rw_hold = self._writer == current_id
        assert self._readers[current_id] >= 1, f'Worker {current_id} releasing read lock without holding it'
        assert self.num_r > 0

        self.num_r_lock.acquire()
        self.num_r -= 1
        if DEBUG:
            print(f'{self.num_r=}')
        self._readers[current_id] -= 1
        if not rw_hold:
            if self.num_r == 0:
                self.w_lock.release()
        self.num_r_lock.release()

    @contextmanager
    def r_locked(self):
        """ This method is designed to be used via the `with` statement. """
        try:
            acquire_success = False
            self.r_acquire()
            acquire_success = True
            yield
        finally:
            if acquire_success:
                self.r_release()

    def w_acquire(self):
        current_id = get_worker_id()
        ww_hold = self._writer == current_id
        wr_hold = self._readers[current_id] > 0
        assert not wr_hold, f'Worker {current_id} acquiring write lock while holding read lock'
        if not ww_hold:
            self.w_lock.acquire()
            self._writer = current_id
        self.num_w += 1
        if DEBUG:
            print(f'{self.num_w=}')

    def w_release(self):
        current_id = get_worker_id()
        assert self._writer == current_id, f'Worker {current_id} releasing write lock without holding it'
        assert self.num_w > 0
        self.num_w -= 1
        if DEBUG:
            print(f'{self.num_w=}')
        if self.num_w == 0:
            self._writer = None
            self.w_lock.release()

    @contextmanager
    def w_locked(self):
        """ This method is designed to be used via the `with` statement. """
        try:
            acquire_success = False
            self.w_acquire()
            acquire_success = True
            yield
        finally:
            if acquire_success:
                self.w_release()



async def test():
    process_lock_cnt = 0
    process_fd = open('process_lock', 'w')

    def read_lock(fn):
        async def wrapped_fn(self, *args, **kwargs):
            nonlocal process_lock_cnt
            if process_lock_cnt == 0:
                fcntl.flock(process_fd, fcntl.LOCK_EX)
            process_lock_cnt += 1
            with self.rwlock.r_locked():
                out = await fn(self, *args, **kwargs)
            process_lock_cnt -= 1
            if process_lock_cnt == 0:
                fcntl.flock(process_fd, fcntl.LOCK_UN)
            return out
        return wrapped_fn

    def write_lock(fn):
        async def wrapped_fn(self, *args, **kwargs):
            nonlocal process_lock_cnt
            if process_lock_cnt == 0:
                fcntl.flock(process_fd, fcntl.LOCK_EX)
            process_lock_cnt += 1
            with self.rwlock.w_locked():
                out = await fn(self, *args, **kwargs)
            process_lock_cnt -= 1
            if process_lock_cnt == 0:
                fcntl.flock(process_fd, fcntl.LOCK_UN)
            return out
        return wrapped_fn
    N = 100
    class A:
        def __init__(self):
            self.a = [0 for _ in range(N)]
            self.rwlock = RWLock()

        @read_lock
        async def get_shuffled_idx(self):
            idxes = list(range(N))
            random.shuffle(idxes)
            return idxes
        
        @read_lock
        async def read(self):
            print(f'Worker {get_worker_id()} reading')
            out = [None for _ in range(N)]
            idxes = await self.get_shuffled_idx()
            for i in idxes:
                out[i] = self.a[i]
            print(out)
            time.sleep(2)

        @write_lock
        async def update(self, a):
            print(f'Worker {get_worker_id()} updating {a}')
            idxes = await self.get_shuffled_idx()
            for i in idxes:
                self.a[i] = a
                time.sleep(random.random() / 100)
            with open(process_fd.name, 'w') as file:
                file.write(str(self.a))
            time.sleep(2)
    a = A()

    async def read():
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, a.read)

    async def update(i):
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, a.update, i)

    async def main():
        tasks = []
        for i in range(1):
            tasks.append(asyncio.create_task(a.update(i)))
            tasks.append(asyncio.create_task(a.read()))
        await asyncio.gather(*tasks)
        await a.read()

    await main()
            
if __name__ == '__main__':
    asyncio.run(test())