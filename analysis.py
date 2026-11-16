"""Public conditional-analysis API over fixed model revisions.

On a version '1' store the executor is SDK1. After the upgrade the same
runs are computed from the stored SDK2 factors: exact observations restrict
the hard support and the conditional moments come from the restricted
energy, so fully correlated observations are never treated as independent.
"""
from db import version
from exact import dot, q
from factors import condition
from tasks import Tasks
from results import Results, Result, Failure
from errors import ImpossibleObservation
from vendor import sdk2


class Analysis:
    def __init__(self, store):
        self.store = store
        self.tasks = Tasks(store)
        self.results = Results(store)

    def save(self, task_id, model_id, revision, observations, contrasts):
        return self.tasks.save(task_id, model_id, revision, observations, contrasts)

    def run(self, task_id, before_publish=None):
        task = self.tasks.get(task_id)
        if version(self.store.connection) == "2":
            return self._run_factors(task, before_publish)
        model = self.store.get_model(task.model_id, task.revision)
        equations = [(v.expression.bind(model.names), v.value - v.expression.offset)
                     for v in task.observations]
        projections = [(v.bind(model.names), v.offset) for v in task.contrasts]
        try:
            means, covariance = model.conditional(equations, projections)
        except ImpossibleObservation:
            self.results.publish(task, Failure(task_id), before_publish)
            raise
        result = Result(task_id, task.model_id, task.revision,
                        tuple(v.name for v in task.contrasts), means, covariance)
        self.results.publish(task, result, before_publish)
        return result

    def _run_factors(self, task, before_publish):
        factor = sdk2.load(self.store.connection, task.model_id, task.revision)
        equations = [(v.expression.bind(factor.names), v.value - v.expression.offset)
                     for v in task.observations]
        try:
            mean, covariance = condition(factor, equations)
        except ImpossibleObservation:
            self.results.publish(task, Failure(task.id), before_publish)
            raise
        vectors = [v.bind(factor.names) for v in task.contrasts]
        means = tuple(dot(row, mean) + q(v.offset)
                      for row, v in zip(vectors, task.contrasts))
        covariance = tuple(tuple(dot(a, [dot(c, b) for c in covariance]) for b in vectors)
                           for a in vectors)
        result = Result(task.id, task.model_id, task.revision,
                        tuple(v.name for v in task.contrasts), means, covariance)
        self.results.publish(task, result, before_publish)
        return result
