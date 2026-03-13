from unittest.mock import MagicMock, patch

from lg_orch.meta_graph import (
    build_meta_graph,
    meta_evaluator,
    meta_planner,
    sub_agent_executor,
    task_dispatcher,
)
from lg_orch.state import MetaOrchState, MetaPlanOutput, SubAgentTask


def test_meta_planner():
    state = MetaOrchState(request="Do things", repositories=["repo1", "repo2"])
    res = meta_planner(state)
    assert "meta_plan" in res
    plan: MetaPlanOutput = res["meta_plan"]
    assert plan.global_objective == "Do things"
    assert len(plan.sub_tasks) == 2
    assert plan.sub_tasks[0].repository == "repo1"
    assert plan.sub_tasks[1].repository == "repo2"


def test_task_dispatcher_dependencies():
    plan = MetaPlanOutput(
        global_objective="Test",
        sub_tasks=[
            SubAgentTask(id="t1", repository="repo1", objective="Do 1", dependencies=[]),
            SubAgentTask(id="t2", repository="repo2", objective="Do 2", dependencies=["t1"]),
            SubAgentTask(id="t3", repository="repo3", objective="Do 3", dependencies=["t1", "t2"]),
        ],
        resolution_criteria=[],
    )
    
    # Initial dispatch
    state = MetaOrchState(request="Test", meta_plan=plan, completed_tasks=[])
    res = task_dispatcher(state)
    assert "t1" in res["active_tasks"]
    assert "t2" not in res["active_tasks"]
    
    # Dispatch with t1 complete
    state.completed_tasks = ["t1"]
    res = task_dispatcher(state)
    assert "t2" in res["active_tasks"]
    assert "t3" not in res["active_tasks"]
    
    # Dispatch with t1 and t2 complete
    state.completed_tasks = ["t1", "t2"]
    res = task_dispatcher(state)
    assert "t3" in res["active_tasks"]


@patch("lg_orch.graph.build_graph")
def test_sub_agent_executor(mock_build_graph):
    mock_app = MagicMock()
    # Mock return based on repo to simulate success/failure
    def mock_invoke(state):
        if state["repo_context"]["root"] == "repo1":
            return {"verification": {"ok": True}, "final": "ok1"}
        else:
            return {"verification": {"ok": False}, "final": "err2"}
    mock_app.invoke.side_effect = mock_invoke
    mock_build_graph.return_value = mock_app
    
    plan = MetaPlanOutput(
        global_objective="Test",
        sub_tasks=[
            SubAgentTask(id="t1", repository="repo1", objective="Do 1", dependencies=[]),
            SubAgentTask(id="t2", repository="repo2", objective="Do 2", dependencies=[]),
        ],
        resolution_criteria=[],
    )
    
    state = MetaOrchState(
        request="Test", 
        meta_plan=plan, 
        active_tasks=["t1", "t2"],
        task_results={}
    )
    
    res = sub_agent_executor(state)
    assert "t1" in res["completed_tasks"]
    assert "t2" in res["failed_tasks"]
    assert "t1" not in res["failed_tasks"]
    assert len(res["active_tasks"]) == 0
    
    assert res["task_results"]["t1"].status == "success"
    assert res["task_results"]["t2"].status == "failure"
    assert res["task_results"]["t1"].output == "ok1"


def test_meta_evaluator():
    plan = MetaPlanOutput(
        global_objective="Test",
        sub_tasks=[
            SubAgentTask(id="t1", repository="repo1", objective="Do 1", dependencies=[]),
            SubAgentTask(id="t2", repository="repo2", objective="Do 2", dependencies=[]),
        ],
        resolution_criteria=[],
    )
    
    # Failure condition
    state = MetaOrchState(
        request="Test", 
        meta_plan=plan, 
        completed_tasks=["t1"],
        failed_tasks=["t2"]
    )
    res = meta_evaluator(state)
    assert "error" in res
    
    # Success condition
    state = MetaOrchState(
        request="Test", 
        meta_plan=plan, 
        completed_tasks=["t1", "t2"],
        failed_tasks=[]
    )
    res = meta_evaluator(state)
    assert res["final_report"] == "All tasks completed successfully."


@patch("lg_orch.graph.build_graph")
def test_meta_graph_e2e(mock_build_graph):
    mock_app = MagicMock()
    mock_app.invoke.return_value = {"verification": {"ok": True}, "final": "success"}
    mock_build_graph.return_value = mock_app
    
    graph = build_meta_graph()
    state = {
        "request": "Test overall",
        "repositories": ["repo1", "repo2"]
    }
    
    results = list(graph.stream(state))
    assert len(results) > 0
    
    final_node = results[-1]
    assert "meta_evaluator" in final_node
    final_report = final_node["meta_evaluator"].get("final_report", "")
    assert "All tasks completed successfully" in final_report
