# Joint observation archive

Python 3.11 standard library only. This application registers exact joint
Gaussian observation models, archives immutable revisions, binds named
conditional-analysis tasks to a revision, and publishes a complete result.
SQLite stores rational values as strings. Public numeric results use Fraction.

Run from this project directory:

    python3.11 -B verify.py
    python3.11 -B demo.py

The suite covers the archive, task validation, singular and deterministic
conditioning, joint contrast covariance, publication rollback, frozen old
databases, reordered variables, revision binding and reopen. Tests and demo
create databases only in temporary directories and remove them on exit.
No service, network, package installation or external data is needed.

## Public API

    from archive import Store
    from analysis import Analysis
    from variables import Linear, Observation
    from results import Results

    with Store("observations.sqlite") as store:
        store.put_model("register-1", {
            "id": "station-pair", "revision": 1, "names": ["x", "y"],
            "mean": ["1", "2"], "covariance": [["1", "2"], ["2", "4"]]
        })
        analysis = Analysis(store)
        analysis.save("check-1", "station-pair", 1,
                      [Observation.make("observed-x", {"x": 1}, 3)],
                      [Linear.make("predicted-y", {"y": 1})])
        result = analysis.run("check-1")
        assert result.means[0] == 6
        assert result.covariance[0][0] == 0
        assert Results(store).get("check-1") == result

Use Store() for an in-memory database. Store owns its connection; close it,
or use its context manager. Analysis, Tasks and Results share that Store.
Single worker operation is supported. Do not access one Store concurrently
or nest its transactions. Reopen creates fresh service objects over the
same database. There is no hidden global model cache.

## Models and archive

vendor.sdk1.Model and equivalent dictionaries have id, revision, names,
mean and covariance. A model has 1..8 ordered, unique, nonempty trimmed
variable names. Revision is a positive integer. Mean length and square
covariance dimensions match names. Covariance must be symmetric positive
semidefinite, including singular and zero matrices. Inputs are int, Fraction
or rational strings such as "2/3"; bool and float are rejected.

Store.put_model(request_id, model) atomically writes the revision, current
head and receipt, returning (model_id, revision). The first revision is 1;
new writes must use head+1. Existing revisions are immutable. Replaying a
request with the same canonical model returns its original binding even
after newer revisions exist. A different valid model under the same
request ID raises RequestConflict. InvalidModel rejects malformed models
and revision gaps before any publication. Valid representations of the
same rational values share the same canonical receipt payload.

Store.get_model(id, revision) returns a complete SDK1 Model; head(id),
revisions(id), and receipt(request_id) expose current revision, ordered
history and original write binding. Missing models/revisions raise
UnknownRevision; an unknown receipt returns None. All SQL uses bound values.

archive.py owns models_v1, heads_v1 and receipts_v1. db.py owns meta and
transaction setup. Raw canonical SDK1 JSON remains in models_v1.payload;
the application does not discard original model revisions.

## Tasks and results

Linear.make(name, terms, offset=0) defines offset + sum(coeff*variable).
terms is a mapping or iterable of unique (variable, coefficient) pairs.
Zero coefficients are removed and remaining terms are canonicalized by
variable name. Observation.make(name, terms, value, offset=0) asserts that
expression equals value exactly. Constant and redundant observations are
legal. All expressions bind by name against the fixed model revision,
never by an independently supplied column position.

Analysis.save(task_id, model_id, revision, observations, contrasts) stores
an immutable Task. At most 6 observations and 1..8 ordered contrasts are
supported. Observation names are unique within observations; contrast
names are unique within contrasts. The same name may occur once in each
group. Unknown variables raise UnknownVariable; missing revisions raise
UnknownRevision. Invalid shape/limits raise InvalidTask. Same task ID and
canonical content replays safely, preserving its current status and result;
different content raises TaskConflict. No rejected save creates a task.
Task/identifier names must be nonempty trimmed strings; malformed identifiers
raise ValueError. Tasks(store).get/status expose binding and status.

Analysis.run(task_id) loads the fixed revision and uses SDK1 to compute
exact conditioning. Result has task_id, model_id, revision, labels, means
and full ordered covariance, including cross-contrast entries. Every
numeric result is Fraction. An observation of zero conditional variance
must agree exactly with the deterministic value. Contradictions raise
ImpossibleObservation and atomically publish Failure(task_id,
"ImpossibleObservation"), with no numeric partial result. Compatible
redundant observations do not change the distribution.

Results(store).get(task_id) returns Result or Failure. UnknownTask means
no saved task; NotRun means saved without a committed publication.
tasks_v1 owns fixed bindings, expressions and status (saved, success,
impossible); results_v1 owns the complete publication. A rerun recomputes
the same fixed task and atomically replaces its publication. There is one
current complete publication per immutable task, not an execution log.

Analysis.run(..., before_publish=callable) invokes the callback after
writing result and status but before commit. An exception rolls both back.
The original exception propagates; prior publication, if any, remains.
db.atomic(connection, before_commit=None) also rolls back transactional
DDL and is reusable by maintenance operations; it does not implement
a model transformation or storage upgrade.

## Offline dependency ports and data

vendor/sdk1.py provides the active Model validation, serialization and
conditional(equations, projections) API. Equations are (coefficient row,
rhs), projections are (coefficient row, offset), all in Model.names order.
conditional returns (means, full covariance). Analysis performs name
binding before calling this numeric port.

vendor/sdk2.py is a separate usable factor-storage port. FactorModel has
id, revision, names, hard, rhs, residuals, targets, weights. It represents
H*x=h and energy sum(w_i*(r_i*x-target_i)^2). Density on the affine
support is proportional to exp(-energy/2), relative to Euclidean measure
on that support. Weights must be positive rationals. Hard equations must
be consistent and residual precision positive definite on ker(H).
Redundant hard equations and zero-dimensional support are legal.
No epsilon, approximate inverse or implicit independent-variable fallback
is used. Empty hard/residual matrices use [] and dimensions come from names.

FactorModel validates and serializes; energy(point) returns Fraction or
raises ValueError outside hard support. install(connection) installs its
five v2 tables; append(connection, model) appends a native head+1 revision;
load(connection, id, revision) reads it or raises KeyError. These functions
leave transaction ownership to the caller. Native append does not create
a legacy request receipt. SDK2 has no mean/covariance, conditioning or old
model conversion API. The active application uses only the SDK1 tables.
Port tests exercise SDK2 separately in an in-memory database.

samples.py contains four finite models and build_legacy(path), a standalone
frozen SQL constructor for a fresh v1 archive: two revisions of joint
(with variable order changed), fixed and independent models, four request
receipts, a published old-task and an unrun waiting-task. It does not call
the active Store or Analysis. Existing populated destinations are refused.
demo.py constructs that real archive, executes compatible and contradictory
conditions, checks receipts, and reopens it. Its JSON output is computed
from public API results.

## Regression map

tests/test_archive.py: model roundtrip, PSD rejection and atomicity, request
replay/conflict, revision and numeric validation.
tests/test_tasks.py: name binding, conflict rejection, limits and lifecycle.
tests/test_analysis.py: full covariance, complete correlation, redundant and
contradictory conditions, constants, deterministic support, publication
rollback and an independent x=m+Bz single-observation projection oracle.
tests/test_pipeline.py: disk persistence, reordered new revision, old-task
binding, frozen database and publication failure/reopen/retry.
tests/test_ports.py: exact generic elimination, both dependency ports,
native factor storage and transactional DDL rollback/retry.
