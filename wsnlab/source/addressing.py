from source import wsnlab_vis as wsn
from . import config

_NEXT_SHORT_ADDR = 1  # 1,2,3,... (skip 254 root and 255 broadcast)

def next_short_addr():
    """Return the next address as a wsn.Addr(net, short)."""
    global _NEXT_SHORT_ADDR
    while _NEXT_SHORT_ADDR in (config.ROOT_SHORT_ADDR, config.BROADCAST_ADDR):
        _NEXT_SHORT_ADDR += 1
    if _NEXT_SHORT_ADDR > 253:
        raise RuntimeError("Address space exhausted (past 253).")
    addr = wsn.Addr(config.NETWORK_ID, _NEXT_SHORT_ADDR)
    _NEXT_SHORT_ADDR += 1
    return addr