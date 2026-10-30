import csv
from abc import ABC, abstractmethod


class HRSource(ABC):
    """Common input contract; new HR transports can implement this interface."""

    @abstractmethod
    def records(self):
        """Yield (position, raw record) without materialising the full feed."""


class CsvHRSource(HRSource):
    def __init__(self, stream):
        self.stream = stream

    def records(self):
        yield from enumerate(csv.DictReader(self.stream), 2)


class ApiHRSource(HRSource):
    def __init__(self, records):
        self._records = records

    def records(self):
        yield from enumerate(self._records)
