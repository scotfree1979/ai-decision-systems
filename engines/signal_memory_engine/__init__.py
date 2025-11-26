# engines/signal_memory_engine/__init__.py

# 1) Set up an alias so absolute imports like
#    "from signal_memory_engine.ram_reader import ..." work
#    whether this package is imported as top-level or nested under "engines."
import sys as _sys
if 'engines.signal_memory_engine' in _sys.modules:
    _sys.modules.setdefault(
        'signal_memory_engine',
        _sys.modules['engines.signal_memory_engine']
    )

# 2) Now it's safe to import submodules that use absolute "signal_memory_engine.*"
from .controller import SignalMemoryEngine
from .injection import inject_live_market_data

# 3) Export a shared instance
signal_memory = SignalMemoryEngine()
