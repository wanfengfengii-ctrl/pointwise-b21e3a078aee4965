"""Run a real legacy archive workflow in a disposable directory."""
import json
import tempfile
from pathlib import Path
from archive import Store
from analysis import Analysis
from variables import Linear, Observation
from results import Results, Failure
from errors import ImpossibleObservation
from samples import build_legacy


def main():
    with tempfile.TemporaryDirectory(prefix="joint-demo-") as directory:
        path = Path(directory) / "archive.sqlite"
        build_legacy(path)
        with Store(path) as store:
            analysis = Analysis(store)
            before = analysis.run("old-task")
            analysis.save("redundant", "joint", 1,
                          [Observation.make("x", {"x": 1}, 3),
                           Observation.make("y", {"y": 1}, 6)],
                          [Linear.make("y", {"y": 1})])
            redundant = analysis.run("redundant")
            analysis.save("contradiction", "joint", 1,
                          [Observation.make("x", {"x": 1}, 3),
                           Observation.make("y", {"y": 1}, 7)],
                          [Linear.make("y", {"y": 1})])
            try:
                analysis.run("contradiction")
            except ImpossibleObservation:
                assert isinstance(Results(store).get("contradiction"), Failure)
            analysis.save("latest", "joint", store.head("joint"), [],
                          [Linear.make("y", {"y": 1})])
            latest = analysis.run("latest")
            assert (before.means, before.covariance) == (redundant.means, redundant.covariance)
            assert latest.means == (10,)
        with Store(path) as reopened:
            old = Analysis(reopened).run("old-task")
            assert old == before
            assert reopened.put_model("seed-0", reopened.get_model("joint", 1)) == ("joint", 1)
            print(json.dumps({"old_y": str(old.means[0]),
                              "old_variance": str(old.covariance[0][0]),
                              "redundant_y": str(redundant.means[0]),
                              "contradiction": Results(reopened).get("contradiction").error,
                              "latest_y": str(latest.means[0]),
                              "old_revision": old.revision, "head": reopened.head("joint"),
                              "reopen_equal": old == before}, sort_keys=True))


if __name__ == "__main__":
    main()
