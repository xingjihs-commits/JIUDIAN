import queue, threading
import logging
logger = logging.getLogger(__name__)

class TaskQueue(threading.Thread):
    def __init__(self):
        super().__init__(daemon=True)
        self.q = queue.Queue()
        self.running = True

    def add_task(self, func, *args):
        self.q.put((func, args))

    def request_stop(self):
        self.running = False

    def run(self):
        while self.running:
            try:
                func, args = self.q.get(timeout=1)
                func(*args)
                self.q.task_done()
            except queue.Empty: continue
            except Exception as e:
                logger.warning("[task_queue] unexpected error: %s", e)

task_queue = TaskQueue()