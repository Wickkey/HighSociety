import logging
import multiprocessing
import os
import sys
import threading
import traceback
from logging.handlers import RotatingFileHandler
import errno


class ConcurrentLogHandler(logging.Handler):
    """multiprocessing log handler

    This handler makes it possible for several processes
    to log to the same file by using a queue.
    """

    def __init__(self, path: str, max_bytes: int, backup_count: int):

        logging.Handler.__init__(self)
        self._handler = self.create_rotating_file_handler(path=path, max_bytes=max_bytes, backup_count=backup_count)
        self.queue = multiprocessing.Queue(-1) # a queue of infinite size that can be shared between processes
        self._emit_event = threading.Event()

        thrd = threading.Thread(target=self.receive, daemon=True)
        thrd.start()

    def setFormatter(self, fmt):
        logging.Handler.setFormatter(self, fmt)
        self._handler.setFormatter(fmt)

    def receive(self):
        while True:        
        #while not self.queue.empty():
            self._emit_event.wait()
            try:
                record = self.queue.get()
                self._handler.emit(record)
            except (KeyboardInterrupt, SystemExit):
                raise
            except EOFError:
                break
            except:
                # TODO: Exception handling in logger thread
                traceback.print_exc(file=sys.stderr)
        self._emit_event.clear()

    def send(self, s):
        self.queue.put_nowait(s)
        self._emit_event.set()

    @staticmethod
    def create_rotating_file_handler(path: str, max_bytes: int, backup_count: int):
        if not os.path.exists(os.path.dirname(path)):
            try:
                os.makedirs(os.path.dirname(path))
            except OSError as exc:
                if exc.errno != errno.EEXIST:
                    raise

        return RotatingFileHandler(path, maxBytes=max_bytes, backupCount=backup_count)

    @staticmethod
    def _format_record(record):

        # if hasattr(record, 'real_pathname'):
        #     record.filename = record.real_pathname

        # if hasattr(record, 'process_name'):
        #     record.processName = record.process_name

        return record

    def emit(self, record):
        try:
            s = self._format_record(record)
            self.send(s)
        except (KeyboardInterrupt, SystemExit):
            raise
        except:
            self.handleError(record)

    def close(self):
        self._handler.close()
        logging.Handler.close(self)
