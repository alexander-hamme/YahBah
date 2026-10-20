"""Self-tests of the Temporal harness: the real ApplicationWorkflow and worker entrypoint, stub
activities under the real names, real db_ops activities on the test database."""
from temporalio.client import WorkflowExecutionStatus

from harness.temporal import activity_definitions


def test_activities_are_discovered_by_their_real_names() -> None:
    names = set(activity_definitions())
    assert {"discover_activity", "fill_activity", "submit_activity", "acquire_run_slot_activity",
            "update_run_state_activity", "mark_run_completed_activity"} <= names


async def test_a_run_completes_through_the_real_worker_with_stubs(workflows, db) -> None:
    run_id = await workflows.enqueue()
    assert await workflows.wait_closed(run_id) == WorkflowExecutionStatus.COMPLETED
    assert (await db.run(run_id))["status"] == "COMPLETED"
    stubbed = [c.name for c in workflows.stubs.calls]
    assert stubbed[0] == "discover_activity" and "fill_activity" in stubbed
    assert all(c.completed for c in workflows.stubs.calls)


async def test_gates_hold_a_stub_and_the_worker_can_restart(workflows, db) -> None:
    gate = workflows.stubs.gate("fill_activity", attempts={1}, timeout=10)
    run_id = await workflows.enqueue()
    first = await gate.wait_entered()
    assert first.attempt == 1
    await workflows.worker.restart()  # attempt 1 is cancelled with the old worker; Temporal retries
    assert await workflows.wait_closed(run_id) == WorkflowExecutionStatus.COMPLETED
    fills = workflows.stubs.calls_to("fill_activity", run_id)
    assert [c.attempt for c in fills] == [1, 2] and fills[0].cancelled and fills[1].completed


async def test_stub_handlers_override_defaults(workflows, db) -> None:
    from temporalio.exceptions import ApplicationError

    def fail(call):
        raise ApplicationError("form_not_found", type="form_not_found", non_retryable=True)

    workflows.stubs.on("discover_activity", fail)
    run_id = await workflows.enqueue()
    assert await workflows.wait_closed(run_id) == WorkflowExecutionStatus.FAILED
    assert (await db.run(run_id))["status"] == "FAILED"


async def test_activity_env_sets_the_activity_info(activity_env) -> None:
    from temporalio import activity

    @activity.defn
    async def whoami() -> str:
        info = activity.info()
        return f"{info.workflow_id}#{info.attempt}"

    assert await activity_env(workflow_id="application-x", attempt=3).call(whoami) == "application-x#3"
