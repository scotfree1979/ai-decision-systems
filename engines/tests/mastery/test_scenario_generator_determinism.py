from engines.mastery.scenario_gen import expand_script

SCRIPT = {
    "segments":[
        {"type":"drift","ticks":6,"slope":0.8,"sigma":0.3,"depth":{"levels":[500,400,300]}},
        {"type":"pullback","ticks":-2,"slope":-0.9,"sigma":0.4,"depth":{"levels":[450,380,280]}},
    ]
}

def test_determinism_same_seed():
    p1 = expand_script(SCRIPT, seed=123)
    p2 = expand_script(SCRIPT, seed=123)
    assert p1 == p2
