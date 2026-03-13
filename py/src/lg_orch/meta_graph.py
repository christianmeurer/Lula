from typing import Any

from langgraph.graph import END, StateGraph  # type: ignore

from lg_orch.state import MetaOrchState, MetaPlanOutput, SubAgentResult, SubAgentTask


def meta_planner(state: MetaOrchState) -> dict[str, Any]:
    # Placeholder or call out to an LLM
    # In reality, this would use InferenceClient to generate a MetaPlanOutput.
    # For now, we expect either a pre-seeded meta_plan or we build a generic one.
    if state.meta_plan:
        return {"meta_plan": state.meta_plan}
    
    # Generic plan generation based on repositories
    tasks = []
    for i, repo in enumerate(state.repositories):
        tasks.append(
            SubAgentTask(
                id=f"task_{i}",
                repository=repo,
                objective=state.request,
                dependencies=[]
            )
        )
    plan = MetaPlanOutput(
        global_objective=state.request,
        sub_tasks=tasks,
        resolution_criteria=["All sub-tasks completed successfully"]
    )
    return {"meta_plan": plan}


def task_dispatcher(state: MetaOrchState) -> dict[str, Any]:
    if not state.meta_plan:
        return {"active_tasks": []}

    completed_ids = set(state.completed_tasks)
    active_ids = set(state.active_tasks)
    failed_ids = set(state.failed_tasks)
    
    new_active = list(state.active_tasks)
    
    for task in state.meta_plan.sub_tasks:
        if task.id in completed_ids or task.id in active_ids or task.id in failed_ids:
            continue
        
        # Check if dependencies are met
        deps_met = all(dep in completed_ids for dep in task.dependencies)
        if deps_met:
            new_active.append(task.id)
            
    return {"active_tasks": new_active}


def sub_agent_executor(state: MetaOrchState) -> dict[str, Any]:
    if not state.meta_plan:
        return {}

    results: dict[str, SubAgentResult] = dict(state.task_results)
    completed = list(state.completed_tasks)
    failed = list(state.failed_tasks)
    
    from lg_orch.graph import build_graph
    
    for task_id in state.active_tasks:
        task = next(t for t in state.meta_plan.sub_tasks if t.id == task_id)
        
        app = build_graph()
        
        sub_state = {
            "request": task.objective,
            "repo_context": {"root": task.repository},
        }
        
        try:
            out = app.invoke(sub_state)
            
            # Simple heuristic for success/failure
            is_success = True
            if isinstance(out, dict):
                verification = out.get("verification")
                if isinstance(verification, dict):
                    if "ok" in verification:
                        is_success = bool(verification["ok"])
                elif hasattr(verification, "ok"):
                    is_success = bool(getattr(verification, "ok"))
            
            status_str = "success" if is_success else "failure"
            
            res = SubAgentResult(
                task_id=task_id,
                status=status_str,
                output=out.get("final", "") if isinstance(out, dict) else str(out)
            )
            results[task_id] = res
            
            if status_str == "success":
                completed.append(task_id)
            else:
                failed.append(task_id)
        except Exception as e:
            res = SubAgentResult(
                task_id=task_id,
                status="failure",
                output=str(e)
            )
            print(f"Exception in sub_agent_executor: {e}")
            results[task_id] = res
            failed.append(task_id)
            
    return {"task_results": results, "completed_tasks": completed, "failed_tasks": failed, "active_tasks": []}


def meta_evaluator(state: MetaOrchState) -> dict[str, Any]:
    if not state.meta_plan:
        return {"final_report": "No plan executed."}
        
    all_tasks = len(state.meta_plan.sub_tasks)
    completed = len(state.completed_tasks)
    failed = len(state.failed_tasks)
    
    if failed > 0:
        return {"error": f"{failed} tasks failed.", "final_report": "Execution failed."}
        
    if completed == all_tasks:
        return {"final_report": "All tasks completed successfully."}
        
    return {}


def route_evaluator(state: MetaOrchState) -> str:
    if not state.meta_plan:
        return "end"
        
    all_tasks = len(state.meta_plan.sub_tasks)
    completed = len(state.completed_tasks)
    failed = len(state.failed_tasks)
    
    if failed > 0:
        return "end"
        
    if completed + failed < all_tasks:
        return "continue"
        
    return "end"


def build_meta_graph():
    workflow = StateGraph(MetaOrchState)
    
    workflow.add_node("meta_planner", meta_planner)
    workflow.add_node("task_dispatcher", task_dispatcher)
    workflow.add_node("sub_agent_executor", sub_agent_executor)
    workflow.add_node("meta_evaluator", meta_evaluator)
    
    workflow.set_entry_point("meta_planner")
    workflow.add_edge("meta_planner", "task_dispatcher")
    workflow.add_edge("task_dispatcher", "sub_agent_executor")
    workflow.add_edge("sub_agent_executor", "meta_evaluator")
    
    workflow.add_conditional_edges(
        "meta_evaluator",
        route_evaluator,
        {"continue": "task_dispatcher", "end": END}
    )
    
    return workflow.compile()
