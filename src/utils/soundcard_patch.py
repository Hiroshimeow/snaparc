"""
Monkey patch for soundcard library to fix numpy.fromstring deprecation error.
This must be imported BEFORE soundcard is used.
"""

import numpy as np

# Check if fromstring exists (it was removed in newer numpy)
if hasattr(np, "fromstring"):
    # Save original fromstring
    _original_fromstring = np.fromstring

    # Create patched version that redirects to frombuffer when called without sep
    def _patched_fromstring(string, dtype=float, count=-1, sep=""):
        """
        Patched version that redirects to frombuffer for binary mode.
        """
        # If sep is empty string (binary mode), use frombuffer
        if sep == "":
            return np.frombuffer(string, dtype=dtype, count=count).copy()
        # Otherwise use original fromstring with sep parameter
        return _original_fromstring(string, dtype=dtype, count=count, sep=sep)

    # Apply monkey patch without polluting normal application logs.
    np.fromstring = _patched_fromstring
else:
    # numpy.fromstring doesn't exist, create it as alias to frombuffer
    def _fromstring_compat(string, dtype=float, count=-1, sep=""):
        if sep == "":
            return np.frombuffer(string, dtype=dtype, count=count).copy()
        else:
            raise ValueError(
                "fromstring with sep parameter is not supported in this numpy version"
            )

    np.fromstring = _fromstring_compat
