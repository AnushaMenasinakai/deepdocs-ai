"""Version-scoped accounting, not multipart parsing or cancellation ownership.

Only _files_to_close_on_error is private. Never modify its contents. A cancelled
parser is NOT quiescent; its streams must not be finalized by this adapter.
"""
import asyncio
import inspect
from importlib.metadata import version, PackageNotFoundError

from fastapi import HTTPException
from starlette.datastructures import UploadFile
from starlette.formparsers import MultiPartParser


def unavailable(*, uncertain=False):
    error = HTTPException(503, 'Multipart upload cleanup is unavailable.')
    error.upload_cleanup_uncertain = uncertain
    return error


class ParserCleanup:
    def __init__(self, headers, stream, *, parser_factory=MultiPartParser):
        try:
            compatible = version('starlette') == '1.6.0' and version('python-multipart') == '0.0.32'
        except PackageNotFoundError:
            compatible = False
        if not compatible:
            raise unavailable()
        self.parser = parser_factory(headers, stream, max_files=1, max_fields=0)
        registry = getattr(self.parser, '_files_to_close_on_error', None)
        if not inspect.iscoroutinefunction(getattr(self.parser, 'parse', None)) or type(registry) is not list or registry:
            raise unavailable()
        self.state = 'pending'
        self.accounted = False
        self.streams = {}
        self.closures = {}
        self.failure = False

    async def parse(self):
        if self.state != 'pending':
            raise unavailable()
        self.state = 'running'
        try:
            form = await self.parser.parse()
        except BaseException as error:
            task = asyncio.current_task()
            if isinstance(error, asyncio.CancelledError) or (task is not None and task.cancelling()):
                self.state = 'cancelled'
                raise
            self.state = 'exception'
            # Starlette already attempted exception cleanup. An open stream may
            # represent a failed close: never silently retry that operation.
            self.account()
            raise
        self.state = 'normal'
        return form

    def account(self, form=None):
        """Transfer valid returned streams, close orphans after normal return.

        Synchronous close completion is observed directly. No async-wrapper
        completion is accepted as a native completion signal. Repeated calls
        never reattempt a failed close or change a completed handoff.
        """
        if self.state not in ('normal', 'exception'):
            raise unavailable(uncertain=True)
        if self.accounted:
            if self.failure:
                raise unavailable(uncertain=True)
            return
        registry = getattr(self.parser, '_files_to_close_on_error', None)
        if type(registry) is not list:
            raise unavailable(uncertain=True)
        self.streams = {id(stream): stream for stream in tuple(registry)}
        returned = {id(value.file) for _, value in form.multi_items()
                    if isinstance(value, UploadFile)} if form is not None else set()
        invalid = not returned <= self.streams.keys()
        for key, stream in self.streams.items():
            try:
                if stream.closed:
                    self.closures[key] = 'closed'
                    if key in returned:
                        invalid = True
                elif key in returned and not invalid:
                    self.closures[key] = 'pending_transfer'
                elif self.state == 'exception':
                    self.closures[key] = 'unconfirmed'
                else:
                    self.closures[key] = 'closing'
                    stream.close()
                    self.closures[key] = 'closed' if stream.closed else 'unconfirmed'
            except BaseException:
                self.closures[key] = 'failed'
        self.failure = invalid or any(value in ('failed', 'unconfirmed') for value in self.closures.values())
        # Handoff is atomic: if orphan cleanup failed, no successful form is
        # returned. Finalize the otherwise-returned streams as well, once.
        for key, status in tuple(self.closures.items()):
            if status != 'pending_transfer':
                continue
            if not self.failure:
                self.closures[key] = 'transferred'
                continue
            try:
                self.closures[key] = 'closing'
                self.streams[key].close()
                self.closures[key] = 'closed' if self.streams[key].closed else 'unconfirmed'
            except BaseException:
                self.closures[key] = 'failed'
        self.accounted = True
        if self.failure:
            raise unavailable(uncertain=True)
