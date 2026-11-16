"""Public conditional-analysis API over fixed SDK1 model revisions."""
from tasks import Tasks
from results import Results, Result, Failure
from errors import ImpossibleObservation


class Analysis:
    def __init__(self, store):
        self.store = store
        self.tasks = Tasks(store)
        self.results = Results(store)

    def save(self, task_id, model_id, revision, observations, contrasts):
        return self.tasks.save(task_id, model_id, revision, observations, contrasts)

    def run(self, task_id, before_publish=None):
        task = self.tasks.get(task_id)
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
