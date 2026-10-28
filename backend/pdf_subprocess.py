"""One bounded spawn supervisor per application, with genuine exit ownership.

One non-daemon monitor thread drains bytes while the child runs, then joins and
closes it. No executor pool, pending-job queue, retries, kills or replacements.
An unobservable start failure (or unconfirmed join/cleanup) deliberately leaves
the running Future unresolved and retains handles. Shutdown cannot claim drain.
The owner must not bypass that ownership merely because a request timed out.
"""
from concurrent.futures import Future
import multiprocessing
from queue import Queue
from threading import Lock, Thread, Event
from types import SimpleNamespace

from pdf_worker_child import child_entry, validate_job
from pdf_worker_protocol import Decoder, Limits, MAX_FRAME_BYTES, VERSION


class PDFInfrastructureFailure(RuntimeError):
    def __init__(self):
        super().__init__('PDF worker completion could not be confirmed as successful.')


class PDFSubmissionRejected(RuntimeError):
    """No child was dispatched by this submission."""
    def __init__(self):
        super().__init__('PDF work was not dispatched.')


class PDFSupervisor:
    def __init__(self, *, _context=None, _target=child_entry):
        # Private injection is for deterministic supervisor tests only.
        self._context = _context if _context is not None else multiprocessing.get_context('spawn')
        if self._context.get_start_method() != 'spawn':
            raise PDFSubmissionRejected()
        self._target = _target
        self._lock = Lock()
        self._close_lock = Lock()
        self._active = None
        self._closing = self._closed = False
        self._mailbox = Queue(maxsize=1)
        self._uncertain = None
        self._uncertainty = Event()
        self._thread = Thread(target=self._work, name='deepdocs-pdf-supervisor', daemon=False)
        self._thread.start()

    def submit(self, path, settings):
        try:
            values = validate_job(path, settings)
        except (ValueError, TypeError):
            raise PDFSubmissionRejected() from None
        with self._lock:
            if self._closing or self._active is not None:
                raise PDFSubmissionRejected()
            future = Future()
            # Cancellation must never erase the monitor's process ownership.
            future.set_running_or_notify_cancel()
            self._active = future
            self._mailbox.put_nowait((future, path, values))
            return future

    def _retain(self, process, reader, writer):
        self._uncertain = (process, reader, writer)
        self._uncertainty.set()

    def _supervise(self, path, values):
        reader = writer = process = None
        entered_start = False
        failed = False
        outcome = None
        try:
            reader, writer = self._context.Pipe(duplex=False)
            process = self._context.Process(target=self._target,
                                            args=(writer, path, values, VERSION), daemon=False)
            entered_start = True
            try:
                process.start()
            except BaseException:
                failed = True
                # CPython assigns _popen only AFTER Popen construction returns.
                # On Windows a child can exist before that assignment. Absence
                # of _popen is therefore NOT proof of a failed pre-dispatch.
                if getattr(process, '_popen', None) is None:
                    self._retain(process, reader, writer)
                    return False, None
            writer.close()
            decoder = Decoder(Limits.from_settings(SimpleNamespace(**values)))
            try:
                while True:
                    try:
                        frame = reader.recv_bytes(MAX_FRAME_BYTES)
                    except EOFError:
                        break
                    decoder.feed(frame)  # includes trailing-record rejection
                outcome = decoder.finish()
            except BaseException:
                failed = True
            # On invalid IPC stop reading and close the receiver, unblocking a
            # cooperative sender with broken pipe. Never kill the child. A
            # sender ignoring this and remaining alive keeps ownership forever.
            reader.close()
            process.join()  # monitor thread, NEVER the event loop
            if process.exitcode is None:
                self._retain(process, reader, writer)
                return False, None
            failed = failed or process.exitcode != 0
            process.close()
            return True, PDFInfrastructureFailure() if failed else outcome
        except BaseException:
            if entered_start:
                # An unexpected monitor/cleanup failure cannot prove reaping.
                self._retain(process, reader, writer)
                return False, None
            # Construction failed before start was invoked: no child possible.
            try:
                if reader is not None:
                    reader.close()
                if writer is not None:
                    writer.close()
                if process is not None:
                    process.close()
            except BaseException:
                self._retain(process, reader, writer)
                return False, None
            return True, PDFInfrastructureFailure()

    def _work(self):
        while True:
            job = self._mailbox.get()
            if job is None:
                return
            future, path, values = job
            completed, outcome = self._supervise(path, values)
            if not completed:
                # No new job can be accepted while _active remains unresolved.
                continue
            with self._lock:
                self._active = None
            if isinstance(outcome, PDFInfrastructureFailure):
                future.set_exception(outcome)
            else:
                future.set_result(outcome)

    def close(self):
        """Blocking drain/join; lifespan calls this off its event loop."""
        with self._close_lock:
            with self._lock:
                self._closing = True
                active = self._active
            if self._closed:
                return
            if active is not None:
                try:
                    active.result()  # unresolved ownership intentionally blocks
                except PDFInfrastructureFailure:
                    pass
            self._mailbox.put(None)
            self._thread.join()
            self._closed = True
