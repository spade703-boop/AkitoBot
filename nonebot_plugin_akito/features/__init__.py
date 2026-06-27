import os

if os.environ.get("AKITO_SKIP_PLUGIN_LOAD") != "1":
    from . import impression
    from . import gallery
    from . import verify
    from . import scheduled
    from . import event_mode
    from . import random_paro
    from . import random_keyword
    from . import gift
    from . import rpg
    try:
        from . import director
    except ImportError:
        pass
