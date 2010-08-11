"""This is a special package because it contains (or will contain, as of now)
two parallel implementations of the same code. One implementation, the original,
uses the SWIG Python bindings. That's great, but those leak RAM and have a few
other quirks. The goal is to have this file automatically contain the "best"
available implementation without the user having to configure what is actually
present.
"""

from common import *

try:
    from subvertpy_wrapper import *
except ImportError, e:
    try:
        from svn_swig_wrapper import *
    except ImportError:
        # propagate the subvertpy error; it's easier to install
        import subvertpy_wrapper
