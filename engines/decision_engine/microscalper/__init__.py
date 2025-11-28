"""
DecideOnce MicroScalper Router
------------------------------

This package integrates MicroScalper v7 into the DecideOnce framework.
It receives fully enriched context from DecideOnce, transforms it into
MicroScalper-ready context, invokes MSC engines, and emits plan dicts
to the LiveRouter bridge.

This folder contains NO trading logic. All trading logic resides in:

    /engines/micro_scalper_v7/

This folder exists exclusively to wire MicroScalper into the
DecideOnce → Plan → LiveRouter pipeline.
"""
