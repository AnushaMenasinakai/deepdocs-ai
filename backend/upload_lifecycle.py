"""Owned parser execution with cooperative abort; no multipart grammar changes.

Only a pending body receive is cancelled. The parser's sequential native spool
awaits are never cancelled by the HTTP waiter. Direct parser-task cancellation
is infrastructure uncertainty: Starlette may already have raced raw closure.
"""
import asyncio
import logging
from concurrent.futures import Future
from types import SimpleNamespace
from starlette.requests import ClientDisconnect
from upload_safety import SaveLifetime, observe_save_result, check_cancellation, UploadUncertain

logger = logging.getLogger(__name__)


class UploadAborted(Exception):
    pass


class OwnedParser:
    def __init__(self, workflow, request, parse, max_bytes):
        self._terminal = Future()
        self._terminal.set_running_or_notify_cancel()
        self.workflow = workflow
        self.request = request
        self.parse = parse
        self.max_bytes = max_bytes
        self.aborted = False
        self.claimed = False
        self.uncertain = False
        self.receiver = None
        self.ready = asyncio.get_running_loop().create_future()
        self.ready.add_done_callback(observe_save_result)
        self.decision = asyncio.Event()
        workflow.own_cleanup(self)
        self.task = asyncio.create_task(self._run())
        self.task.add_done_callback(self._task_done)

    def _task_done(self, task):
        observe_save_result(task)
        if task.cancelled():
            self.uncertain = True
            logger.warning('Upload parser completion is unconfirmed; service review required.')
            if not self.ready.done():
                self.ready.set_exception(UploadUncertain())

    def close(self):
        if self._terminal.done():
            return
        self.aborted = True
        self.decision.set()
        if self.receiver is not None and not self.receiver.done():
            self.receiver.cancel()

    async def stream(self):
        iterator = self.request.stream().__aiter__()
        try:
            while True:
                if self.aborted:
                    raise UploadAborted()
                self.receiver = asyncio.create_task(iterator.__anext__())
                try:
                    chunk = await self.receiver
                except StopAsyncIteration:
                    return
                except asyncio.CancelledError:
                    if self.aborted and not asyncio.current_task().cancelling():
                        raise UploadAborted() from None
                    raise
                finally:
                    self.receiver = None
                if self.aborted:
                    raise UploadAborted()
                yield chunk
        finally:
            # Only the body receiver runs here, never a spool write or seek.
            if self.receiver is not None:
                self.receiver.cancel()
                await asyncio.gather(self.receiver, return_exceptions=True)
                self.receiver = None
            await iterator.aclose()

    async def _run(self):
        form = None
        try:
            form = await self.parse(SimpleNamespace(headers=self.request.headers, stream=self.stream), self.max_bytes)
            if not self.ready.done():
                self.ready.set_result(form)
            await self.decision.wait()
        except asyncio.CancelledError:
            self.uncertain = True
            logger.warning('Upload parser completion is unconfirmed; service review required.')
        except BaseException as error:
            # Adapter cleanup uncertainty is not genuine finalization.
            if getattr(error, 'upload_cleanup_uncertain', False):
                self.uncertain = True
                logger.warning('Upload cleanup completion is unconfirmed; service review required.')
            if not self.ready.done():
                self.ready.set_exception(error)
        finally:
            if not self.uncertain and form is not None and not self.claimed:
                try:
                    await form.close()
                except BaseException:
                    self.uncertain = True
                    logger.warning('Upload stream completion is unconfirmed; service review required.')
            if not self.ready.done():
                self.ready.set_exception(UploadUncertain() if self.uncertain else UploadAborted())
            if not self.uncertain:
                self._terminal.set_result(None)

    async def result(self):
        try:
            form = await asyncio.shield(self.ready)
            check_cancellation()
            if self.aborted:
                raise UploadAborted()
            # Attach stream ownership before acknowledging parser handoff.
            lifetime = SaveLifetime(form['file'])
            self.workflow.own_cleanup(lifetime)
            lifetime.owned = True
            form['file']._deepdocs_save_lifetime = lifetime
            self.claimed = True
            self.decision.set()
            return form
        except asyncio.CancelledError:
            self.close()
            raise
        except (UploadAborted, ClientDisconnect):
            self.close()
            raise
