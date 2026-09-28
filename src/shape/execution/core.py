from concurrent.futures import ThreadPoolExecutor, as_completed


class ExecutionPlan:
    def __init__(self, workers=4):
        self.workers = workers

    def map(self, fn, partitions):
        with ThreadPoolExecutor(max_workers=self.workers) as ex:
            return [f.result() for f in as_completed([ex.submit(fn, p) for p in partitions])]
