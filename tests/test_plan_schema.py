from engine.planning.plan_schema import Plan, PlanStep

def test_plan_roundtrip():
    plan = Plan.new(prompt="Build a website", project_path="C:/foo")
    step = PlanStep(id=0, description="Create index.html")
    plan.steps.append(step)
    
    d = plan.to_dict()
    assert d["id"] == plan.id
    assert d["prompt"] == "Build a website"
    assert d["steps"][0]["description"] == "Create index.html"
    
    plan2 = Plan.from_dict(d)
    assert plan2.id == plan.id
    assert plan2.prompt == plan.prompt
    assert plan2.project_path == plan.project_path
    assert plan2.steps[0].id == 0
    assert plan2.steps[0].description == "Create index.html"
