"""Append-only task identity, resume segments and bounded delegation."""
import json


class TaskRegistry:
    def __init__(self, store):
        self.store = store

    @staticmethod
    def _runs(db):
        runs = {}
        for (encoded,) in db.execute("SELECT body FROM events WHERE json_extract(body,'$.registry_event') IN ('RUN_CREATED','RUN_RESUMED') ORDER BY seq"):
            body = json.loads(encoded)
            if body.get('registry_event') == 'RUN_CREATED':
                runs[body['run_id']] = dict(body)
            elif body.get('registry_event') == 'RUN_RESUMED':
                runs[body['run_id']]['segment'] = body['segment']
        return runs

    def create(self, event_id, task_id, run_id, parent_run_id=None):
        if any(not isinstance(v, str) or not v.strip() for v in (task_id, run_id)):
            raise ValueError('task_id and run_id required')
        body = dict(registry_event='RUN_CREATED', task_id=task_id,
                    run_id=run_id, parent_run_id=parent_run_id, segment=1)
        def validate(db, candidate):
            runs = self._runs(db)
            if run_id in runs:
                raise ValueError('run already exists')
            parent = parent_run_id
            depth = 0
            while parent is not None:
                if parent not in runs:
                    raise ValueError('parent run missing')
                depth += 1
                if depth > 3:
                    raise ValueError('delegation exceeds depth 3')
                parent = runs[parent]['parent_run_id']
        return self.store.append(event_id, body, validate=validate)

    def resume(self, event_id, run_id, expected_segment):
        if type(expected_segment) is not int or expected_segment < 1:
            raise ValueError('positive segment required')
        body = dict(registry_event='RUN_RESUMED', run_id=run_id,
                    segment=expected_segment + 1)
        def validate(db, candidate):
            run = self._runs(db).get(run_id)
            if run is None or run['segment'] != expected_segment:
                raise ValueError('missing run or stale segment')
        return self.store.append(event_id, body, validate=validate)

    def read(self, run_id):
        from contextlib import closing
        with closing(self.store._connect()) as db:
            return self._runs(db).get(run_id)
