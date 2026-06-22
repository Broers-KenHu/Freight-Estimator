from __future__ import annotations

from importlib import import_module

from django.core.exceptions import ImproperlyConfigured


ALLOWED_CALCULATOR_PREFIXES = ("freight.calculators.",)


class ChannelRegistry:
    """Lazy calculator loader.

    The quote engine passes only enabled QuoteChannel rows here, so disabled
    channels are neither imported nor executed.
    """

    def load(self, channel):
        key = channel.calculator_key
        if not key.startswith(ALLOWED_CALCULATOR_PREFIXES):
            raise ImproperlyConfigured(f"Calculator path is not allowed: {key}")
        module_path, class_name = key.rsplit(".", 1)
        module = import_module(module_path)
        calculator_cls = getattr(module, class_name)
        return calculator_cls(channel)
