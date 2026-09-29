"""Fake official-client interface. No network, real model, or Qdrant server."""
from types import SimpleNamespace
from qdrant_client import models


class FakeQdrant:
    def __init__(self):
        self.collections = {}
        self.points = {}
        self.failure = None
        self.fail_upsert = False
        self.fail_delete = False
        self.creates = 0
        self.closed = False

    def check(self):
        if self.failure:
            raise self.failure

    async def get_collections(self):
        self.check()
        return SimpleNamespace(collections=[])

    async def collection_exists(self, name):
        self.check()
        return name in self.collections

    async def create_collection(self, name, vectors_config):
        self.check()
        self.creates += 1
        self.collections[name] = vectors_config
        self.points[name] = {}

    async def get_collection(self, name):
        self.check()
        return SimpleNamespace(config=SimpleNamespace(params=SimpleNamespace(vectors=self.collections[name])))

    async def create_payload_index(self, *args, **kwargs):
        self.check()

    async def upsert(self, name, points, wait):
        self.check()
        assert wait
        for point in points:
            self.points[name][point.id] = point
            if self.fail_upsert:
                raise RuntimeError("private-upsert-credentials")
        return SimpleNamespace(status=models.UpdateStatus.COMPLETED)

    def matches(self, point, query):
        return all(point.payload.get(condition.key) == condition.match.value for condition in query.must)

    async def count(self, name, count_filter, exact):
        self.check()
        assert exact
        return SimpleNamespace(count=sum(self.matches(p, count_filter) for p in self.points[name].values()))

    async def delete(self, name, points_selector, wait):
        self.check()
        if self.fail_delete:
            raise RuntimeError("private-delete-credentials")
        assert wait
        self.points[name] = {key: point for key, point in self.points[name].items()
                             if not self.matches(point, points_selector.filter)}
        return SimpleNamespace(status=models.UpdateStatus.COMPLETED)

    async def close(self):
        self.closed = True
